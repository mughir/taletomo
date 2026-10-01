import json
import re
from typing import Any, Dict, List, Optional
from django.db import transaction
from taletomo.canon.models import Character, Faction, WorldRule
from taletomo.planning.models import Project
from taletomo.planning.services import PlanningService
from taletomo.providers.adapters import BaseProviderAdapter, ProviderGateway


class WorldbuildingWizardService:
    @staticmethod
    def propose_world_bible(
        premise: str,
        genre: str = "Fantasy",
        tone: str = "Epic",
        adapter: Optional[BaseProviderAdapter] = None,
    ) -> Dict[str, Any]:
        """Proposes structured world bible elements: setting, central conflict, rules, and tone."""
        prompt = (
            f"TASK: WIZARD_PROPOSE_BIBLE\n"
            f"Premise: {premise}\n"
            f"Genre: {genre}\n"
            f"Tone: {tone}\n\n"
            "Generate a JSON object with: world_setting (str), central_conflict (str), reader_promise (str), "
            "hook (str), magic_tech_rules (list of 3 string constraints), and tone_rules (list of 2 string guidelines)."
        )
        if adapter:
            try:
                model = adapter.config.get_model_for_task("planning") if hasattr(adapter, "config") else None
                resp = adapter.generate_text(prompt, model=model)
                match = re.search(r"\{.*\}", resp.content, re.DOTALL)
                if match:
                    return json.loads(match.group(0))
            except Exception:
                pass

        # Deterministic rich fallback
        return {
            "world_setting": f"A richly detailed {genre.lower()} realm shaped by ancient conflicts and shifting power balances.",
            "central_conflict": f"The protagonist must navigate escalating forces threatened by: {premise[:80]}.",
            "reader_promise": "Deep strategic stakes, memorable character growth, and escalating mysteries.",
            "hook": f"When the balance shatters, a lone seeker must uncover the truth behind {premise[:50]}.",
            "magic_tech_rules": [
                "Every exertive power draws upon finite internal or ambient reserves.",
                "Ancient artifacts carry unforeseen side effects and physical backlash.",
                "Power cannot permanently alter mortal destinies without proportional sacrifice.",
            ],
            "tone_rules": [
                f"Maintain the {tone.lower()} atmosphere with grounded emotional realism.",
                "Pacing should balance quiet character moments with high-tension escalations.",
            ],
        }

    @staticmethod
    def propose_factions(
        premise: str,
        genre: str = "Fantasy",
        world_setting: str = "",
        adapter: Optional[BaseProviderAdapter] = None,
    ) -> List[Dict[str, Any]]:
        """Proposes 2-3 conflicting factions with resources, goals, and agendas."""
        prompt = (
            f"TASK: WIZARD_PROPOSE_FACTIONS\n"
            f"Premise: {premise}\n"
            f"Genre: {genre}\n"
            f"World Setting: {world_setting}\n\n"
            "Generate a JSON list of 2-3 faction objects, each with: name (str), goals (str), resources (str), and alliances (list of str)."
        )
        if adapter:
            try:
                model = adapter.config.get_model_for_task("planning") if hasattr(adapter, "config") else None
                resp = adapter.generate_text(prompt, model=model)
                match = re.search(r"\[.*\]", resp.content, re.DOTALL)
                if match:
                    return json.loads(match.group(0))
            except Exception:
                pass

        # Deterministic rich fallback
        return [
            {
                "name": "The High Order",
                "goals": "Preserve centuries of political dominion and monopolize forbidden knowledge.",
                "resources": "Extensive archives, elite enforcers, and deep treasury.",
                "alliances": ["Merchant Guild"],
            },
            {
                "name": "The Ashen Covenant",
                "goals": "Overthrow institutional suppression and awaken dormant ancient powers.",
                "resources": "Underground guerrilla cells, smuggled relics, and zealot scouts.",
                "alliances": [],
            },
            {
                "name": "The Free Marches",
                "goals": "Maintain regional autonomy and protect border trade routes from imperial interference.",
                "resources": "Skilled scouts, fortified outposts, and flexible mercenary contracts.",
                "alliances": [],
            },
        ]

    @staticmethod
    def propose_characters(
        premise: str,
        genre: str = "Fantasy",
        factions: Optional[List[Dict[str, Any]]] = None,
        protagonist_type: str = "Underdog",
        adapter: Optional[BaseProviderAdapter] = None,
    ) -> List[Dict[str, Any]]:
        """Proposes dramatis personae: protagonist, antagonist, and key supporting cast."""
        factions_names = [f["name"] for f in (factions or [])]
        prompt = (
            f"TASK: WIZARD_PROPOSE_CHARACTERS\n"
            f"Premise: {premise}\n"
            f"Genre: {genre}\n"
            f"Protagonist Type: {protagonist_type}\n"
            f"Factions: {', '.join(factions_names)}\n\n"
            "Generate a JSON list of 3-4 character objects with keys: name, role (Protagonist/Antagonist/Supporting), "
            "archetype, psych_flaw, goals, and aliases (list)."
        )
        if adapter:
            try:
                model = adapter.config.get_model_for_task("planning") if hasattr(adapter, "config") else None
                resp = adapter.generate_text(prompt, model=model)
                match = re.search(r"\[.*\]", resp.content, re.DOTALL)
                if match:
                    return json.loads(match.group(0))
            except Exception:
                pass

        # Deterministic rich fallback
        return [
            {
                "name": "Kaelen Vance",
                "role": "Protagonist",
                "archetype": protagonist_type or "Reluctant Hero",
                "psych_flaw": "Hesitates to trust others after a past betrayal.",
                "goals": "Uncover the secret behind his family's disappearance and master his abilities.",
                "aliases": ["The Ghost of Lowtown"],
            },
            {
                "name": "Inquisitor Mara",
                "role": "Antagonist",
                "archetype": "Ideological Enforcer",
                "psych_flaw": "Believes ruthless authoritarian control is the only defense against chaos.",
                "goals": "Eliminate unauthorized power wielders and secure imperial succession.",
                "aliases": ["The Silver Talon"],
            },
            {
                "name": "Orin Sel",
                "role": "Supporting",
                "archetype": "Cynical Mentor",
                "psych_flaw": "Haunted by the collateral damage of past revolutions.",
                "goals": "Guide Kaelen without letting him repeat the bloody mistakes of the past.",
                "aliases": ["Old Fox"],
            },
        ]

    @staticmethod
    def propose_spine_and_arcs(
        premise: str,
        target_chapters: int = 100,
        adapter: Optional[BaseProviderAdapter] = None,
    ) -> Dict[str, Any]:
        """Proposes major series milestones and first volume structure."""
        prompt = (
            f"TASK: WIZARD_PROPOSE_SPINE\n"
            f"Premise: {premise}\n"
            f"Target Chapters: {target_chapters}\n\n"
            "Generate a JSON object with: series_promise (str), ending_direction (str), and major_milestones (list of dicts with chapter and event)."
        )
        if adapter:
            try:
                model = adapter.config.get_model_for_task("planning") if hasattr(adapter, "config") else None
                resp = adapter.generate_text(prompt, model=model)
                match = re.search(r"\{.*\}", resp.content, re.DOTALL)
                if match:
                    return json.loads(match.group(0))
            except Exception:
                pass

        c1 = max(1, target_chapters // 4)
        c2 = max(1, target_chapters // 2)
        c3 = max(1, (target_chapters * 3) // 4)
        return {
            "series_promise": f"An expansive epic spanning {target_chapters} chapters resolving the core conflict of {premise[:60]}.",
            "ending_direction": "A decisive confrontation that fundamentally reshapes the balance of the realm.",
            "major_milestones": [
                {"chapter": c1, "event": "The inciting conspiracy is uncovered; the old life is destroyed."},
                {"chapter": c2, "event": "Point of no return: the protagonist commits to the rebellion."},
                {"chapter": c3, "event": "Dark night of the soul: betrayal and near-fatal setback."},
                {"chapter": target_chapters, "event": "Climactic resolution and establishment of the new era."},
            ],
        }

    @classmethod
    @transaction.atomic
    def finalize_wizard(
        cls,
        user,
        wizard_data: Dict[str, Any],
        adapter: Optional[BaseProviderAdapter] = None,
    ) -> Project:
        """Instantiates and scaffolds a fully populated project from approved wizard data."""
        p_data = wizard_data.get("project", {})
        bible_data = wizard_data.get("bible", {})
        spine_data = wizard_data.get("spine", {})

        project = PlanningService.create_project_with_scaffold(
            owner=user,
            title=p_data.get("title", "Untitled Epic"),
            premise=p_data.get("premise", "An epic tale."),
            target_chapters=int(p_data.get("target_chapters", 50)),
            genre=p_data.get("genre", "Fantasy"),
            subgenre=p_data.get("subgenre", ""),
            audience=p_data.get("audience", "Young Adult / General"),
            tone=p_data.get("tone", "Epic, Mysterious"),
            language=p_data.get("language", "English"),
            pov=p_data.get("pov", "Third Person Limited"),
            tense=p_data.get("tense", "Past Tense"),
            pacing=p_data.get("pacing", "Balanced"),
            protagonist_type=p_data.get("protagonist_type", ""),
            novel_tags=p_data.get("novel_tags", ""),
            target_words_per_chapter=int(p_data.get("target_words_per_chapter", 2200)),
            content_boundaries=p_data.get("content_boundaries", ""),
            bible_data=bible_data,
            spine_data=spine_data,
        )

        # 1. Create Factions
        for fac in wizard_data.get("factions", []):
            Faction.objects.create(
                project=project,
                name=fac.get("name", "Unnamed Faction"),
                goals=fac.get("goals", ""),
                resources=fac.get("resources", ""),
                alliances=fac.get("alliances", []),
            )

        # 2. Create Characters
        from taletomo.canon.models import CharacterRole

        for char in wizard_data.get("characters", []):
            role_map = {
                "protagonist": CharacterRole.PROTAGONIST,
                "antagonist": CharacterRole.ANTAGONIST,
                "ally": CharacterRole.ALLY,
                "supporting": CharacterRole.NEUTRAL,
            }
            raw_role = char.get("role", "supporting").lower()
            role = role_map.get(raw_role, CharacterRole.NEUTRAL)

            archetype = char.get("archetype", "")
            traits = [archetype] if archetype else []

            Character.objects.create(
                project=project,
                name=char.get("name", "Unknown Character"),
                role=role,
                goals=char.get("goals", ""),
                internal_need=char.get("psych_flaw", ""),
                traits=traits,
                aliases=char.get("aliases", []),
                metadata={
                    "archetype": archetype,
                    "psych_flaw": char.get("psych_flaw", ""),
                },
            )


        # 3. Create WorldRules from bible magic/tech rules
        for rule_text in bible_data.get("magic_tech_rules", []):
            if isinstance(rule_text, str) and rule_text.strip():
                WorldRule.objects.create(
                    project=project,
                    category=WorldRule.Category.MAGIC,
                    title=rule_text[:60],
                    rule_statement=rule_text,
                    forbidden_violations=f"Do not contradict rule: {rule_text[:50]}",
                )

        return project
