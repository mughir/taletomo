import logging
from decimal import Decimal, ROUND_HALF_UP
from django.db import models, transaction
from django.utils import timezone
from taletomo.canon.extraction import CanonExtractionService
from taletomo.consistency.checker import ContinuityChecker
from taletomo.consistency.models import ContinuityFinding, FindingCategory, FindingSeverity
from taletomo.context.budget import BudgetCalculator
from taletomo.context.retrieval import ContextAssembler
from taletomo.generation.heartbeat import LeaseHeartbeat
from taletomo.generation.models import (
    DraftArtifact,
    DraftStatus,
    GenerationJob,
    JobAttempt,
    JobStatus,
)
from taletomo.planning.models import Chapter
from taletomo.providers.adapters import BaseProviderAdapter, ProviderGateway, safe_decimal
from taletomo.providers.models import BudgetReservation, ProviderConfig

logger = logging.getLogger(__name__)


class LeaseAcquisitionError(RuntimeError):
    """Raised when another live worker holds the job lease."""


class GenerationPipeline:
    @staticmethod
    def _estimate_reservation_cost(input_tokens: int, output_tokens: int, model_profile: dict) -> Decimal:
        """Prices the spending allowance from the model's pricing profile, with 25% headroom."""
        price_in = safe_decimal(model_profile.get("pricing_input_per_m"), "0.0")
        price_out = safe_decimal(model_profile.get("pricing_output_per_m"), "0.0")
        raw = (
            Decimal(input_tokens) * price_in / Decimal(1000000)
            + Decimal(output_tokens) * price_out / Decimal(1000000)
        ) * Decimal("1.25")
        return raw.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

    @classmethod
    def execute_chapter_generation(
        cls,
        job: GenerationJob,
        worker_id: str = "worker-default",
        custom_adapter: BaseProviderAdapter = None,
        lease_seconds: int = 90,
        heartbeat_interval: float = None,
    ) -> DraftArtifact | None:
        """Executes the full drafting pipeline: lease acquisition -> budget reservation -> context assembly -> drafting -> continuity check -> canon extraction.

        A lease heartbeat renews the job lease on a background thread for the
        whole execution so long provider calls cannot let the lease expire and
        allow a second worker to double-generate.
        """
        # 1. Atomic lease acquisition
        if not job.acquire_lease(worker_id=worker_id, duration_seconds=lease_seconds):
            raise LeaseAcquisitionError(
                f"Lease acquisition failed for job {job.id}: currently held by worker {job.lease_worker_id}"
            )

        heartbeat = LeaseHeartbeat(
            job,
            worker_id=worker_id,
            interval_seconds=heartbeat_interval if heartbeat_interval is not None else max(1.0, lease_seconds / 3),
        )
        heartbeat.start()

        reservation = None
        attempt = None

        try:
            chapter = Chapter.objects.get(id=job.target_chapter_id)
            project = chapter.project

            # Resolve adapter scoped to user/project
            adapter = custom_adapter or ProviderGateway.get_adapter(user=job.user, project=project)

            # Query model profile context limit (supports 250k profiles)
            model_name = "mock-drafting-v1"
            context_limit = 128000
            model_profile: dict = {}
            if hasattr(adapter, "config") and adapter.config:
                if hasattr(adapter.config, "get_model_for_task"):
                    model_name = adapter.config.get_model_for_task("drafting")
                else:
                    model_name = getattr(adapter.config, "default_drafting_model", "mock-drafting-v1")
                context_limit = adapter.config.get_model_context_limit(model_name)
                profiles = adapter.config.model_profiles
                if isinstance(profiles, dict):
                    model_profile = profiles.get(model_name) or {}

            # Size the output window from the chapter's word target so long
            # chapters are not truncated and short ones do not over-reserve.
            chapter_plan = getattr(chapter, "plan", None)
            target_words = (
                chapter_plan.target_words if chapter_plan else project.target_words_per_chapter
            )
            output_tokens = BudgetCalculator.estimate_output_tokens(
                target_words=target_words,
                max_output=model_profile.get("max_output"),
            )

            # Context assembly with verified model context limit and hybrid retrieval
            ctx_package = ContextAssembler.assemble_chapter_context(
                chapter=chapter,
                model_context_limit=context_limit,
                requested_output_tokens=output_tokens,
                adapter=adapter,
            )

            # Check if job was cancelled while preparing context
            job.refresh_from_db()
            if job.status == JobStatus.CANCELLED:
                logger.info(f"Job {job.id} was cancelled before provider call.")
                return None

            estimated_cost = cls._estimate_reservation_cost(
                input_tokens=ctx_package.total_tokens,
                output_tokens=output_tokens,
                model_profile=model_profile,
            )

            # Validate against configured daily/monthly spending caps
            if hasattr(adapter, "config") and hasattr(adapter.config, "check_budget_limits"):
                allowed, reason = adapter.config.check_budget_limits(
                    estimated_cost_usd=estimated_cost, user=job.user
                )
                if not allowed:
                    job.status = JobStatus.FAILED
                    job.stage = "Spending limit reached"
                    job.error_message = reason
                    job.save(update_fields=["status", "stage", "error_message", "updated_at"])
                    return None

            # Atomic Budget Reservation, derived from the assembled context
            # and the model's pricing profile instead of a fixed guess.
            reservation = BudgetReservation.objects.create(
                user=job.user,
                project_id=project.id,
                job_id=job.id,
                reserved_tokens=min(context_limit, ctx_package.total_tokens + output_tokens),
                reserved_cost_usd=estimated_cost,
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

            # Multi-scene or single-shot drafting
            scenes = list(chapter_plan.scenes.all().order_by("scene_order")) if chapter_plan else []

            try:
                if len(scenes) > 1:
                    scene_drafts = []
                    total_tokens = 0
                    total_cost = Decimal("0.0000")
                    last_provider_id = ""
                    previous_scene_tail = ""

                    for idx, sc in enumerate(scenes, start=1):
                        job.refresh_from_db()
                        if job.status == JobStatus.CANCELLED:
                            logger.info(f"Job {job.id} cancelled during scene {sc.scene_order} generation.")
                            return None

                        job.stage = f"Drafting Scene {sc.scene_order} of {len(scenes)}: {sc.objective[:30]}"
                        job.progress_pct = 30 + int(((idx - 1) / len(scenes)) * 40)
                        job.save(update_fields=["stage", "progress_pct", "updated_at"])

                        sc_words = sc.estimated_words or (target_words // len(scenes))
                        sc_output = BudgetCalculator.estimate_output_tokens(
                            target_words=sc_words,
                            max_output=model_profile.get("max_output"),
                        )

                        scene_prompt_parts = [
                            f"TASK: DRAFT_SCENE (Scene {sc.scene_order} of {len(scenes)})",
                            f"Scene Objective: {sc.objective}",
                        ]
                        if sc.conflict:
                            scene_prompt_parts.append(f"Scene Conflict: {sc.conflict}")
                        if sc.characters:
                            chars_str = ", ".join(sc.characters) if isinstance(sc.characters, list) else str(sc.characters)
                            scene_prompt_parts.append(f"Characters Present: {chars_str}")
                        if sc.setting:
                            scene_prompt_parts.append(f"Setting: {sc.setting}")
                        scene_prompt_parts.append(f"Scene Word Target: ~{sc_words} words")

                        if previous_scene_tail:
                            scene_prompt_parts.append(f"### PREVIOUS SCENE ENDING (continue seamlessly from this moment):\n{previous_scene_tail}")

                        scene_prompt_parts.append(f"\n{ctx_package.user_prompt}")
                        scene_prompt_parts.append(f"\nWrite only the narrative prose for Scene {sc.scene_order}:")
                        scene_user_prompt = "\n".join(scene_prompt_parts)

                        resp = adapter.generate_text(
                            prompt=scene_user_prompt,
                            system_prompt=ctx_package.system_prompt,
                            model=model_name,
                            max_tokens=sc_output,
                        )
                        scene_text = resp.content.strip()
                        scene_drafts.append(scene_text)
                        total_tokens += resp.total_tokens
                        total_cost += resp.cost_usd
                        if resp.raw_metadata.get("id"):
                            last_provider_id = str(resp.raw_metadata["id"])

                        words = scene_text.split()
                        previous_scene_tail = " ".join(words[-200:]) if len(words) > 200 else scene_text

                    composite_prose = "\n\n* * *\n\n".join(scene_drafts)
                else:
                    resp = adapter.generate_text(
                        prompt=ctx_package.user_prompt,
                        system_prompt=ctx_package.system_prompt,
                        model=model_name,
                        max_tokens=output_tokens,
                    )
                    composite_prose = resp.content
                    total_tokens = resp.total_tokens
                    total_cost = resp.cost_usd
                    last_provider_id = str(resp.raw_metadata.get("id", ""))
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
            attempt.provider_request_id = last_provider_id
            attempt.save(update_fields=["outcome", "provider_request_id"])

            # Reconcile budget
            if reservation:
                reservation.reconcile(tokens_used=total_tokens, cost_usd=total_cost)

            # Determine draft version using Max and link parent draft
            max_v = DraftArtifact.objects.filter(chapter=chapter).aggregate(max_v=models.Max("version_number"))["max_v"]
            version_number = (max_v + 1) if max_v else 1
            latest_draft = DraftArtifact.objects.filter(chapter=chapter).order_by("-version_number").first()

            word_count = len(composite_prose.split())
            target_words = chapter_plan.target_words if chapter_plan else project.target_words_per_chapter
            tolerance = chapter_plan.tolerance_percent if chapter_plan else project.tolerance_percent
            min_words = (target_words * (100 - tolerance) + 99) // 100
            max_words = (target_words * (100 + tolerance)) // 100
            length_outside_tolerance = not min_words <= word_count <= max_words

            draft = DraftArtifact.objects.create(
                chapter=chapter,
                version_number=version_number,
                prose_content=composite_prose,
                word_count=word_count,
                model_name=model_name,
                prompt_version="v2-scene-pipeline" if len(scenes) > 1 else "v1",
                context_manifest=ctx_package.manifest,
                parent_draft=latest_draft,
                status=DraftStatus.UNDER_REVIEW,
            )

            quantized_cost = total_cost.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
            job.draft_artifact = draft
            job.confirmed_tokens = total_tokens
            job.confirmed_cost = quantized_cost
            job.save(update_fields=["draft_artifact", "confirmed_tokens", "confirmed_cost", "updated_at"])

            # 3. Transition to CHECKING (Continuity check)
            job.status = JobStatus.CHECKING
            job.stage = "Auditing continuity and world rule integrity"
            job.progress_pct = 75
            job.save(update_fields=["status", "stage", "progress_pct", "updated_at"])

            ContinuityChecker.check_and_persist(
                chapter=chapter,
                prose=composite_prose,
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

            # 4. Extract proposed canon for author review. Non-fatal: an
            # extraction failure must never lose the completed draft.
            job.status = JobStatus.EXTRACTING
            job.stage = "Extracting proposed canon from draft prose"
            job.progress_pct = 88
            job.save(update_fields=["status", "stage", "progress_pct", "updated_at"])
            try:
                proposals = CanonExtractionService.extract_from_draft(
                    chapter=chapter, draft=draft, adapter=adapter
                )
                if not proposals:
                    job.stage = "No canon proposals extracted; draft ready for review"
            except Exception:
                logger.exception("Canon extraction failed for job %s (non-fatal)", job.id)

            # 5. Ready for review
            job.status = JobStatus.READY
            if length_outside_tolerance:
                job.stage = (
                    f"Draft has {word_count} words; target {target_words} ±{tolerance}%. "
                    "Needs author review for length."
                )
            else:
                job.stage = "Generation complete. Ready for author review."
            job.progress_pct = 100
            job.result_url = f"/projects/{project.id}/chapters/{chapter.id}/edit/"
            job.save(update_fields=["status", "stage", "progress_pct", "result_url", "updated_at"])

            chapter.status = Chapter.Status.REVIEW
            chapter.active_draft_id = draft.id
            chapter.current_word_count = word_count
            chapter.save(update_fields=["status", "active_draft_id", "current_word_count", "updated_at"])

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
            heartbeat.stop()
            job.release_lease(worker_id)
