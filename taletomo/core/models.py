import uuid
from django.conf import settings
from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class UUIDModel(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class AuditLog(models.Model):
    """Append-only audit trail for domain actions and canon transitions."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project_id = models.UUIDField(null=True, blank=True, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
    )
    action = models.CharField(max_length=100, db_index=True)
    target_type = models.CharField(max_length=100, db_index=True)
    target_id = models.CharField(max_length=100, db_index=True)
    old_version = models.CharField(max_length=50, blank=True, default="")
    new_version = models.CharField(max_length=50, blank=True, default="")
    reason = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["project_id", "timestamp"]),
            models.Index(fields=["target_type", "target_id"]),
        ]

    def __str__(self):
        return f"[{self.timestamp.isoformat()}] {self.action} on {self.target_type}:{self.target_id}"
