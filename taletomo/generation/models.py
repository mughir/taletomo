import datetime
from decimal import Decimal
from django.conf import settings
from django.db import models, transaction
from django.utils import timezone
from taletomo.context.models import ContextManifest
from taletomo.core.models import UUIDModel
from taletomo.planning.models import Chapter, Project


class DraftStatus(models.TextChoices):
    GENERATING = "generating", "Generating"
    GENERATED = "generated", "Generated"
    UNDER_REVIEW = "under_review", "Under Review"
    ACCEPTED = "accepted", "Accepted (Prose Approved)"
    REJECTED = "rejected", "Rejected"


class DraftArtifact(UUIDModel):
    """Immutable version of a generated or edited chapter manuscript."""

    chapter = models.ForeignKey(Chapter, on_delete=models.CASCADE, related_name="drafts")
    version_number = models.PositiveIntegerField(default=1)
    prose_content = models.TextField()
    word_count = models.PositiveIntegerField(default=0)
    model_name = models.CharField(max_length=100, default="")
    parameters = models.JSONField(default=dict, blank=True)
    prompt_version = models.CharField(max_length=50, default="v1")
    context_manifest = models.ForeignKey(
        ContextManifest, null=True, blank=True, on_delete=models.SET_NULL, related_name="drafts"
    )
    status = models.CharField(max_length=20, choices=DraftStatus.choices, default=DraftStatus.GENERATED)
    parent_draft = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children"
    )

    class Meta:
        ordering = ["chapter", "-version_number"]
        unique_together = ("chapter", "version_number")

    def __str__(self):
        return f"Ch {self.chapter.chapter_number} Draft v{self.version_number} ({self.status})"


class JobStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    PREPARING_CONTEXT = "preparing_context", "Preparing Context"
    SUBMITTED = "submitted", "Submitted"
    GENERATING = "generating", "Generating"
    CHECKING = "checking", "Checking Continuity"
    READY = "ready", "Ready for Review"
    CANCELLED = "cancelled", "Cancelled"
    FAILED = "failed", "Failed"
    STALE = "stale", "Stale"


class GenerationJob(UUIDModel):
    """Durable asynchronous workflow record with leases and idempotency."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="generation_jobs")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="jobs"
    )
    job_type = models.CharField(max_length=50, default="chapter_draft")
    status = models.CharField(max_length=30, choices=JobStatus.choices, default=JobStatus.QUEUED)
    stage = models.CharField(max_length=100, default="queued")
    idempotency_key = models.CharField(max_length=128, unique=True, db_index=True)

    progress_pct = models.PositiveSmallIntegerField(default=0)
    estimated_cost = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal("0.0000"))
    confirmed_cost = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal("0.0000"))
    confirmed_tokens = models.PositiveIntegerField(default=0)

    # Renewable worker lease
    lease_worker_id = models.CharField(max_length=100, blank=True, null=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)

    result_url = models.CharField(max_length=500, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    error_details = models.JSONField(default=dict, blank=True)

    target_chapter_id = models.UUIDField(null=True, blank=True)
    draft_artifact = models.ForeignKey(
        DraftArtifact, null=True, blank=True, on_delete=models.SET_NULL, related_name="jobs"
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["idempotency_key"]),
        ]

    def acquire_lease(self, worker_id: str, duration_seconds: int = 90) -> bool:
        """Atomically acquires a worker lease using database row lock."""
        with transaction.atomic():
            job = GenerationJob.objects.select_for_update().get(id=self.id)
            now = timezone.now()
            if (
                job.lease_worker_id
                and job.lease_expires_at
                and job.lease_expires_at > now
                and job.lease_worker_id != worker_id
            ):
                return False
            job.lease_worker_id = worker_id
            job.lease_expires_at = now + datetime.timedelta(seconds=duration_seconds)
            job.save(update_fields=["lease_worker_id", "lease_expires_at", "updated_at"])
            self.lease_worker_id = job.lease_worker_id
            self.lease_expires_at = job.lease_expires_at
            return True

    def renew_lease(self, worker_id: str, extension_seconds: int = 90) -> bool:
        """Renews worker lease if currently held by worker."""
        with transaction.atomic():
            job = GenerationJob.objects.select_for_update().get(id=self.id)
            if job.lease_worker_id != worker_id:
                return False
            job.lease_expires_at = timezone.now() + datetime.timedelta(seconds=extension_seconds)
            job.save(update_fields=["lease_expires_at", "updated_at"])
            self.lease_expires_at = job.lease_expires_at
            return True

    def release_lease(self, worker_id: str = None):
        """Releases the worker lease."""
        with transaction.atomic():
            job = GenerationJob.objects.select_for_update().get(id=self.id)
            if worker_id is None or job.lease_worker_id == worker_id:
                job.lease_worker_id = None
                job.lease_expires_at = None
                job.save(update_fields=["lease_worker_id", "lease_expires_at", "updated_at"])
                self.lease_worker_id = None
                self.lease_expires_at = None

    def __str__(self):
        return f"Job {self.id} [{self.job_type}] - {self.status} ({self.progress_pct}%)"


class JobAttempt(UUIDModel):
    """Attempt history per provider call."""

    job = models.ForeignKey(GenerationJob, on_delete=models.CASCADE, related_name="attempts")
    attempt_number = models.PositiveSmallIntegerField(default=1)
    provider_request_id = models.CharField(max_length=150, blank=True, default="")
    outcome = models.CharField(max_length=50, default="started")
    error_details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["attempt_number"]
