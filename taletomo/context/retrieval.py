import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from taletomo.canon.models import CanonFact, Character, Faction, Location, PlotThread, TruthScope, WorldRule
from taletomo.context.budget import BudgetCalculator, TokenBudget
from taletomo.context.models import ContextManifest
from taletomo.generation.models import DraftArtifact
from taletomo.planning.models import Chapter, ChapterPlan, ScenePlan, SeriesBible
from taletomo.taxonomy.models import StyleTerm

# How much of the previous chapter's closing prose to carry into the prompt so
# the new chapter continues voice, scene, and momentum seamlessly.
PREVIOUS_PROSE_TAIL_WORDS = 250

# Candidate caps per category before ranking; the token budget is the real
# inclusion limit. Caps only bound the scoring work per assembly.
CANDIDATE_CAPS = {
    "characters": 60,
    "locations": 30,
    "factions": 30,
    "rules": 30,
    "threads": 30,
    "facts": 80,
}

_STOPWORDS = frozenset(
    """about above after again against being below between doing during each few from
    further have here into just more most other over some such than that their them
    then there these they this through under until very what when where which while
    will with your yours""".split()
)


def _query_terms(text: str) -> set:
    if not text:
        return set()
    return {
        term
        for term in re.findall(r"[a-z0-9']+", text.lower())
        if len(term) >= 4 and term not in _STOPWORDS
    }


def _score_terms(terms: set, *texts: Any) -> int:
    if not terms:
        return 0
    blob = " ".join(str(t).lower() for t in texts if t)
    return sum(1 for term in terms if term in blob)


@dataclass
class ContextPackage:
    system_prompt: str
    user_prompt: str
    manifest: ContextManifest
    total_tokens: int


