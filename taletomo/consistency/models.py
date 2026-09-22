from django.db import models
from taletomo.core.models import UUIDModel
from taletomo.planning.models import Chapter, Project


class FindingCategory(models.TextChoices):
    IDENTITY = "identity", "Identity & Naming"
    INJURY = "injury", "Injury & Status"
    KNOWLEDGE = "knowledge", "Character Knowledge"
    LOCATION = "location", "Location & Travel"
    TIMELINE = "timeline", "Timeline & Chronology"
    RULE = "rule", "World Rule Violation"
    PLOT = "plot", "Plot Thread & Beat"
    POV = "pov", "POV & Tense Drift"
    TERMINOLOGY = "terminology", "Terminology & Tone"


class FindingSeverity(models.TextChoices):
    BLOCKER = "blocker", "Blocker (Deterministic Contradiction)"
    WARNING = "warning", "Warning (Likely Continuity Issue)"
    ADVISORY = "advisory", "Advisory (Literary / Stylistic Note)"


class FindingStatus(models.TextChoices):
    OPEN = "open", "Open"
    FIXED = "fixed", "Fixed"
    INTENTIONAL = "intentional", "Marked Intentional"
    DISMISSED = "dismissed", "Dismissed"
    SUPERSEDED = "superseded", "Superseded"


class ContinuityFinding(UUIDModel):
    """Evidence-grounded finding detecting possible story contradictions."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="continuity_findings")
    chapter = models.ForeignKey(Chapter, on_delete=models.CASCADE, related_name="continuity_findings")
    draft_id = models.UUIDField(null=True, blank=True)
    category = models.CharField(
        max_length=30, choices=FindingCategory.choices, default=FindingCategory.PLOT
    )
    severity = models.CharField(
        max_length=20, choices=FindingSeverity.choices, default=FindingSeverity.WARNING
    )
    confidence = models.FloatField(default=0.85)
    claim = models.TextField(help_text="The contradictory claim detected in the prose")
    conflicting_evidence = models.JSONField(
        default=list,
        help_text="References to canonical facts, rules, or character states that contradict the claim",
    )
    source_references = models.JSONField(
        default=list, help_text="Specific lines or scene locations in the draft"
    )
    suggested_action = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=20, choices=FindingStatus.choices, default=FindingStatus.OPEN
    )
    override_rationale = models.TextField(
        blank=True, default="", help_text="Author explanation if marked intentional or dismissed"
    )

    class Meta:
        ordering = ["-severity", "created_at"]
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["chapter", "severity"]),
        ]

    def __str__(self):
        return f"[{self.get_severity_display()}] {self.category}: {self.claim[:40]}"
