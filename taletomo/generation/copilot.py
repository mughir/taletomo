import difflib
import html
import logging
import re
from typing import Any, Dict, Optional, Tuple

from django.contrib.auth import get_user_model
from taletomo.planning.models import Chapter, Project
from taletomo.providers.adapters import ProviderGateway

logger = logging.getLogger(__name__)
User = get_user_model()


class ProseCoPilotService:
    """Intelligent in-editor prose co-pilot providing contextual rewrites and instant visual diffs."""

    ACTION_DIRECTIVES = {
        "show_not_tell": (
            "Transform passive exposition, abstract statements, or emotional summaries into visceral physical actions, "
            "body language, involuntary physiological reactions, and subtextual behavior."
        ),
        "sensory_immersion": (
            "Infuse the prose with vivid tactile sensations, acoustic atmosphere, ambient temperatures, smells, "
            "and environmental lighting that firmly ground the reader in the scene's immediate physical reality."
        ),
        "punch_up_dialogue": (
            "Sharpen dialogue cadences: eliminate polite filler, inject distinct character voice and verbal mannerisms, "
            "deepen interpersonal conflict, and enhance subtext beneath spoken words."
        ),
        "intensify_tension": (
            "Accelerate pacing: compress sentence structure, heighten urgency, raise immediate stakes, "
            "and amplify visceral micro-conflict and suspense."
        ),
        "expand": (
            "Flesh out the moment with atmospheric worldbuilding texture, psychological depth, and immersive environmental resonance."
        ),
        "fix_continuity": (
            "Revise the passage to strictly respect canon rules, active character injuries/wounds, and physical scene constraints."
        ),
        "voice_align": (
            "Revise dialogue, verbal mannerisms, and spoken reactions of the target character to strictly reflect their distinct dialogue style, "
            "vocabulary level, cadence, and idioms without altering the narrative outcome."
        ),
        "infill_bridge": (
            "Write a seamless narrative bridge or transition connecting the preceding moment to the succeeding moment, "
            "preserving pacing, sensory atmosphere, and spatial continuity."
        ),
    }

    @staticmethod
    def compute_word_diff(original: str, suggested: str) -> Tuple[str, int, int]:
        """Calculates a clean word-level HTML diff highlighting insertions and deletions."""
        if not original and not suggested:
            return "", 0, 0
        if not original:
            s_escaped = html.escape(suggested)
            return f'<ins class="copilot-diff-ins">{s_escaped}</ins>', len(suggested.split()), 0
        if not suggested:
            o_escaped = html.escape(original)
            return f'<del class="copilot-diff-del">{o_escaped}</del>', 0, len(original.split())

        # Tokenize preserving spaces and punctuation
        orig_tokens = re.findall(r"\S+|\s+", original)
        sugg_tokens = re.findall(r"\S+|\s+", suggested)

        matcher = difflib.SequenceMatcher(None, orig_tokens, sugg_tokens)
        html_chunks = []
        words_added = 0
        words_removed = 0

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                html_chunks.append(html.escape("".join(orig_tokens[i1:i2])))
            elif tag == "delete":
                deleted = "".join(orig_tokens[i1:i2])
                words_removed += len(deleted.split())
                html_chunks.append(f'<del class="copilot-diff-del">{html.escape(deleted)}</del>')
            elif tag == "insert":
                inserted = "".join(sugg_tokens[j1:j2])
                words_added += len(inserted.split())
                html_chunks.append(f'<ins class="copilot-diff-ins">{html.escape(inserted)}</ins>')
            elif tag == "replace":
                deleted = "".join(orig_tokens[i1:i2])
                inserted = "".join(sugg_tokens[j1:j2])
                words_removed += len(deleted.split())
                words_added += len(inserted.split())
                html_chunks.append(f'<del class="copilot-diff-del">{html.escape(deleted)}</del>')
                html_chunks.append(f'<ins class="copilot-diff-ins">{html.escape(inserted)}</ins>')

        diff_html = "".join(html_chunks)
        return diff_html, words_added, words_removed

    @classmethod
    def polish_selection(
        cls,
        user,
        project: Project,
        chapter: Chapter,
        selected_text: str,
        action: str = "show_not_tell",
        context_before: str = "",
        context_after: str = "",
        custom_instruction: str = "",
        target_character_name: Optional[str] = None,
        custom_adapter: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Processes an inline prose selection, produces an AI revision, and returns visual diffs."""
        cleaned_text = selected_text.strip()
        if not cleaned_text:
            raise ValueError("No prose text selected for Co-Pilot.")

        action_key = str(action).strip().lower()
        directive = cls.ACTION_DIRECTIVES.get(action_key, cls.ACTION_DIRECTIVES["show_not_tell"])

        if custom_instruction and custom_instruction.strip():
            editorial_directive = f"{directive} Special author directive: {custom_instruction.strip()}"
        else:
            editorial_directive = directive

        # Gather narrative grounding
        bible = getattr(project, "bible", None)
        style_guide = bible.prose_style_guide if bible else ""
        plan = getattr(chapter, "plan", None)
        pov_char = chapter.pov_character_name or (project.characters.first().name if project.characters.exists() else "Protagonist")

        # Build prompt
        system_prompt = (
            "You are Tomo, an expert novel editor and prose stylist. "
            "Your mission is to polish, sharpen, and transform the author's selected prose according to the editorial directive. "
            "You must seamlessly preserve the established POV, tense, character voice, and narrative continuity. "
            "Output ONLY the final replacement prose text. Do NOT include markdown commentary, quotes around the whole text, or conversational preambles."
        )

        prompt_sections = [
            "TASK: COPILOT",
            f"EDITORIAL_ACTION: {action_key.upper()}",
            f"DIRECTIVE: {editorial_directive}",
            f"STORY CONSTRAINTS: Tone: {project.tone} | POV: {project.pov} | Tense: {project.tense} | POV Character: {pov_char}",
        ]
        if style_guide:
            prompt_sections.append(f"STYLE GUIDE: {style_guide}")
        if plan and plan.objectives:
            prompt_sections.append(f"CHAPTER OBJECTIVES: {', '.join(plan.objectives)}")

        # Inject character voice & speech guidelines
        from taletomo.canon.models import find_project_character

        target_char_obj = find_project_character(project, target_character_name) if target_character_name else None
        if target_char_obj:
            prompt_sections.append(
                f"TARGET CHARACTER VOICE PROFILE (RIGID REQUIREMENT):\n"
                f"- Character: {target_char_obj.name} ({target_char_obj.get_role_display()})\n"
                f"- Distinct Dialogue Style: {target_char_obj.dialogue_style or 'Natural, contextual'}\n"
                f"- Physical Demeanor & Presence: {target_char_obj.appearance or 'Standard'}\n"
                f"- Key Traits: {', '.join(target_char_obj.traits) if target_char_obj.traits else 'N/A'}\n"
                f"MANDATE: Rewrite all spoken dialogue and reactions for {target_char_obj.name} so their distinct cadence, idioms, and voice profile are unmistakable."
            )

        pov_char_obj = find_project_character(project, pov_char) if pov_char else None
        voice_guidelines = []
        if pov_char_obj and pov_char_obj.dialogue_style and pov_char_obj != target_char_obj:
            voice_guidelines.append(f"- {pov_char_obj.name} (POV): {pov_char_obj.dialogue_style}")
        for c in project.characters.filter(dialogue_style__isnull=False).exclude(dialogue_style=""):
            if c != pov_char_obj and c != target_char_obj and any(v.lower() in cleaned_text.lower() for v in c.get_name_variants()):
                voice_guidelines.append(f"- {c.name}: {c.dialogue_style}")
        if voice_guidelines:
            prompt_sections.append("OTHER CHARACTER VOICE GUIDELINES:\n" + "\n".join(voice_guidelines))

        if context_before.strip():
            # Include last ~300 chars of preceding text for seamless syntactic flow
            tail_before = context_before.strip()[-350:]
            prompt_sections.append(f"PRECEDING CONTEXT (for voice/rhythm continuity):\n...{tail_before}")

        prompt_sections.append(f"PASSAGE TO REVISE:\n{cleaned_text}")

        if context_after.strip():
            # Include first ~300 chars of following text for seamless transition
            head_after = context_after.strip()[:350]
            prompt_sections.append(f"SUCCEEDING CONTEXT (for seamless narrative transition):\n{head_after}...")

        prompt_sections.append("REWRITTEN REPLACEMENT PROSE:")
        full_prompt = "\n\n".join(prompt_sections)

        # Resolve model and adapter
        adapter = custom_adapter or ProviderGateway.get_adapter(user=user, project=project)
        model_name = None
        if hasattr(adapter, "config") and adapter.config:
            model_name = adapter.config.get_model_for_task("copilot")

        response = adapter.generate_text(
            prompt=full_prompt,
            system_prompt=system_prompt,
            model=model_name,
            max_tokens=1500,
            temperature=0.7,
        )

        suggested_text = response.content.strip()
        # Strip potential wrapping quotes
        if suggested_text.startswith('"""') and suggested_text.endswith('"""'):
            suggested_text = suggested_text[3:-3].strip()
        elif suggested_text.startswith('"') and suggested_text.endswith('"') and len(suggested_text) > 2:
            suggested_text = suggested_text[1:-1].strip()

        # Compute instant visual diff
        diff_html, words_added, words_removed = cls.compute_word_diff(cleaned_text, suggested_text)
        orig_words = len(cleaned_text.split())
        new_words = len(suggested_text.split())

        explanation_map = {
            "show_not_tell": "Replaced abstract narration with active physical cues, sensory reactions, and subtext.",
            "sensory_immersion": "Enriched ambient acoustic textures, physical sensations, and environmental lighting.",
            "punch_up_dialogue": "Sharpened voice distinctions, tightened speech cadences, and heightened verbal tension.",
            "intensify_tension": "Compressed sentence structure and heightened visceral urgency.",
            "expand": "Expanded worldbuilding textures and character interiority.",
            "fix_continuity": "Harmonized physical actions with canon wounds and world rules.",
            "voice_align": f"Re-voiced dialogue to match {target_char_obj.name if target_char_obj else 'character'}'s distinct dialogue style and verbal cadence.",
            "infill_bridge": "Synthesized a seamless narrative bridge connecting adjacent scenes.",
        }
        explanation = explanation_map.get(action_key, "Applied editorial polish and stylistic refinement.")
        if custom_instruction:
            explanation += f" (Directive: {custom_instruction})"

        return {
            "success": True,
            "action": action_key,
            "original_text": cleaned_text,
            "suggested_text": suggested_text,
            "diff_html": diff_html,
            "words_original": orig_words,
            "words_suggested": new_words,
            "words_added": words_added,
            "words_removed": words_removed,
            "word_count_delta": new_words - orig_words,
            "explanation": explanation,
        }