class ContextAssembler:
    """Assembles a typed, budget-governed, evidence-grounded context package for drafting.

    Entity selection is relevance-ranked: candidates are scored against the
    chapter's contract, title, and recent narrative, then included best-first
    within each category's token budget. Ranking only reorders and
    budget-gates; it never excludes candidates while budget remains, so
    deterministic anti-leakage invariants hold regardless of scores.
    """

    @classmethod
    def assemble_chapter_context(
        cls,
        chapter: Chapter,
        model_context_limit: int = 128000,
        requested_output_tokens: int = 4000,
    ) -> ContextPackage:
        project = chapter.project
        budget = BudgetCalculator.calculate_budget(
            model_context_limit=model_context_limit,
            requested_output_tokens=requested_output_tokens,
        )

        source_entries: List[Dict[str, Any]] = []
        category_spent: Dict[str, int] = {
            "constraints": 0,
            "contract": 0,
            "state": 0,
            "retrieval": 0,
            "recent_context": 0,
        }

        def record_entry(entry_id: str, category: str, content: str, priority: int = 1, score: int = 0) -> Optional[str]:
            tokens = BudgetCalculator.estimate_tokens(content)
            cat_limit = budget.category_budgets.get(category, budget.usable_input)

            if category_spent.get(category, 0) + tokens > cat_limit:
                if category in ("constraints", "contract"):
                    raise ValueError(
                        f"Mandatory category '{category}' ({category_spent.get(category, 0) + tokens} tokens) "
                        f"exceeds category budget ({cat_limit} tokens). Model limit is {model_context_limit}."
                    )
                return None

            category_spent[category] = category_spent.get(category, 0) + tokens
            h = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
            source_entries.append(
                {
                    "id": entry_id,
                    "category": category,
                    "tokens": tokens,
                    "priority": priority,
                    "score": score,
                    "hash": h,
                }
            )
            return content

        # 1. Constraints & Bible
        bible = getattr(project, "bible", None)
        bible_text = ""
        if bible:
            bible_parts = [
                f"Title: {project.title}",
                f"Genre: {project.genre} / {project.subgenre}",
                f"Tone: {project.tone}",
                f"POV: {project.pov}, Tense: {project.tense}",
                f"Prose Style Guide: {bible.prose_style_guide}",
                f"World Setting: {bible.world_setting}",
                f"Magic/Tech Rules: {', '.join(bible.magic_tech_rules) if isinstance(bible.magic_tech_rules, list) else bible.magic_tech_rules}",
            ]
            if project.content_boundaries:
                bible_parts.append(f"Content Boundaries: {project.content_boundaries}")
            full_b_text = "\n".join(bible_parts)
            if record_entry(str(bible.id), "constraints", full_b_text, priority=1):
                bible_text = full_b_text

        # 1b. Style dictionary: resolve the project's chosen genre, subgenre,
        # tone, POV, tense, and pacing to dictionary terms and inject their
        # definitions and examples, so the model shares the author's
        # vocabulary. Non-mandatory: skipped if the constraints budget is full.
        style_terms = StyleTerm.resolve_for_project(project)
        style_text = ""
        if style_terms:
            style_lines = [
                f"- {term.get_field_display()} — {term.name}: {term.definition}"
                + (f" Example: {term.example}" if term.example else "")
                for term in style_terms
            ]
            style_text = (
                "Style Dictionary (the project's chosen style terms, with definitions and examples):\n"
                + "\n".join(style_lines)
            )
            if not record_entry("style-dictionary", "constraints", style_text, priority=1):
                style_text = ""

        # 2. Local Chapter Contract & Scene Plan
        plan = getattr(chapter, "plan", None)
        contract_text = ""
        scene_plans_text = ""
        if plan:
            contract_parts = [
                f"Chapter {chapter.chapter_number} Contract:",
                f"Objectives: {json.dumps(plan.objectives)}",
                f"Required Beats: {json.dumps(plan.required_beats)}",
                f"Prohibited Outcomes: {json.dumps(plan.prohibited_outcomes)}",
                f"Continuity Requirements: {json.dumps(plan.continuity_requirements)}",
                f"Target Words: {plan.target_words} (+/- {plan.tolerance_percent}%)",
            ]
            full_c_text = "\n".join(contract_parts)
            if record_entry(str(plan.id), "contract", full_c_text, priority=1):
                contract_text = full_c_text

            scenes = list(plan.scenes.all())
            if scenes:
                s_lines = [
                    f"Scene {s.scene_order} (~{s.estimated_words} words): {s.objective} (Conflict: {s.conflict})"
                    for s in scenes
                ]
                full_s_text = "\n".join(s_lines)
                if record_entry(f"scenes-{plan.id}", "contract", full_s_text, priority=1):
                    scene_plans_text = full_s_text

        # 3. Relevance signal: what this chapter is about — contract, title,
        # and recent narrative. Entities matching it rank first.
        query_terms: set = _query_terms(chapter.title or "")
        if plan:
            query_terms |= _query_terms(
                " ".join(
                    [
                        *(plan.objectives or []),
                        *(plan.required_beats or []),
                        *(plan.prohibited_outcomes or []),
                        *(plan.continuity_requirements or []),
                        chapter.pov_character_name or "",
                    ]
                )
            )

        recent_entries: List[Tuple[Any, str]] = []
        past_chapters = Chapter.objects.filter(
            project=project,
            chapter_number__lt=chapter.chapter_number,
        ).order_by("-chapter_number")[:3]
        for past_ch in reversed(list(past_chapters)):
            if past_ch.current_summary:
                recent_entries.append((past_ch.id, f"Chapter {past_ch.chapter_number} Summary: {past_ch.current_summary}"))
                # Summaries describe what still binds the next scene.
                query_terms |= _query_terms(past_ch.current_summary)

        # 3b. Previous chapter ending: the closing prose flows straight into
        # the new chapter, so include its tail for voice and momentum.
        previous_tail = ""
        prev_prose_chapter = Chapter.objects.filter(
            project=project,
            chapter_number__lt=chapter.chapter_number,
            active_draft_id__isnull=False,
        ).order_by("-chapter_number").first()
        if prev_prose_chapter:
            prev_draft = DraftArtifact.objects.filter(id=prev_prose_chapter.active_draft_id).first()
            if prev_draft and prev_draft.prose_content.strip():
                tail = " ".join(prev_draft.prose_content.split()[-PREVIOUS_PROSE_TAIL_WORDS:])
                previous_tail = f"Chapter {prev_prose_chapter.chapter_number} ended with: ...{tail}"
                recent_entries.append((f"prev-ending-{prev_prose_chapter.id}", previous_tail))

        def ranked(candidates: List, search_of, name_of) -> List[Tuple[Any, int]]:
            def sort_key(candidate):
                score = _score_terms(query_terms, *search_of(candidate))
                return (-score, str(name_of(candidate)).lower())

            return [(candidate, -sort_key(candidate)[0]) for candidate in sorted(candidates, key=sort_key)]

        # 4. Canonical Story State & Entities (relevance-ranked, budget-gated)
        state_parts = []

        characters = ranked(
            list(Character.objects.filter(project=project)[: CANDIDATE_CAPS["characters"]]),
            lambda c: [c.name, *c.aliases, c.role, c.goals, *c.traits, c.wounds_status, c.internal_need],
            lambda c: c.name,
        )
        for char, score in characters:
            char_desc = f"Character: {char.name} ({char.role}). Status: {'Alive' if char.is_alive else 'Dead'}."
            if char.goals:
                char_desc += f" Goals: {char.goals}."
            if char.wounds_status:
                char_desc += f" Wounds: {char.wounds_status}."
            if char.beliefs:
                char_desc += f" Beliefs: {json.dumps(char.beliefs)}."
            if record_entry(str(char.id), "state", char_desc, priority=2, score=score):
                state_parts.append(char_desc)

        locations = ranked(
            list(Location.objects.filter(project=project)[: CANDIDATE_CAPS["locations"]]),
            lambda loc: [loc.name, loc.description, loc.travel_rules, loc.current_state],
            lambda loc: loc.name,
        )
        for loc, score in locations:
            loc_desc = f"Location: {loc.name}. Description: {loc.description}."
            if loc.travel_rules:
                loc_desc += f" Travel Rules: {loc.travel_rules}."
            if record_entry(str(loc.id), "state", loc_desc, priority=2, score=score):
                state_parts.append(loc_desc)

        factions = ranked(
            list(Faction.objects.filter(project=project)[: CANDIDATE_CAPS["factions"]]),
            lambda fac: [fac.name, fac.goals, fac.resources],
            lambda fac: fac.name,
        )
        for fac, score in factions:
            fac_desc = f"Faction: {fac.name}. Goals: {fac.goals}."
            if fac.resources:
                fac_desc += f" Resources: {fac.resources}."
            if record_entry(str(fac.id), "state", fac_desc, priority=2, score=score):
                state_parts.append(fac_desc)

        rules = ranked(
            list(WorldRule.objects.filter(project=project)[: CANDIDATE_CAPS["rules"]]),
            lambda rule: [rule.title, rule.rule_statement, rule.forbidden_violations, rule.category],
            lambda rule: rule.title,
        )
        for rule, score in rules:
            r_desc = f"Rule [{rule.category}]: {rule.title} - {rule.rule_statement}"
            if rule.forbidden_violations:
                r_desc += f" (Forbidden: {rule.forbidden_violations})"
            if record_entry(str(rule.id), "state", r_desc, priority=1, score=score):
                state_parts.append(r_desc)

        threads = ranked(
            list(
                PlotThread.objects.filter(
                    project=project,
                    setup_chapter__lte=chapter.chapter_number,
                    status__in=[PlotThread.Status.OPEN, PlotThread.Status.PROGRESSING],
                )[: CANDIDATE_CAPS["threads"]]
            ),
            lambda thread: [thread.title, thread.notes, thread.category],
            lambda thread: thread.title,
        )
        for thread, score in threads:
            t_desc = f"Active Thread: {thread.title} ({thread.category}) - Setup Ch {thread.setup_chapter}"
            if record_entry(str(thread.id), "state", t_desc, priority=2, score=score):
                state_parts.append(t_desc)

        state_text = "\n".join(state_parts)

        # 5. Retrieved Older Evidence (ranked, recency-tiebroken, Anti-Leakage filtered)
        fact_candidates: List[Tuple[CanonFact, int, int]] = []
        for fact in CanonFact.objects.filter(
            project=project,
            canonical_status=CanonFact.Status.CONFIRMED,
        )[: CANDIDATE_CAPS["facts"]]:
            # Check provenance against chapter number to prevent future leakage
            provenance_chapter = -1
            match = re.search(r"Chapter\s+(\d+)", fact.provenance, re.IGNORECASE)
            if match:
                provenance_chapter = int(match.group(1))
                if provenance_chapter >= chapter.chapter_number and fact.truth_scope not in (
                    TruthScope.PLAN_ONLY,
                    TruthScope.AUTHOR_NOTE,
                ):
                    continue  # Anti-leakage: exclude future fact!

            score = _score_terms(query_terms, fact.subject, fact.predicate, fact.value)
            fact_candidates.append((fact, score, provenance_chapter))

        # Recent facts first among equals: they are most likely to still bind
        # the current scene.
        fact_candidates.sort(key=lambda item: (-item[1], -item[2]))

        retrieval_parts = []
        for fact, score, _prov_ch in fact_candidates:
            fact_line = f"Canon Fact: {fact.subject} {fact.predicate} '{fact.value}' ({fact.truth_scope})"
            if record_entry(str(fact.id), "retrieval", fact_line, priority=3, score=score):
                retrieval_parts.append(fact_line)

        retrieval_text = "\n".join(retrieval_parts)

        # 6. Recent Summaries (Anti-leakage: strictly chapter_number < current)
        recent_summaries = []
        for past_chapter_id, summ_line in recent_entries:
            if record_entry(str(past_chapter_id), "recent_context", summ_line, priority=2):
                recent_summaries.append(summ_line)

        recent_text = "\n".join(recent_summaries)

        # Build prompts
        system_prompt = (
            "You are an expert novelist and creative writing engine. "
            "Write immersive, high-quality, continuous web-novel prose that strictly follows the local contract, "
            "maintains worldbuilding consistency, and obeys all established rules and character injuries."
        )

        constraints_text = "\n\n".join(part for part in (bible_text, style_text) if part)
        user_prompt_sections = [
            "### SERIES RULES & CONSTRAINTS",
            constraints_text or "(None defined)",
            "\n### CURRENT CHAPTER CONTRACT",
            contract_text or "(No contract specified)",
            "\n### SCENE BREAKDOWN",
            scene_plans_text or "(No scene breakdown)",
            "\n### RECENT NARRATIVE CONTEXT",
            recent_text or "(This is Chapter 1)",
            "\n### PREVIOUS CHAPTER ENDING",
            previous_tail or "(No previous prose — open the chapter with a fresh hook)",
            "\n### CANONICAL STATE & ACTIVE PARTICIPANTS",
            state_text or "(None defined)",
        ]
        if retrieval_text:
            user_prompt_sections.extend(["\n### RETRIEVED CANONICAL EVIDENCE", retrieval_text])

        user_prompt_sections.append(
            "\n### GENERATION INSTRUCTION\n"
            "TASK: DRAFT_CHAPTER\n"
            f"Write the full prose for Chapter {chapter.chapter_number}: '{chapter.title or f'Chapter {chapter.chapter_number}'}'. "
            f"Target length: {plan.target_words if plan else 2200} words. "
            "Fulfill all required beats and do NOT trigger prohibited outcomes. Maintain tense and POV consistency."
        )

        final_prompt = "\n".join(user_prompt_sections)
        template_overhead = BudgetCalculator.estimate_tokens(final_prompt) - sum(e["tokens"] for e in source_entries)
        total_tokens = sum(e["tokens"] for e in source_entries) + BudgetCalculator.estimate_tokens(system_prompt) + max(0, template_overhead)

        # Verify preflight budget
        if total_tokens > budget.usable_input:
            raise ValueError(
                f"Mandatory context ({total_tokens} tokens) exceeds usable input budget ({budget.usable_input} tokens). "
                f"Model limit is {model_context_limit} tokens."
            )

        manifest_raw = json.dumps(source_entries, sort_keys=True)
        manifest_hash = hashlib.sha256(manifest_raw.encode("utf-8")).hexdigest()

        manifest = ContextManifest.objects.create(
            project=project,
            chapter=chapter,
            model_context_limit=model_context_limit,
            requested_output_tokens=requested_output_tokens,
            usable_budget=budget.usable_input,
            category_budgets=budget.category_budgets,
            source_entries=source_entries,
            total_assembled_tokens=total_tokens,
            manifest_hash=manifest_hash,
        )

        return ContextPackage(
            system_prompt=system_prompt,
            user_prompt=final_prompt,
            manifest=manifest,
            total_tokens=total_tokens,
        )
