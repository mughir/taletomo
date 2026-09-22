import json
import logging
import re
from typing import Any, Dict, List, Optional
from taletomo.canon.models import Character, WorldRule
from taletomo.consistency.models import (
    ContinuityFinding,
    FindingCategory,
    FindingSeverity,
    FindingStatus,
)
from taletomo.planning.models import Chapter, ChapterPlan
from taletomo.providers.adapters import BaseProviderAdapter

logger = logging.getLogger(__name__)


class ContinuityChecker:
    @staticmethod
    def run_deterministic_checks(chapter: Chapter, prose: str, draft_id: Optional[str] = None) -> List[ContinuityFinding]:
        """Runs fast, machine-checkable, deterministic consistency rules against prose."""
        findings = []
        project = chapter.project
        prose_lower = prose.lower()

        # 1. Dead character check
        dead_characters = Character.objects.filter(project=project, is_alive=False)
        for char in dead_characters:
            pattern = rf"\b{re.escape(char.name.lower())}\b"
            if re.search(pattern, prose_lower):
                findings.append(
                    ContinuityFinding(
                        project=project,
                        chapter=chapter,
                        draft_id=draft_id,
                        category=FindingCategory.IDENTITY,
                        severity=FindingSeverity.BLOCKER,
                        confidence=1.0,
                        claim=f"Character '{char.name}' appears active in the prose, but is marked deceased.",
                        conflicting_evidence=[f"Character Record: {char.name} (is_alive=False)"],
                        source_references=[f"Chapter {chapter.chapter_number}"],
                        suggested_action=f"Verify if this is a flashback, mention, or resurrection; otherwise remove '{char.name}' from active action.",
                    )
                )

        # 2. Known injury / impairment check
        injured_characters = Character.objects.filter(
            project=project
        ).exclude(wounds_status="")
        for char in injured_characters:
            wounds = char.wounds_status.lower()
            if "left hand" in wounds or "left arm" in wounds or "broken" in wounds or "shattered" in wounds:
                # Contradiction triggers
                forbidden_actions = [
                    "with both hands",
                    "held the sword in his left hand",
                    "climbed using both hands",
                    "struck with his left fist",
                    "caught the ledge with both hands",
                ]
                for action in forbidden_actions:
                    if action in prose_lower and char.name.lower() in prose_lower:
                        findings.append(
                            ContinuityFinding(
                                project=project,
                                chapter=chapter,
                                draft_id=draft_id,
                                category=FindingCategory.INJURY,
                                severity=FindingSeverity.BLOCKER,
                                confidence=0.95,
                                claim=f"{char.name} performed action '{action}', violating documented physical status.",
                                conflicting_evidence=[f"Wound Record: {char.name} - {char.wounds_status}"],
                                source_references=[f"Chapter {chapter.chapter_number}"],
                                suggested_action=f"Revise prose to respect {char.name}'s injury: {char.wounds_status}.",
                            )
                        )

        # 3. Prohibited outcomes check from Chapter Contract
        plan = getattr(chapter, "plan", None)
        if plan and plan.prohibited_outcomes:
            for prohibited in plan.prohibited_outcomes:
                # Basic check for direct prohibited phrase occurrences
                clean_prob = prohibited.lower().replace("do not ", "").replace("never ", "").strip()
                if clean_prob and len(clean_prob) > 10 and clean_prob in prose_lower:
                    findings.append(
                        ContinuityFinding(
                            project=project,
                            chapter=chapter,
                            draft_id=draft_id,
                            category=FindingCategory.PLOT,
                            severity=FindingSeverity.BLOCKER,
                            confidence=0.8,
                            claim=f"Prose may trigger prohibited outcome: '{prohibited}'",
                            conflicting_evidence=[f"Chapter Contract: Prohibited outcome '{prohibited}'"],
                            source_references=[f"Chapter {chapter.chapter_number} Contract"],
                            suggested_action=f"Review draft to ensure '{prohibited}' was not violated.",
                        )
                    )

        # 4. World Rules forbidden violations
        rules = WorldRule.objects.filter(project=project).exclude(forbidden_violations="")
        for rule in rules:
            clean_violation = rule.forbidden_violations.lower().strip()
            if clean_violation and clean_violation in prose_lower:
                findings.append(
                    ContinuityFinding(
                        project=project,
                        chapter=chapter,
                        draft_id=draft_id,
                        category=FindingCategory.RULE,
                        severity=FindingSeverity.BLOCKER,
                        confidence=0.85,
                        claim=f"Possible violation of world rule: '{rule.title}'",
                        conflicting_evidence=[f"WorldRule #{rule.title}: {rule.rule_statement} (Forbidden: {rule.forbidden_violations})"],
                        source_references=[f"Chapter {chapter.chapter_number}"],
                        suggested_action=f"Revise passage to comply with world rule constraint.",
                    )
                )

        return findings

    @staticmethod
    def run_model_critique(
        adapter: BaseProviderAdapter,
        chapter: Chapter,
        prose: str,
        contract_text: str,
        draft_id: Optional[str] = None,
    ) -> List[ContinuityFinding]:
        """Runs LLM critique against the drafted chapter to identify subtle literary or continuity drift."""
        system_prompt = (
            "You are a strict story editor and continuity auditor. Analyze the following chapter draft "
            "against the provided contract and canonical constraints. Output ONLY a valid JSON list of findings. "
            "Each finding must have: category, severity ('blocker', 'warning', 'advisory'), claim, conflicting_evidence, source_references, suggested_action. "
            "If no issues, return []."
        )
        user_prompt = f"Contract:\n{contract_text}\n\nChapter Draft:\n{prose}\n\nAnalyze continuity and output JSON:"

        try:
            resp = adapter.generate_text(
                prompt=user_prompt,
                system_prompt=system_prompt,
                max_tokens=2000,
                temperature=0.2,
            )
            raw = resp.content.strip()
            # Handle potential markdown code fences
            if raw.startswith("```"):
                raw = re.sub(r"^```[a-z]*\n", "", raw)
                raw = re.sub(r"\n```$", "", raw)

            parsed = json.loads(raw)
            if not isinstance(parsed, list):
                return []

            findings = []
            for item in parsed:
                findings.append(
                    ContinuityFinding(
                        project=chapter.project,
                        chapter=chapter,
                        draft_id=draft_id,
                        category=item.get("category", FindingCategory.PLOT),
                        severity=item.get("severity", FindingSeverity.WARNING),
                        confidence=float(item.get("confidence", 0.8)),
                        claim=item.get("claim", "Potential continuity inconsistency"),
                        conflicting_evidence=item.get("conflicting_evidence", []),
                        source_references=item.get("source_references", []),
                        suggested_action=item.get("suggested_action", ""),
                    )
                )
            return findings
        except Exception as e:
            logger.warning(f"Model critique parsing failed: {e}")
            return []

    @staticmethod
    def check_and_persist(
        chapter: Chapter,
        prose: str,
        draft_id: Optional[str] = None,
        adapter: Optional[BaseProviderAdapter] = None,
    ) -> List[ContinuityFinding]:
        """Runs both deterministic checks and model critique, saving all findings."""
        findings = ContinuityChecker.run_deterministic_checks(chapter, prose, draft_id)
        if adapter:
            plan = getattr(chapter, "plan", None)
            contract_text = json.dumps(plan.objectives if plan else [])
            ai_findings = ContinuityChecker.run_model_critique(adapter, chapter, prose, contract_text, draft_id)
            findings.extend(ai_findings)

        if findings:
            ContinuityFinding.objects.bulk_create(findings)

        return findings
