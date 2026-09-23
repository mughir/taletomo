import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from taletomo.canon.models import CanonFact, Character, Location, PlotThread, TruthScope, WorldRule
from taletomo.context.budget import BudgetCalculator, TokenBudget
from taletomo.context.models import ContextManifest
from taletomo.planning.models import Chapter, ChapterPlan, ScenePlan, SeriesBible


@dataclass
class ContextPackage:
    system_prompt: str
    user_prompt: str
    manifest: ContextManifest
    total_tokens: int


class ContextAssembler:
    """Assembles a typed, budget-governed, evidence-grounded context package for drafting."""

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

        def record_entry(entry_id: str, category: str, content: str, priority: int = 1) -> Optional[str]:
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

            scenes = plan.scenes.all()
            if scenes:
                s_lines = [f"Scene {s.scene_order}: {s.objective} (Conflict: {s.conflict})" for s in scenes]
                full_s_text = "\n".join(s_lines)
                if record_entry(f"scenes-{plan.id}", "contract", full_s_text, priority=1):
                    scene_plans_text = full_s_text

        # 3. Canonical Story State & Entities (1-hop deterministic graph expansion)
        state_parts = []
        characters = Character.objects.filter(project=project)
        for char in characters[:15]:
            char_desc = f"Character: {char.name} ({char.role}). Status: {'Alive' if char.is_alive else 'Dead'}."
            if char.wounds_status:
                char_desc += f" Wounds: {char.wounds_status}."
            if char.beliefs:
                char_desc += f" Beliefs: {json.dumps(char.beliefs)}."
            if record_entry(str(char.id), "state", char_desc, priority=2):
                state_parts.append(char_desc)

        # Active rules
        rules = WorldRule.objects.filter(project=project)
        for rule in rules[:10]:
            r_desc = f"Rule [{rule.category}]: {rule.title} - {rule.rule_statement}"
            if rule.forbidden_violations:
                r_desc += f" (Forbidden: {rule.forbidden_violations})"
            if record_entry(str(rule.id), "state", r_desc, priority=1):
                state_parts.append(r_desc)

        # Active plot threads
        threads = PlotThread.objects.filter(
            project=project,
            status__in=[PlotThread.Status.OPEN, PlotThread.Status.PROGRESSING],
        )
        for thread in threads[:10]:
            t_desc = f"Active Thread: {thread.title} ({thread.category}) - Setup Ch {thread.setup_chapter}"
            if record_entry(str(thread.id), "state", t_desc, priority=2):
                state_parts.append(t_desc)

        state_text = "\n".join(state_parts)

        # 4. Retrieved Older Evidence (Hybrid Retrieval with Anti-Leakage Filter)
        retrieval_parts = []
        confirmed_facts = CanonFact.objects.filter(
            project=project,
            canonical_status=CanonFact.Status.CONFIRMED,
        )
        for fact in confirmed_facts:
            # Check provenance against chapter number to prevent future leakage
            if fact.provenance.startswith("Chapter "):
                try:
                    ch_prov = int(fact.provenance.replace("Chapter ", "").split()[0])
                    if ch_prov >= chapter.chapter_number and fact.truth_scope not in (
                        TruthScope.PLAN_ONLY,
                        TruthScope.AUTHOR_NOTE,
                    ):
                        continue  # Anti-leakage: exclude future fact!
                except Exception:
                    pass

            fact_line = f"Canon Fact: {fact.subject} {fact.predicate} '{fact.value}' ({fact.truth_scope})"
            if record_entry(str(fact.id), "retrieval", fact_line, priority=3):
                retrieval_parts.append(fact_line)

        retrieval_text = "\n".join(retrieval_parts[:30])

        # 5. Recent Summaries (Anti-leakage: strictly chapter_number < current)
        recent_summaries = []
        past_chapters = Chapter.objects.filter(
            project=project,
            chapter_number__lt=chapter.chapter_number,
        ).order_by("-chapter_number")[:3]

        for past_ch in reversed(list(past_chapters)):
            if past_ch.current_summary:
                summ_line = f"Chapter {past_ch.chapter_number} Summary: {past_ch.current_summary}"
                if record_entry(str(past_ch.id), "recent_context", summ_line, priority=2):
                    recent_summaries.append(summ_line)

        recent_text = "\n".join(recent_summaries)

        # Build prompts
        system_prompt = (
            "You are an expert novelist and creative writing engine. "
            "Write immersive, high-quality, continuous web-novel prose that strictly follows the local contract, "
            "maintains worldbuilding consistency, and obeys all established rules and character injuries."
        )

        user_prompt_sections = [
            "### SERIES RULES & CONSTRAINTS",
            bible_text or "(None defined)",
            "\n### CURRENT CHAPTER CONTRACT",
            contract_text or "(No contract specified)",
            "\n### SCENE BREAKDOWN",
            scene_plans_text or "(No scene breakdown)",
            "\n### RECENT NARRATIVE CONTEXT",
            recent_text or "(This is Chapter 1)",
            "\n### CANONICAL STATE & ACTIVE PARTICIPANTS",
            state_text or "(None defined)",
        ]
        if retrieval_text:
            user_prompt_sections.extend(["\n### RETRIEVED CANONICAL EVIDENCE", retrieval_text])

        user_prompt_sections.append(
            "\n### GENERATION INSTRUCTION\n"
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
