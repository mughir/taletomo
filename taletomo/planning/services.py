import math
from typing import Any, Dict, List, Optional, Tuple
from django.db import transaction
from django.utils.text import slugify
from taletomo.planning.models import (
    Arc,
    Chapter,
    ChapterPlan,
    Project,
    ProjectLengthPreset,
    ScenePlan,
    SeriesBible,
    SeriesSpine,
    Volume,
)


class PlanningService:
    @staticmethod
    @transaction.atomic
    def create_project_with_scaffold(
        owner,
        title: str,
        premise: str,
        target_chapters: int = 100,
        genre: str = "Fantasy",
        subgenre: str = "",
        audience: str = "Young Adult / General",
        tone: str = "Epic, Mysterious",
        language: str = "English",
        pov: str = "Third Person Limited",
        tense: str = "Past Tense",
        pacing: str = "Balanced",
        protagonist_type: str = "",
        novel_tags: str = "",
        length_preset: str = ProjectLengthPreset.STANDARD,
        target_words_per_chapter: int = 2200,
        content_boundaries: str = "",
        bible_data: Optional[Dict[str, Any]] = None,
        spine_data: Optional[Dict[str, Any]] = None,
    ) -> Project:
        """Initializes a new project with its Bible, Spine, initial Volume, Arc, and rolling Horizon."""
        if not 1 <= target_chapters <= 4000:
            raise ValueError("Target chapter count must be between 1 and 4,000.")
        if not 500 <= target_words_per_chapter <= 10000:
            raise ValueError("Target chapter length must be between 500 and 10,000 words.")

        slug = slugify(title) or "novel"
        base_slug = slug
        counter = 1
        while Project.objects.filter(slug=slug).exists():
            slug = f"{base_slug}-{counter}"
            counter += 1

        project = Project.objects.create(
            owner=owner,
            title=title,
            slug=slug,
            premise=premise,
            target_chapters=target_chapters,
            length_preset=length_preset,
            genre=genre,
            subgenre=subgenre,
            audience=audience,
            tone=tone,
            language=language,
            pov=pov,
            tense=tense,
            pacing=pacing,
            protagonist_type=protagonist_type,
            novel_tags=novel_tags,
            target_words_per_chapter=target_words_per_chapter,
            content_boundaries=content_boundaries,
        )

        b_data = bible_data or {}
        SeriesBible.objects.create(
            project=project,
            pitch=b_data.get("pitch", premise),
            hook=b_data.get("hook", f"A tale set in the world of {title}."),
            central_conflict=b_data.get("central_conflict", "Protagonist faces overwhelming opposition."),
            reader_promise=b_data.get("reader_promise", "Immersive progression and satisfying resolutions."),
            world_setting=b_data.get("world_setting", "A vast and evolving realm."),
            magic_tech_rules=b_data.get("magic_tech_rules", ["Equal exchange of magical power."]),
            tone_rules=b_data.get("tone_rules", ["Keep emotional stakes grounded."]),
            prose_style_guide=b_data.get("prose_style_guide", "Lyrical yet punchy action descriptions."),
        )

        s_data = spine_data or {}
        SeriesSpine.objects.create(
            project=project,
            series_promise=s_data.get("series_promise", f"The journey across {target_chapters} chapters."),
            ending_direction=s_data.get("ending_direction", "Climactic resolution of the core conflict."),
            major_milestones=s_data.get(
                "major_milestones",
                [
                    {"chapter": max(1, target_chapters // 4), "event": "First major paradigm shift"},
                    {"chapter": max(1, target_chapters // 2), "event": "Midpoint point of no return"},
                    {"chapter": max(1, (target_chapters * 3) // 4), "event": "Dark night of the soul"},
                    {"chapter": target_chapters, "event": "Final confrontation and climax"},
                ],
            ),
        )

        # Create Volume 1 and Opening Arc
        vol1 = Volume.objects.create(
            project=project,
            volume_number=1,
            title="Volume 1: Awakening",
            premise="The beginnings of the journey and initial trial.",
            target_chapters=min(target_chapters, 50),
        )

        arc1 = Arc.objects.create(
            volume=vol1,
            arc_number=1,
            title="Arc 1: The First Step",
            conflict_goal="Survive the initial incident and establish a base.",
            start_chapter=1,
            end_chapter=min(target_chapters, 10),
            target_state_transition="Protagonist moves from helpless bystander to active seeker.",
        )

        # Scaffold initial rolling horizon (detailed chapter 1 contract, sparse remainder)
        PlanningService.ensure_rolling_horizon(project, horizon_size=5)

        return project

    @staticmethod
    def ensure_rolling_horizon(project: Project, horizon_size: int = 5):
        """Ensures that the next `horizon_size` chapters exist and have planned contracts."""
        latest_chapter = (
            Chapter.objects.filter(project=project).order_by("-chapter_number").first()
        )
        current_max = latest_chapter.chapter_number if latest_chapter else 0

        target_max = min(project.target_chapters, max(current_max, horizon_size))
        vol1 = Volume.objects.filter(project=project, volume_number=1).first()
        arc1 = Arc.objects.filter(volume=vol1, arc_number=1).first() if vol1 else None

        for ch_num in range(1, target_max + 1):
            chapter, created = Chapter.objects.get_or_create(
                project=project,
                chapter_number=ch_num,
                defaults={
                    "volume": vol1,
                    "arc": arc1,
                    "title": f"Chapter {ch_num}",
                    "status": Chapter.Status.PLANNED if ch_num == 1 else Chapter.Status.UNPLANNED,
                },
            )

            if ch_num <= horizon_size and not hasattr(chapter, "plan"):
                plan = ChapterPlan.objects.create(
                    chapter=chapter,
                    objectives=[f"Advance chapter {ch_num} primary objective."],
                    required_beats=[f"Opening scene hooks the reader into chapter {ch_num}."],
                    prohibited_outcomes=["Do not prematurely reveal the overarching mystery."],
                    continuity_requirements=[],
                    thread_operations={"advance": [], "reinforce": [], "open": [], "close": []},
                    end_state=[f"Chapter {ch_num} ending cliffhanger or transition."],
                    target_words=project.target_words_per_chapter,
                    status=ChapterPlan.Status.APPROVED if ch_num == 1 else ChapterPlan.Status.PROPOSED,
                )
                # Create default scenes
                ScenePlan.objects.create(
                    chapter_plan=plan,
                    scene_order=1,
                    objective=f"Scene 1 objective for Chapter {ch_num}",
                    conflict="Initial friction or suspense",
                    estimated_words=project.target_words_per_chapter // 2,
                )
                ScenePlan.objects.create(
                    chapter_plan=plan,
                    scene_order=2,
                    objective=f"Scene 2 objective for Chapter {ch_num}",
                    conflict="Escalation and resolution/hook",
                    estimated_words=project.target_words_per_chapter // 2,
                )
                if chapter.status == Chapter.Status.UNPLANNED:
                    chapter.status = Chapter.Status.PLANNED
                    chapter.save(update_fields=["status"])

    @staticmethod
    def validate_chapter_contract(plan: ChapterPlan) -> List[str]:
        """Validates that a chapter contract has no syntax or logical structural gaps."""
        errors = []
        if not plan.objectives:
            errors.append("Chapter contract MUST have at least one objective.")
        if not plan.required_beats:
            errors.append("Chapter contract MUST specify required beats.")
        if plan.target_words < 500 or plan.target_words > 10000:
            errors.append(f"Target words ({plan.target_words}) out of valid range (500–10,000).")
        return errors

    @staticmethod
    @transaction.atomic
    def replan_frontier(
        project: Project,
        from_chapter: Optional[int] = None,
        horizon_size: int = 5,
        custom_adapter=None,
    ) -> Dict[str, Any]:
        """Dynamically adapts future uncommitted chapter contracts to align with newly established canon."""
        import json
        import re
        from taletomo.canon.models import CanonFact, Character, PlotThread
        from taletomo.providers.adapters import ProviderGateway

        # 1. Determine starting chapter number if not provided
        if not from_chapter:
            highest_committed = (
                Chapter.objects.filter(
                    project=project, status__in=[Chapter.Status.APPROVED, Chapter.Status.LOCKED]
                )
                .order_by("-chapter_number")
                .first()
            )
            from_chapter = (highest_committed.chapter_number + 1) if highest_committed else 1

        end_chapter = min(project.target_chapters, from_chapter + horizon_size - 1)
        if from_chapter > end_chapter:
            return {
                "replanned_chapters": [],
                "skipped_chapters": [],
                "horizon_size": horizon_size,
                "open_threads_count": 0,
            }

        # 2. Gather context up to from_chapter - 1
        prev_chapter = (
            Chapter.objects.filter(project=project, chapter_number=from_chapter - 1).first()
        )
        prev_summary = prev_chapter.current_summary if prev_chapter else ""

        open_threads = list(
            PlotThread.objects.filter(
                project=project,
                status__in=[PlotThread.Status.OPEN, PlotThread.Status.PROGRESSING],
            ).order_by("setup_chapter")[:10]
        )
        threads_summary = [f"{t.title} ({t.category})" for t in open_threads]

        characters = list(Character.objects.filter(project=project))
        wounded_characters = [
            f"{c.name}: {c.wounds_status}" for c in characters if c.wounds_status
        ]

        facts = list(
            CanonFact.objects.filter(
                project=project, canonical_status=CanonFact.Status.CONFIRMED
            ).order_by("-updated_at")[:8]
        )
        facts_summary = [f"{f.subject} {f.predicate} {f.value}" for f in facts]

        current_arc = (
            Arc.objects.filter(
                volume__project=project,
                start_chapter__lte=from_chapter,
                end_chapter__gte=from_chapter,
            ).first()
            or Arc.objects.filter(volume__project=project).order_by("arc_number").first()
        )
        arc_goal = current_arc.conflict_goal if current_arc else project.premise

        spine = getattr(project, "spine", None)
        spine_promise = spine.series_promise if spine else project.premise

        # 3. Resolve adapter
        adapter = custom_adapter
        if not adapter and project.owner:
            try:
                adapter = ProviderGateway.get_adapter(user=project.owner, project=project)
            except Exception:
                adapter = None

        model = (
            adapter.config.get_model_for_task("planning")
            if (adapter and hasattr(adapter, "config"))
            else None
        )

        replanned_chapters = []
        skipped_chapters = []

        vol1 = Volume.objects.filter(project=project, volume_number=1).first()

        for ch_num in range(from_chapter, end_chapter + 1):
            ch, _ = Chapter.objects.get_or_create(
                project=project,
                chapter_number=ch_num,
                defaults={
                    "volume": current_arc.volume if current_arc else vol1,
                    "arc": current_arc,
                    "title": f"Chapter {ch_num}",
                    "status": Chapter.Status.PLANNED,
                },
            )

            # Never overwrite approved or locked canon chapters!
            if ch.status in [Chapter.Status.APPROVED, Chapter.Status.LOCKED]:
                skipped_chapters.append(ch_num)
                continue

            prompt = (
                f"TASK: REPLAN_CHAPTER_PLAN\n"
                f"Project: {project.title}\n"
                f"Chapter: {ch_num}\n"
                f"Arc Goal: {arc_goal}\n"
                f"Series Spine Promise: {spine_promise}\n"
                f"Previous Chapter Ending/Summary: {prev_summary or 'Opening story frontier.'}\n"
                f"Open Plot Threads: {', '.join(threads_summary) or 'None'}\n"
                f"Active Character Wounds & Impairments: {', '.join(wounded_characters) or 'None'}\n"
                f"Recent Confirmed Lore: {', '.join(facts_summary) or 'Initial state'}\n"
                f"Target Words: {project.target_words_per_chapter}\n\n"
                "Return a structured JSON object with keys: title, objectives (list), required_beats (list), "
                "prohibited_outcomes (list), continuity_requirements (list), thread_operations (dict with advance/open/close), "
                "and scenes (list of objects with scene_order, objective, conflict, estimated_words)."
            )

            contract_data = None
            if adapter:
                try:
                    resp = adapter.generate_text(prompt, model=model)
                    match = re.search(r"\{.*\}", resp.content, re.DOTALL)
                    if match:
                        contract_data = json.loads(match.group(0))
                except Exception:
                    contract_data = None

            if not contract_data:
                # Deterministic fallback when provider unavailable
                contract_data = {
                    "title": ch.title or f"Chapter {ch_num}",
                    "objectives": [
                        f"Advance conflict towards arc goal: {arc_goal[:60]}.",
                        f"Address unresolved threads: {', '.join(threads_summary[:2]) or 'primary mission'}.",
                    ],
                    "required_beats": [
                        "Confront the consequences of previous chapter events.",
                        "Encounter unexpected complication or obstacle.",
                        "Transition towards next planned milestone.",
                    ],
                    "prohibited_outcomes": [
                        "Do not disregard active injuries or previous canon outcomes.",
                    ],
                    "continuity_requirements": [w for w in wounded_characters[:3]],
                    "thread_operations": {
                        "advance": [t.title for t in open_threads[:2]],
                        "open": [],
                        "close": [],
                    },
                    "scenes": [
                        {
                            "scene_order": 1,
                            "objective": f"Scene 1 for Chapter {ch_num}",
                            "conflict": "Immediate challenge",
                            "estimated_words": project.target_words_per_chapter // 2,
                        },
                        {
                            "scene_order": 2,
                            "objective": f"Scene 2 for Chapter {ch_num}",
                            "conflict": "Escalation",
                            "estimated_words": project.target_words_per_chapter // 2,
                        },
                    ],
                }

            # Inject callback reminders for dormant plot threads
            dormant_for_ch = [t for t in open_threads if t.is_dormant(ch_num)]
            if dormant_for_ch and "required_beats" in contract_data and isinstance(contract_data["required_beats"], list):
                for dt in dormant_for_ch[:2]:
                    reminder = f"Narrative Callback: Reintroduce or foreshadow dormant plot thread '{dt.title}'."
                    if reminder not in contract_data["required_beats"]:
                        contract_data["required_beats"].append(reminder)

            # Update or create ChapterPlan
            plan, _ = ChapterPlan.objects.get_or_create(
                chapter=ch,
                defaults={
                    "target_words": project.target_words_per_chapter,
                    "tolerance_percent": project.tolerance_percent,
                },
            )
            plan.objectives = contract_data.get("objectives", [f"Chapter {ch_num} objectives."])
            plan.required_beats = contract_data.get(
                "required_beats", ["Opening beat.", "Closing beat."]
            )
            plan.prohibited_outcomes = contract_data.get("prohibited_outcomes", [])
            plan.continuity_requirements = contract_data.get("continuity_requirements", [])
            plan.thread_operations = contract_data.get(
                "thread_operations", {"advance": [], "open": [], "close": []}
            )
            plan.status = ChapterPlan.Status.PROPOSED
            plan.save()

            if contract_data.get("title") and contract_data["title"] != f"Chapter {ch_num}":
                ch.title = contract_data["title"]
            ch.status = Chapter.Status.PLANNED
            ch.save(update_fields=["title", "status"])

            # Update scenes
            plan.scenes.all().delete()
            scenes_list = contract_data.get("scenes", [])
            if not scenes_list:
                scenes_list = [
                    {
                        "scene_order": 1,
                        "objective": f"Scene 1 for Chapter {ch_num}",
                        "conflict": "",
                        "estimated_words": project.target_words_per_chapter // 2,
                    },
                    {
                        "scene_order": 2,
                        "objective": f"Scene 2 for Chapter {ch_num}",
                        "conflict": "",
                        "estimated_words": project.target_words_per_chapter // 2,
                    },
                ]
            for s in scenes_list:
                ScenePlan.objects.create(
                    chapter_plan=plan,
                    scene_order=s.get("scene_order", 1),
                    objective=s.get("objective", f"Objective for scene {s.get('scene_order', 1)}"),
                    conflict=s.get("conflict", ""),
                    characters=s.get("characters", []),
                    setting=s.get("setting", ""),
                    estimated_words=s.get(
                        "estimated_words", project.target_words_per_chapter // len(scenes_list)
                    ),
                )

            replanned_chapters.append(ch_num)
            prev_summary = f"Chapter {ch_num}: {plan.objectives[0] if plan.objectives else ''}"

        return {
            "replanned_chapters": replanned_chapters,
            "skipped_chapters": skipped_chapters,
            "horizon_size": horizon_size,
            "open_threads_count": len(open_threads),
        }

    @staticmethod
    def _clone_bible(source_project: Project, branched_project: Project) -> None:
        """Clones the SeriesBible configuration if present."""
        if hasattr(source_project, "bible"):
            b = source_project.bible
            SeriesBible.objects.create(
                project=branched_project,
                pitch=b.pitch,
                hook=b.hook,
                central_conflict=b.central_conflict,
                reader_promise=b.reader_promise,
                world_setting=b.world_setting,
                era=b.era,
                magic_tech_rules=b.magic_tech_rules,
                factions_overview=b.factions_overview,
                tone_rules=b.tone_rules,
                prose_style_guide=b.prose_style_guide,
                author_constraints=b.author_constraints,
            )

    @staticmethod
    def _clone_spine(source_project: Project, branched_project: Project) -> None:
        """Clones the SeriesSpine architecture if present."""
        if hasattr(source_project, "spine"):
            s = source_project.spine
            SeriesSpine.objects.create(
                project=branched_project,
                series_promise=s.series_promise,
                ending_direction=s.ending_direction,
                major_milestones=s.major_milestones,
                sparse_volumes_overview=s.sparse_volumes_overview,
            )

    @staticmethod
    def _clone_volumes_and_arcs(
        source_project: Project,
        branched_project: Project,
    ) -> Tuple[Dict[Any, Volume], Dict[Any, Arc]]:
        """Clones structural Volumes and Arcs, returning mapping dictionaries."""
        vol_map = {}
        for vol in source_project.volumes.all():
            new_vol = Volume.objects.create(
                project=branched_project,
                volume_number=vol.volume_number,
                title=vol.title,
                premise=vol.premise,
                target_chapters=vol.target_chapters,
                established_outcomes=vol.established_outcomes,
            )
            vol_map[vol.id] = new_vol

        arc_map = {}
        for arc in Arc.objects.filter(volume__project=source_project):
            new_vol = vol_map.get(arc.volume_id)
            if new_vol:
                new_arc = Arc.objects.create(
                    volume=new_vol,
                    arc_number=arc.arc_number,
                    title=arc.title,
                    conflict_goal=arc.conflict_goal,
                    start_chapter=arc.start_chapter,
                    end_chapter=arc.end_chapter,
                    target_state_transition=arc.target_state_transition,
                )
                arc_map[arc.id] = new_arc
        return vol_map, arc_map

    @staticmethod
    def _clone_canon_entities(
        source_project: Project,
        branched_project: Project,
    ) -> None:
        """Clones factions, characters, locations, rules, and items into the branched timeline."""
        from taletomo.canon.models import (
            Character,
            CharacterRelationship,
            Faction,
            Item,
            Location,
            WorldRule,
        )

        for fac in source_project.factions.all():
            Faction.objects.create(
                project=branched_project,
                name=fac.name,
                goals=fac.goals,
                resources=fac.resources,
                members=fac.members,
                alliances=fac.alliances,
            )

        char_id_map = {}
        for char in source_project.characters.all():
            new_char = Character.objects.create(
                project=branched_project,
                name=char.name,
                aliases=char.aliases,
                role=char.role,
                traits=char.traits,
                goals=char.goals,
                internal_need=char.internal_need,
                appearance=char.appearance,
                dialogue_style=char.dialogue_style,
                wounds_status=char.wounds_status,
                is_alive=char.is_alive,
                beliefs=char.beliefs,
                metadata=char.metadata,
                embedding=char.embedding,
            )
            char_id_map[char.id] = new_char

        for rel in source_project.character_relationships.all():
            src_obj = char_id_map.get(rel.source_character_id)
            tgt_obj = char_id_map.get(rel.target_character_id)
            if src_obj and tgt_obj:
                CharacterRelationship.objects.update_or_create(
                    project=branched_project,
                    source_character=src_obj,
                    target_character=tgt_obj,
                    defaults={
                        "relationship_type": rel.relationship_type,
                        "description": rel.description,
                        "dynamic_status": rel.dynamic_status,
                    },
                )

        for loc in source_project.locations.all():
            Location.objects.create(
                project=branched_project,
                name=loc.name,
                description=loc.description,
                travel_rules=loc.travel_rules,
                current_state=loc.current_state,
                coord_x=loc.coord_x,
                coord_y=loc.coord_y,
                region=loc.region,
                embedding=loc.embedding,
            )

        for rule in source_project.rules.all():
            WorldRule.objects.create(
                project=branched_project,
                category=rule.category,
                title=rule.title,
                rule_statement=rule.rule_statement,
                forbidden_violations=rule.forbidden_violations,
            )

        char_name_map = {c.name.lower(): c for c in branched_project.characters.all()}
        loc_name_map = {l.name.lower(): l for l in branched_project.locations.all()}
        for it in source_project.items.all():
            h_obj = char_name_map.get(it.current_holder.name.lower()) if it.current_holder else None
            l_obj = loc_name_map.get(it.current_location.name.lower()) if it.current_location else None
            Item.objects.create(
                project=branched_project,
                name=it.name,
                description=it.description,
                status_notes=it.status_notes,
                is_destroyed=it.is_destroyed,
                destroyed_at_chapter=it.destroyed_at_chapter,
                current_holder=h_obj,
                current_location=l_obj,
            )

    @staticmethod
    def _clone_facts_and_threads(
        source_project: Project,
        branched_project: Project,
        from_chapter: int,
    ) -> None:
        """Clones confirmed facts and plot threads active up to the branch point."""
        from taletomo.canon.models import CanonFact, PlotThread

        for fact in source_project.canon_facts.filter(canonical_status=CanonFact.Status.CONFIRMED):
            CanonFact.objects.create(
                project=branched_project,
                subject=fact.subject,
                predicate=fact.predicate,
                value=fact.value,
                truth_scope=fact.truth_scope,
                story_time_valid_from=fact.story_time_valid_from,
                story_time_valid_until=fact.story_time_valid_until,
                provenance=fact.provenance,
                confidence=fact.confidence,
                canonical_status=fact.canonical_status,
                embedding=fact.embedding,
            )

        for th in source_project.plot_threads.filter(setup_chapter__lte=from_chapter):
            PlotThread.objects.create(
                project=branched_project,
                title=th.title,
                category=th.category,
                status=th.status,
                setup_chapter=th.setup_chapter,
                payoff_chapter=th.payoff_chapter,
                notes=th.notes,
            )

    @staticmethod
    def _clone_chapters_and_drafts(
        source_project: Project,
        branched_project: Project,
        from_chapter: int,
        vol_map: Dict[Any, Volume],
        arc_map: Dict[Any, Arc],
    ) -> None:
        """Clones committed chapters with drafts and scene plans, and resets post-branch chapters."""
        from taletomo.generation.models import DraftArtifact

        source_chapters = list(
            source_project.chapters.prefetch_related("drafts").order_by("chapter_number")
        )
        for ch in source_chapters:
            new_vol = vol_map.get(ch.volume_id)
            new_arc = arc_map.get(ch.arc_id)

            if ch.chapter_number <= from_chapter:
                new_ch = Chapter.objects.create(
                    project=branched_project,
                    volume=new_vol,
                    arc=new_arc,
                    chapter_number=ch.chapter_number,
                    title=ch.title,
                    pov_character_name=ch.pov_character_name,
                    status=ch.status,
                    current_word_count=ch.current_word_count,
                    current_summary=ch.current_summary,
                )

                if ch.active_draft_id:
                    active_draft = ch.drafts.filter(id=ch.active_draft_id).first()
                    if active_draft:
                        cloned_draft = DraftArtifact.objects.create(
                            chapter=new_ch,
                            version_number=active_draft.version_number,
                            prose_content=active_draft.prose_content,
                            word_count=active_draft.word_count,
                            model_name=active_draft.model_name,
                            status=active_draft.status,
                        )
                        new_ch.active_draft_id = cloned_draft.id
                        new_ch.save(update_fields=["active_draft_id"])

                if hasattr(ch, "plan"):
                    old_plan = ch.plan
                    new_plan = ChapterPlan.objects.create(
                        chapter=new_ch,
                        objectives=old_plan.objectives,
                        required_beats=old_plan.required_beats,
                        prohibited_outcomes=old_plan.prohibited_outcomes,
                        continuity_requirements=old_plan.continuity_requirements,
                        thread_operations=old_plan.thread_operations,
                        end_state=old_plan.end_state,
                        target_words=old_plan.target_words,
                        tolerance_percent=old_plan.tolerance_percent,
                        status=old_plan.status,
                    )
                    for s in old_plan.scenes.all():
                        ScenePlan.objects.create(
                            chapter_plan=new_plan,
                            scene_order=s.scene_order,
                            objective=s.objective,
                            conflict=s.conflict,
                            characters=s.characters,
                            setting=s.setting,
                            estimated_words=s.estimated_words,
                        )
            else:
                Chapter.objects.create(
                    project=branched_project,
                    volume=new_vol,
                    arc=new_arc,
                    chapter_number=ch.chapter_number,
                    title=f"Chapter {ch.chapter_number}",
                    status=Chapter.Status.UNPLANNED,
                )

    @staticmethod
    @transaction.atomic
    def branch_project(
        source_project: Project,
        from_chapter: int,
        branch_name: str,
    ) -> Project:
        """Creates a parallel 'What-If' timeline diverging from a specified chapter."""
        clean_branch = branch_name.strip() or "Alternate Timeline"
        branch_slug = f"{source_project.slug}-{slugify(clean_branch)}"
        base_slug = branch_slug
        counter = 1
        while Project.objects.filter(slug=branch_slug).exists():
            branch_slug = f"{base_slug}-{counter}"
            counter += 1

        branched_project = Project.objects.create(
            owner=source_project.owner,
            title=f"{source_project.title} ({clean_branch})",
            slug=branch_slug,
            premise=source_project.premise,
            target_chapters=source_project.target_chapters,
            length_preset=source_project.length_preset,
            target_words_per_chapter=source_project.target_words_per_chapter,
            tolerance_percent=source_project.tolerance_percent,
            genre=source_project.genre,
            subgenre=source_project.subgenre,
            audience=source_project.audience,
            tone=source_project.tone,
            language=source_project.language,
            prose_language=source_project.prose_language,
            pov=source_project.pov,
            tense=source_project.tense,
            pacing=source_project.pacing,
            protagonist_type=source_project.protagonist_type,
            novel_tags=source_project.novel_tags,
            content_boundaries=source_project.content_boundaries,
            parent_project=source_project,
            branch_point_chapter=from_chapter,
            branch_name=clean_branch,
        )

        # 1. Clone Bible & Spine
        PlanningService._clone_bible(source_project, branched_project)
        PlanningService._clone_spine(source_project, branched_project)

        # 2. Clone Volumes and Arcs
        vol_map, arc_map = PlanningService._clone_volumes_and_arcs(source_project, branched_project)

        # 3. Clone Canon Entities & Relationships
        PlanningService._clone_canon_entities(source_project, branched_project)

        # 4. Clone Canon Facts and Plot Threads up to from_chapter
        PlanningService._clone_facts_and_threads(source_project, branched_project, from_chapter)

        # 5. Clone Chapters, Drafts, and Plans
        PlanningService._clone_chapters_and_drafts(
            source_project, branched_project, from_chapter, vol_map, arc_map
        )

        PlanningService.ensure_rolling_horizon(
            branched_project, horizon_size=min(source_project.target_chapters, from_chapter + 3)
        )

        return branched_project


