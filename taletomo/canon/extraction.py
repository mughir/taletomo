"""Structured canon extraction from approved-candidate drafts.

The extractor asks the provider for a strict JSON payload of events, claims
(subject–predicate–value facts), and plot-thread updates found in the prose,
then persists them as ProposedCanonItem rows. Nothing touches canonical state
here: proposals are inert until the author approves them and commits canon.
"""

import json
import logging
import re
from typing import Any, Dict, List
from django.db import transaction
from taletomo.canon.models import (
    CanonFact,
    PlotThread,
    ProposedCanonItem,
    TruthScope,
    find_project_character,
    parse_alive_value,
)
from taletomo.generation.models import DraftArtifact
from taletomo.planning.models import Chapter
from taletomo.providers.adapters import BaseProviderAdapter

logger = logging.getLogger(__name__)

EXTRACTION_SYSTEM_PROMPT = (
    "You are a precise story-bible archivist. Extract structured canon from chapter prose. "
    "Output ONLY a valid JSON object with keys 'events', 'claims', 'threads_updated', and 'character_updates'. "
    "'events' is a list of {summary, event_type}. "
    "'claims' is a list of {subject, predicate, value, scope, confidence} where scope is one of: "
    "world_truth, narrator_assertion, character_belief, rumor, prophecy. "
    "'threads_updated' is a list of {thread_title, operation, note} where operation is one of: "
    "open, advance, reinforce, close, abandon. "
    "'character_updates' is a list of {name, field, value, note} where field is one of: "
    "wounds_status, is_alive, goals. Only include a character update when the prose explicitly "
    "establishes it (a new wound, a death, a resurrection, a stated goal change); use value "
    "'alive' or 'dead' for is_alive. "
    "Only include what the prose actually establishes. If nothing is found for a key, use an empty list."
)

MAX_PROPOSALS_PER_EXTRACTION = 40

_THREAD_OPERATIONS = {
    "open": "open",
    "reopen": "open",
    "advance": "advance",
    "reinforce": "reinforce",
    "progress": "advance",
    "close": "close",
    "resolve": "close",
    "resolved": "close",
    "closed": "close",
    "abandon": "abandon",
}


def strip_json_fences(raw: str) -> str:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n", "", cleaned)
        cleaned = re.sub(r"\n```$", "", cleaned)
    return cleaned.strip()


def _as_str(val: Any, fallback: str = "") -> str:
    if val is None:
        return fallback
    if isinstance(val, str):
        return val.strip()
    return str(val).strip()


class CanonExtractionError(Exception):
    """Raised when the provider response cannot be parsed into proposals."""


