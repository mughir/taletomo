import math
from typing import Any, Dict, List, Optional
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
