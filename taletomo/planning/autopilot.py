import logging
import uuid
from typing import Optional
from django.db import IntegrityError, models, transaction
from taletomo.canon.models import ProposedCanonItem, TruthScope
from taletomo.canon.services import CanonService
from taletomo.generation.models import DraftArtifact, DraftStatus, GenerationJob, JobStatus
from taletomo.generation.tasks import generate_chapter_task
from taletomo.planning.models import Chapter, ChapterPlan, Project, ScenePlan
from taletomo.planning.services import PlanningService

logger = logging.getLogger(__name__)


class AutoPilotService:
    """Autonomous story orchestration engine for hands-off reading experience.

    Handles chapter contract preparation, background drafting, draft approval,
    and automatic canon commits so reader-mode users can enjoy their novel without
    manual workbench manipulation.
    """

    @staticmethod
    def ensure_chapter_contract(chapter: Chapter) -> ChapterPlan:
        """Ensures the chapter has a valid, approved ChapterPlan ready for drafting."""
        project = chapter.project

        # If chapter has no plan, expand rolling horizon
        if not hasattr(chapter, "plan"):
            PlanningService.ensure_rolling_horizon(
                project, horizon_size=max(5, chapter.chapter_number + 2)
            )
            chapter.refresh_from_db()

        plan = chapter.plan
        if plan.status != ChapterPlan.Status.APPROVED and plan.status != ChapterPlan.Status.LOCKED:
            if not plan.scenes.exists():
                half_words = max(500, project.target_words_per_chapter // 2)
                ScenePlan.objects.create(
                    chapter_plan=plan,
                    scene_order=1,
                    objective=f"Opening beat and narrative establishment for Chapter {chapter.chapter_number}",
                    conflict="Initial tension, curiosity, or external obstacle",
                    estimated_words=half_words,
                )
                ScenePlan.objects.create(
                    chapter_plan=plan,
                    scene_order=2,
                    objective=f"Development, escalation, and hook for Chapter {chapter.chapter_number}",
                    conflict="Pivotal interaction or revelation",
                    estimated_words=half_words,
                )
            plan.status = ChapterPlan.Status.APPROVED
            plan.save(update_fields=["status"])

        if chapter.status in (Chapter.Status.UNPLANNED, Chapter.Status.PLANNED):
            chapter.status = Chapter.Status.PLANNED
            chapter.save(update_fields=["status"])

        return plan

    @staticmethod
    def get_active_job(chapter: Chapter) -> Optional[GenerationJob]:
        """Returns any currently pending or generating job for the given chapter."""
        active_statuses = [
            JobStatus.QUEUED,
            JobStatus.PREPARING_CONTEXT,
            JobStatus.SUBMITTED,
            JobStatus.GENERATING,
            JobStatus.CHECKING,
            JobStatus.EXTRACTING,
        ]
        return GenerationJob.objects.filter(
            target_chapter_id=chapter.id,
            status__in=active_statuses,
        ).order_by("-created_at").first()

    @staticmethod
    def start_chapter_generation(project: Project, chapter: Chapter, user) -> GenerationJob:
        """Prepares contract and dispatches background drafting task."""
        AutoPilotService.ensure_chapter_contract(chapter)

        # Check for existing active job
        existing = AutoPilotService.get_active_job(chapter)
        if existing:
            return existing

        next_version = (
            chapter.drafts.aggregate(max_v=models.Max("version_number"))["max_v"] or 0
        ) + 1
        idempotency_key = f"draft-{chapter.id}-v{next_version}"

        try:
            job = GenerationJob.objects.create(
                project=project,
                user=user,
                job_type="chapter_draft",
                idempotency_key=idempotency_key,
                target_chapter_id=chapter.id,
                stage="Queued for generation",
            )
        except IntegrityError:
            # Raced or existing terminal job claimed key
            existing = AutoPilotService.get_active_job(chapter)
            if existing:
                return existing
            job = GenerationJob.objects.create(
                project=project,
                user=user,
                job_type="chapter_draft",
                idempotency_key=f"{idempotency_key}-{uuid.uuid4().hex[:8]}",
                target_chapter_id=chapter.id,
                stage="Queued for generation",
            )

        transaction.on_commit(lambda: generate_chapter_task.delay(str(job.id)))
        return job

    @staticmethod
    def auto_advance_chapter(project: Project, chapter: Chapter, user):
        """Automatically approves draft and commits extracted canon when reader advances to next chapter."""
        if chapter.status == Chapter.Status.LOCKED:
            return  # Already committed and locked

        if not chapter.active_draft_id:
            return  # No draft to commit

        draft = DraftArtifact.objects.filter(id=chapter.active_draft_id).first()
        if not draft:
            return

        # 1. Ensure draft is marked accepted
        if draft.status != DraftStatus.ACCEPTED:
            draft.status = DraftStatus.ACCEPTED
            draft.save(update_fields=["status"])

        if chapter.status != Chapter.Status.APPROVED:
            chapter.status = Chapter.Status.APPROVED
            chapter.current_word_count = draft.word_count
            chapter.save(update_fields=["status", "current_word_count"])

        # 2. Gather extracted proposed canon items
        items = list(
            chapter.proposed_canon_items.filter(
                status__in=[ProposedCanonItem.Status.PROPOSED, ProposedCanonItem.Status.APPROVED]
            )
        )

        summary_text = chapter.current_summary or f"Events of Chapter {chapter.chapter_number}"
        events = [
            {
                "event_type": "chapter_conclusion",
                "summary": summary_text,
                "payload": {"chapter_number": chapter.chapter_number},
            }
        ]
        facts = []
        thread_updates = []
        character_updates = []

        for item in items:
            payload = item.payload or {}
            if item.kind == ProposedCanonItem.Kind.FACT:
                facts.append(
                    {
                        "subject": payload.get("subject", ""),
                        "predicate": payload.get("predicate", ""),
                        "value": payload.get("value", ""),
                        "scope": payload.get("scope", TruthScope.WORLD_TRUTH),
                    }
                )
            elif item.kind == ProposedCanonItem.Kind.EVENT:
                events.append(
                    {
                        "event_type": payload.get("event_type", "plot_progress"),
                        "summary": payload.get("summary", ""),
                    }
                )
            elif item.kind == ProposedCanonItem.Kind.THREAD_UPDATE:
                thread_updates.append(payload)
            elif item.kind == ProposedCanonItem.Kind.CHARACTER_UPDATE:
                character_updates.append(payload)

        # 3. Commit chapter canon into world state
        try:
            with transaction.atomic():
                CanonService.commit_chapter_canon(
                    project=project,
                    chapter=chapter,
                    expected_head=project.active_branch_head,
                    events=events,
                    facts=facts,
                    actor=user,
                    override_blockers=True,
                    override_rationale="Auto-Pilot reader mode progression",
                    thread_updates=thread_updates,
                    character_updates=character_updates,
                )
                if items:
                    ProposedCanonItem.objects.filter(id__in=[it.id for it in items]).update(
                        status=ProposedCanonItem.Status.CONSUMED
                    )
        except Exception as e:
            logger.warning(
                f"AutoPilot auto-commit for Chapter {chapter.chapter_number} failed or skipped: {e}"
            )
