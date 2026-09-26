"""Recovery for generation jobs abandoned by dead workers.

A worker that dies (OOM, redeploy, network partition) leaves its job in an
in-flight status with an expired lease. The reaper classifies each stale job
by its last provider attempt and applies the billing-safe policy:

- No attempt recorded: the provider was never called — requeue automatically.
- Attempt in flight or outcome unknown: the provider may have billed — mark
  FAILED with ``unknown_outcome`` and hold the budget reservation, mirroring
  the provider-timeout policy. Never requeued automatically.
- Attempt completed: the provider call succeeded — mark FAILED for manual
  retry; automatic re-execution would spend money again without consent.

Queued jobs whose dispatch was likely lost (no lease, untouched past the
grace window) are re-dispatched; the lease makes duplicate dispatch safe.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from taletomo.generation.models import GenerationJob, JobStatus
from taletomo.generation.tasks import generate_chapter_task

logger = logging.getLogger(__name__)

# Statuses that mean a worker should be actively executing the job.
IN_FLIGHT_STATUSES = (
    JobStatus.PREPARING_CONTEXT,
    JobStatus.SUBMITTED,
    JobStatus.GENERATING,
    JobStatus.CHECKING,
    JobStatus.EXTRACTING,
)

DEFAULT_QUEUED_GRACE_SECONDS = 900


@dataclass
class ReapResult:
    requeued: int = 0
    failed_unknown_outcome: int = 0
    failed_post_provider: int = 0
    skipped: int = 0

    @property
    def acted(self) -> int:
        return self.requeued + self.failed_unknown_outcome + self.failed_post_provider


def _requeue_job(job_id, allowed_statuses) -> bool:
    """Resets a stale job to QUEUED and re-dispatches it on commit."""
    with transaction.atomic():
        locked = GenerationJob.objects.select_for_update().filter(id=job_id).first()
        if locked is None or locked.status not in allowed_statuses:
            return False
        locked.status = JobStatus.QUEUED
        locked.stage = "Requeued: worker lease expired before any provider submission"
        locked.progress_pct = 0
        locked.lease_worker_id = None
        locked.lease_expires_at = None
        locked.save(
            update_fields=[
                "status",
                "stage",
                "progress_pct",
                "lease_worker_id",
                "lease_expires_at",
                "updated_at",
            ]
        )
        transaction.on_commit(lambda: generate_chapter_task.delay(str(locked.id)))
    return True


def _fail_stale_job(job_id, *, unknown_outcome: bool) -> bool:
    """Fails a stale in-flight job; unknown provider outcomes keep their reservation held."""
    with transaction.atomic():
        locked = GenerationJob.objects.select_for_update().filter(id=job_id).first()
        if locked is None or locked.status not in IN_FLIGHT_STATUSES:
            return False
        locked.status = JobStatus.FAILED
        locked.lease_worker_id = None
        locked.lease_expires_at = None
        if unknown_outcome:
            locked.stage = "Worker lease expired during the provider call; outcome unknown"
            locked.error_message = (
                "The worker stopped responding mid-provider-call. Billing status is unverified; "
                "reconcile with the provider before retrying."
            )
            locked.error_details = {
                "unknown_outcome": True,
                "billing_status": "unverified",
                "reason": "worker_lease_expired",
            }
        else:
            locked.stage = "Worker lease expired after the provider call completed"
            locked.error_message = (
                "The worker died after the provider call succeeded. Retry manually if the "
                "completed draft was not saved."
            )
            locked.error_details = {"unknown_outcome": False, "reason": "worker_lease_expired_post_provider"}
        locked.save(
            update_fields=[
                "status",
                "stage",
                "error_message",
                "error_details",
                "lease_worker_id",
                "lease_expires_at",
                "updated_at",
            ]
        )
    # Budget reservations are intentionally left RESERVED for unknown outcomes;
    # releasing them would allow blind retries while the provider may have billed.
    return True


def reap_stale_jobs(now=None, queued_grace_seconds: Optional[int] = None) -> ReapResult:
    result = ReapResult()
    now = now or timezone.now()
    if queued_grace_seconds is None:
        queued_grace_seconds = getattr(
            settings, "TALETOMO_REAPER_QUEUED_GRACE_SECONDS", DEFAULT_QUEUED_GRACE_SECONDS
        )

    # 1. In-flight jobs whose lease expired or was never held (dead worker).
    stale_inflight = GenerationJob.objects.filter(status__in=IN_FLIGHT_STATUSES).filter(
        lease_expires_at__lt=now
    ) | GenerationJob.objects.filter(status__in=IN_FLIGHT_STATUSES, lease_expires_at__isnull=True)

    for job in stale_inflight.distinct():
        last_attempt = job.attempts.order_by("-attempt_number").first()
        if last_attempt is None:
            if _requeue_job(job.id, IN_FLIGHT_STATUSES):
                result.requeued += 1
            else:
                result.skipped += 1
        elif last_attempt.outcome == "completed":
            if _fail_stale_job(job.id, unknown_outcome=False):
                result.failed_post_provider += 1
            else:
                result.skipped += 1
        else:
            # "started", "unknown_provider_outcome", or "failed": the provider
            # may have been reached and billed, so never auto-requeue.
            if _fail_stale_job(job.id, unknown_outcome=True):
                result.failed_unknown_outcome += 1
            else:
                result.skipped += 1

    # 2. Queued jobs whose broker dispatch was likely lost (untouched past grace).
    grace_cutoff = now - timedelta(seconds=queued_grace_seconds)
    stale_queued = GenerationJob.objects.filter(
        status=JobStatus.QUEUED,
        lease_worker_id__isnull=True,
        updated_at__lt=grace_cutoff,
    )
    for job in stale_queued:
        if _requeue_job(job.id, (JobStatus.QUEUED,)):
            result.requeued += 1
        else:
            result.skipped += 1

    return result
