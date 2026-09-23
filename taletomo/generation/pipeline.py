import datetime
import logging
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from taletomo.consistency.checker import ContinuityChecker
from taletomo.consistency.models import ContinuityFinding, FindingCategory, FindingSeverity
from taletomo.context.retrieval import ContextAssembler
from taletomo.generation.models import DraftArtifact, DraftStatus, GenerationJob, JobAttempt, JobStatus
from taletomo.planning.models import Chapter
from taletomo.providers.adapters import BaseProviderAdapter, ProviderGateway
from taletomo.providers.models import BudgetReservation, ProviderConfig

logger = logging.getLogger(__name__)


class GenerationPipeline:
    @classmethod
    def execute_chapter_generation(
        cls,
        job: GenerationJob,
        worker_id: str = "worker-default",
        custom_adapter: BaseProviderAdapter = None,
    ) -> DraftArtifact:
        """Executes the full drafting pipeline: lease acquisition -> budget reservation -> context assembly -> drafting -> continuity check."""
        # 1. Atomic lease acquisition
        if not job.acquire_lease(worker_id=worker_id, duration_seconds=90):
            raise RuntimeError(
                f"Lease acquisition failed for job {job.id}: currently held by worker {job.lease_worker_id}"
            )

        job.status = JobStatus.PREPARING_CONTEXT
        job.stage = "Assembling context package and manifest"
        job.progress_pct = 15
        job.save(update_fields=["status", "stage", "progress_pct", "updated_at"])

        reservation = None
        attempt = None

        try:
            chapter = Chapter.objects.get(id=job.target_chapter_id)
            project = chapter.project

            # Resolve adapter scoped to user/project
            adapter = custom_adapter or ProviderGateway.get_adapter(user=job.user, project=project)

            # Query model profile context limit (supports 250k profiles)
            model_name = getattr(adapter.config, "default_drafting_model", "mock-drafting-v1")
            context_limit = 128000
            if hasattr(adapter, "config") and adapter.config:
                context_limit = adapter.config.get_model_context_limit(model_name)

            # Context assembly with verified model context limit
            ctx_package = ContextAssembler.assemble_chapter_context(
                chapter=chapter,
                model_context_limit=context_limit,
                requested_output_tokens=4000,
            )

            # Atomic Budget Reservation
            reservation = BudgetReservation.objects.create(
                user=job.user,
                project_id=project.id,
                job_id=job.id,
                reserved_tokens=min(context_limit, 8000),
                reserved_cost_usd=Decimal("0.0500"),
            )

            # 2. Transition to GENERATING
            job.status = JobStatus.GENERATING
            job.stage = "Calling model for prose generation"
            job.progress_pct = 40
            job.save(update_fields=["status", "stage", "progress_pct", "updated_at"])

            attempt = JobAttempt.objects.create(
                job=job,
                attempt_number=job.attempts.count() + 1,
                outcome="started",
            )

            # Call provider
            try:
                resp = adapter.generate_text(
                    prompt=ctx_package.user_prompt,
                    system_prompt=ctx_package.system_prompt,
                    max_tokens=4000,
                )
            except TimeoutError as te:
                if attempt:
                    attempt.outcome = "unknown_provider_outcome"
                    attempt.error_details = {"error": str(te)}
                    attempt.save(update_fields=["outcome", "error_details"])
                job.status = JobStatus.FAILED
                job.stage = "Unknown provider outcome: timeout after submission"
                job.error_message = "Provider request timed out after submission; billing status unknown."
                job.error_details = {"unknown_outcome": True, "billing_status": "unverified"}
                job.save(update_fields=["status", "stage", "error_message", "error_details", "updated_at"])
                raise

            attempt.outcome = "completed"
            attempt.provider_request_id = str(resp.raw_metadata.get("id", ""))
            attempt.save(update_fields=["outcome", "provider_request_id"])

            # Reconcile budget
            if reservation:
                reservation.reconcile(tokens_used=resp.total_tokens, cost_usd=resp.cost_usd)

            # Determine draft version
            existing_count = DraftArtifact.objects.filter(chapter=chapter).count()
            version_number = existing_count + 1

            word_count = len(resp.content.split())
            chapter_plan = getattr(chapter, "plan", None)
            target_words = chapter_plan.target_words if chapter_plan else project.target_words_per_chapter
            tolerance = chapter_plan.tolerance_percent if chapter_plan else project.tolerance_percent
            min_words = (target_words * (100 - tolerance) + 99) // 100
            max_words = (target_words * (100 + tolerance)) // 100
            length_outside_tolerance = not min_words <= word_count <= max_words

            draft = DraftArtifact.objects.create(
                chapter=chapter,
                version_number=version_number,
                prose_content=resp.content,
                word_count=word_count,
                model_name=resp.model or model_name,
                prompt_version="v1",
                context_manifest=ctx_package.manifest,
                status=DraftStatus.UNDER_REVIEW,
            )

            job.draft_artifact = draft
            job.confirmed_tokens = resp.total_tokens
            job.confirmed_cost = resp.cost_usd
            job.save(update_fields=["draft_artifact", "confirmed_tokens", "confirmed_cost", "updated_at"])

            # 3. Transition to CHECKING (Continuity check)
            job.status = JobStatus.CHECKING
            job.stage = "Auditing continuity and world rule integrity"
            job.progress_pct = 75
            job.save(update_fields=["status", "stage", "progress_pct", "updated_at"])

            ContinuityChecker.check_and_persist(
                chapter=chapter,
                prose=resp.content,
                draft_id=str(draft.id),
                adapter=adapter,
            )

            if length_outside_tolerance:
                ContinuityFinding.objects.create(
                    project=project,
                    chapter=chapter,
                    draft_id=draft.id,
                    category=FindingCategory.LENGTH,
                    severity=FindingSeverity.WARNING,
                    confidence=1.0,
                    claim=(
                        f"Draft has {word_count} words; target is {target_words} "
                        f"with an allowed range of {min_words}–{max_words}."
                    ),
                    conflicting_evidence=[
                        f"DraftArtifact {draft.id}: stored word count {word_count}",
                        f"Chapter contract: target {target_words} words ±{tolerance}%",
                    ],
                    source_references=[f"Draft {draft.id}"],
                    suggested_action="Continue or revise the draft to meet the target, or explicitly approve the shorter/longer length.",
                )

            # 4. Ready for review
            job.status = JobStatus.READY
            if length_outside_tolerance:
                job.stage = (
                    f"Draft has {word_count} words; target {target_words} ±{tolerance}%. "
                    "Needs author review for length."
                )
            else:
                job.stage = "Generation complete. Ready for author review."
            job.progress_pct = 100
            job.result_url = f"/projects/{project.id}/chapters/{chapter.id}/edit"
            job.save(update_fields=["status", "stage", "progress_pct", "result_url", "updated_at"])

            chapter.status = Chapter.Status.REVIEW
            chapter.active_draft_id = draft.id
            chapter.save(update_fields=["status", "active_draft_id", "updated_at"])

            return draft

        except Exception as e:
            logger.exception("Generation pipeline failed")
            if (
                reservation
                and reservation.status == BudgetReservation.Status.RESERVED
                and not job.error_details.get("unknown_outcome")
            ):
                reservation.release()
            if attempt and attempt.outcome == "started":
                attempt.outcome = "failed"
                attempt.error_details = {"error": str(e)}
                attempt.save(update_fields=["outcome", "error_details"])

            if job.status != JobStatus.FAILED:
                job.status = JobStatus.FAILED
                job.error_message = str(e)
                job.stage = "Pipeline failed"
                job.save(update_fields=["status", "error_message", "stage", "updated_at"])
            raise
        finally:
            job.release_lease(worker_id)
