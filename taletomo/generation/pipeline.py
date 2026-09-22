import datetime
import logging
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from taletomo.consistency.checker import ContinuityChecker
from taletomo.context.retrieval import ContextAssembler
from taletomo.generation.models import DraftArtifact, DraftStatus, GenerationJob, JobAttempt, JobStatus
from taletomo.planning.models import Chapter
from taletomo.providers.adapters import BaseProviderAdapter, ProviderGateway
from taletomo.providers.models import ProviderConfig

logger = logging.getLogger(__name__)


class GenerationPipeline:
    @classmethod
    def execute_chapter_generation(
        cls,
        job: GenerationJob,
        worker_id: str = "worker-default",
        custom_adapter: BaseProviderAdapter = None,
    ) -> DraftArtifact:
        """Executes the full drafting pipeline: context assembly -> drafting -> continuity check."""
        # 1. Lease acquisition and validation
        now = timezone.now()
        job.lease_worker_id = worker_id
        job.lease_expires_at = now + datetime.timedelta(seconds=90)
        job.status = JobStatus.PREPARING_CONTEXT
        job.stage = "Assembling context package and manifest"
        job.progress_pct = 15
        job.save(update_fields=["lease_worker_id", "lease_expires_at", "status", "stage", "progress_pct", "updated_at"])

        try:
            chapter = Chapter.objects.get(id=job.target_chapter_id)
            project = chapter.project

            # Context assembly
            ctx_package = ContextAssembler.assemble_chapter_context(chapter)

            # 2. Transition to GENERATING
            job.status = JobStatus.GENERATING
            job.stage = "Calling model for prose generation"
            job.progress_pct = 40
            job.save(update_fields=["status", "stage", "progress_pct", "updated_at"])

            # Resolve adapter
            adapter = custom_adapter or ProviderGateway.get_adapter()

            # Record attempt
            attempt = JobAttempt.objects.create(
                job=job,
                attempt_number=job.attempts.count() + 1,
                outcome="generating",
            )

            # Generate prose
            resp = adapter.generate_text(
                prompt=ctx_package.user_prompt,
                system_prompt=ctx_package.system_prompt,
                max_tokens=4000,
            )

            attempt.outcome = "completed"
            attempt.provider_request_id = str(resp.raw_metadata.get("id", ""))
            attempt.save(update_fields=["outcome", "provider_request_id"])

            # Determine draft version
            existing_count = DraftArtifact.objects.filter(chapter=chapter).count()
            version_number = existing_count + 1

            word_count = len(resp.content.split())
            draft = DraftArtifact.objects.create(
                chapter=chapter,
                version_number=version_number,
                prose_content=resp.content,
                word_count=word_count,
                model_name=resp.model or "unknown-model",
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

            # 4. Ready for review
            job.status = JobStatus.READY
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
            job.status = JobStatus.FAILED
            job.error_message = str(e)
            job.stage = "Pipeline failed"
            job.save(update_fields=["status", "error_message", "stage", "updated_at"])
            raise
