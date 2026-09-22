from django.db import models
from taletomo.core.models import UUIDModel
from taletomo.planning.models import Chapter, Project


class ContextManifest(UUIDModel):
    """Persisted record of the exact sources, budgets, and tokens assembled for a generation request."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="manifests")
    chapter = models.ForeignKey(Chapter, on_delete=models.CASCADE, related_name="manifests")
    model_context_limit = models.PositiveIntegerField()
    requested_output_tokens = models.PositiveIntegerField()
    usable_budget = models.PositiveIntegerField()
    category_budgets = models.JSONField(default=dict)
    source_entries = models.JSONField(
        default=list,
        help_text="List of source items with id, version, priority, token_count, category, and hash",
    )
    total_assembled_tokens = models.PositiveIntegerField(default=0)
    manifest_hash = models.CharField(max_length=64, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Manifest for Ch {self.chapter.chapter_number} ({self.total_assembled_tokens} tokens)"