class CanonExtractionService:
    @staticmethod
    def build_extraction_prompt(chapter: Chapter, prose: str) -> str:
        plan = getattr(chapter, "plan", None)
        contract: Dict[str, Any] = {}
        if plan:
            contract = {
                "objectives": plan.objectives,
                "required_beats": plan.required_beats,
                "end_state": plan.end_state,
            }
        open_threads = list(
            PlotThread.objects.filter(
                project=chapter.project_id,
                status__in=[PlotThread.Status.OPEN, PlotThread.Status.PROGRESSING],
            ).values_list("title", flat=True)[:20]
        )
        sections = [
            "TASK: EXTRACT_CANON",
            f"Chapter {chapter.chapter_number}: {chapter.title or 'Untitled'}",
            f"Chapter contract: {json.dumps(contract)}" if contract else "",
            f"Known open plot threads: {json.dumps(open_threads)}" if open_threads else "",
            "Chapter prose:",
            prose,
            "Extract structured canon as JSON:",
        ]
        return "\n".join(section for section in sections if section)

    @staticmethod
    def parse_extraction_response(content: str) -> Dict[str, List[Dict[str, Any]]]:
        raw = strip_json_fences(content)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            raise CanonExtractionError(f"Extraction response is not valid JSON: {e}") from e
        if not isinstance(parsed, dict):
            raise CanonExtractionError("Extraction response must be a JSON object.")

        def _dict_list(val: Any) -> List[Dict[str, Any]]:
            if not isinstance(val, list):
                return []
            return [item for item in val if isinstance(item, dict)]

        return {
            "events": _dict_list(parsed.get("events")),
            "claims": _dict_list(parsed.get("claims")),
            "threads_updated": _dict_list(parsed.get("threads_updated")),
            "character_updates": _dict_list(parsed.get("character_updates")),
        }

    @staticmethod
    def extract_from_draft(
        chapter: Chapter,
        draft: DraftArtifact,
        adapter: BaseProviderAdapter,
    ) -> List[ProposedCanonItem]:
        """Extracts proposals from a draft, replacing stale pending ones for the same draft.

        Reviewed (approved/rejected/consumed) proposals are never touched, and an
        unparseable provider response raises CanonExtractionError without
        modifying existing proposals.
        """
        prompt = CanonExtractionService.build_extraction_prompt(chapter, draft.prose_content)
        resp = adapter.generate_text(
            prompt=prompt,
            system_prompt=EXTRACTION_SYSTEM_PROMPT,
            max_tokens=2000,
            temperature=0.0,
        )
        parsed = CanonExtractionService.parse_extraction_response(resp.content)

        proposals: List[ProposedCanonItem] = []

        def _confidence(val: Any) -> float:
            try:
                conf = float(val)
            except (TypeError, ValueError):
                return 0.8
            return min(1.0, max(0.0, conf))

        # Don't re-propose what canon already confirms.
        confirmed = {
            (subject.lower(), predicate.lower(), value.lower())
            for subject, predicate, value in CanonFact.objects.filter(
                project=chapter.project_id, canonical_status=CanonFact.Status.CONFIRMED
            ).values_list("subject", "predicate", "value")
        }
        seen_claims = set()

        for claim in parsed["claims"]:
            subject = _as_str(claim.get("subject"))
            predicate = _as_str(claim.get("predicate"))
            value = _as_str(claim.get("value"))
            if not subject or not predicate or not value:
                continue
            key = (subject.lower(), predicate.lower(), value.lower())
            if key in confirmed or key in seen_claims:
                continue
            seen_claims.add(key)
            scope = claim.get("scope")
            if scope not in TruthScope.values or scope in (
                TruthScope.PLAN_ONLY,
                TruthScope.AUTHOR_NOTE,
                TruthScope.NONCANONICAL_DRAFT,
            ):
                scope = TruthScope.WORLD_TRUTH
            payload = {
                "subject": subject[:150],
                "predicate": predicate[:100],
                "value": value,
                "scope": scope,
            }
            proposals.append(
                ProposedCanonItem(
                    project=chapter.project,
                    chapter=chapter,
                    draft=draft,
                    kind=ProposedCanonItem.Kind.FACT,
                    payload=payload,
                    summary=f"{subject} — {predicate}: {value}"[:300],
                    confidence=_confidence(claim.get("confidence")),
                    extracted_by_model=resp.model or "",
                )
            )

        for event in parsed["events"]:
            summary = _as_str(event.get("summary"))
            if not summary:
                continue
            payload = {
                "summary": summary,
                "event_type": _as_str(event.get("event_type"), "plot_progress")[:100] or "plot_progress",
            }
            proposals.append(
                ProposedCanonItem(
                    project=chapter.project,
                    chapter=chapter,
                    draft=draft,
                    kind=ProposedCanonItem.Kind.EVENT,
                    payload=payload,
                    summary=summary[:300],
                    confidence=_confidence(event.get("confidence")),
                    extracted_by_model=resp.model or "",
                )
            )

        for thread in parsed["threads_updated"]:
            title = _as_str(thread.get("thread_title")) or _as_str(thread.get("title"))
            if not title:
                continue
            operation = _THREAD_OPERATIONS.get(_as_str(thread.get("operation")).lower(), "advance")
            payload = {
                "thread_title": title[:200],
                "operation": operation,
                "note": _as_str(thread.get("note")),
            }
            proposals.append(
                ProposedCanonItem(
                    project=chapter.project,
                    chapter=chapter,
                    draft=draft,
                    kind=ProposedCanonItem.Kind.THREAD_UPDATE,
                    payload=payload,
                    summary=f"{title}: {operation}"[:300],
                    confidence=_confidence(thread.get("confidence")),
                    extracted_by_model=resp.model or "",
                )
            )

        # Character-state updates anchor prose to the cast records the
        # continuity checker depends on (wounds, life status, goals). Only
        # known cast members are proposed, so extraction cannot invent
        # near-duplicate characters.
        for update in parsed["character_updates"]:
            name = _as_str(update.get("name"))
            field = _as_str(update.get("field")).lower()
            value = _as_str(update.get("value"))
            if not name or not value or field not in ProposedCanonItem.APPLICABLE_CHARACTER_FIELDS:
                continue
            character = find_project_character(chapter.project, name)
            if character is None:
                continue
            if field == "is_alive":
                alive = parse_alive_value(value)
                if alive is None:
                    continue
                value = "alive" if alive else "dead"
            payload = {"name": character.name, "field": field, "value": value}
            note = _as_str(update.get("note"))
            if note:
                payload["note"] = note
            proposals.append(
                ProposedCanonItem(
                    project=chapter.project,
                    chapter=chapter,
                    draft=draft,
                    kind=ProposedCanonItem.Kind.CHARACTER_UPDATE,
                    payload=payload,
                    summary=f"{character.name}: {field.replace('_', ' ')} → {value}"[:300],
                    confidence=_confidence(update.get("confidence", 0.85)),
                    extracted_by_model=resp.model or "",
                )
            )

        proposals = proposals[:MAX_PROPOSALS_PER_EXTRACTION]

        with transaction.atomic():
            # Re-extraction of the same draft replaces only pending proposals;
            # author-reviewed items are preserved as the review trail.
            ProposedCanonItem.objects.filter(
                chapter=chapter, draft=draft, status=ProposedCanonItem.Status.PROPOSED
            ).delete()
            return ProposedCanonItem.objects.bulk_create(proposals)
