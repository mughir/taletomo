from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from taletomo.core.models import UUIDModel


class ProjectLengthPreset(models.TextChoices):
    SHORT = "short", "Short (800–1,500 words)"
    STANDARD = "standard", "Standard (1,500–2,500 words)"
    LONG = "long", "Long (2,500–4,000 words)"
    CUSTOM = "custom", "Custom (500–10,000 words)"


class ProjectStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    ARCHIVED = "archived", "Archived"
    DELETED = "deleted", "Deleted"


class Project(UUIDModel):
    """Authoritative project representation supporting 1–4,000 chapters."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="projects",
    )
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, blank=True)
    premise = models.TextField(help_text="Original hook or premise")

    target_chapters = models.PositiveIntegerField(
        default=100,
        validators=[MinValueValidator(1), MaxValueValidator(4000)],
        help_text="Target chapters from 1 through 4,000",
    )
    length_preset = models.CharField(
        max_length=20,
        choices=ProjectLengthPreset.choices,
        default=ProjectLengthPreset.STANDARD,
    )
    target_words_per_chapter = models.PositiveIntegerField(default=2200)
    tolerance_percent = models.PositiveSmallIntegerField(default=15)

    # Narrative direction
    genre = models.CharField(max_length=100, default="Fantasy")
    subgenre = models.CharField(max_length=100, blank=True, default="")
    audience = models.CharField(max_length=100, default="Young Adult / General")
    tone = models.CharField(max_length=100, default="Epic, Mysterious")
    language = models.CharField(max_length=50, default="English")
    prose_language = models.CharField(max_length=50, default="English")
    pov = models.CharField(max_length=50, default="Third Person Limited")
    tense = models.CharField(max_length=30, default="Past Tense")
    pacing = models.CharField(max_length=50, default="Balanced")
    protagonist_type = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Protagonist archetype (e.g. Underdog, Anti-hero) from the style dictionary",
    )
    content_boundaries = models.TextField(blank=True, default="")

    active_branch_head = models.CharField(max_length=50, default="rev_1")
    status = models.CharField(
        max_length=20, choices=ProjectStatus.choices, default=ProjectStatus.ACTIVE
    )

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.title} ({self.target_chapters} chs)"


class SeriesBible(UUIDModel):
    """Top-level worldbuilding, genre, and voice constraints (Tomo's Memory)."""

    class Status(models.TextChoices):
        PROPOSED = "proposed", "Proposed"
        APPROVED = "approved", "Approved"

    project = models.OneToOneField(Project, on_delete=models.CASCADE, related_name="bible")
    pitch = models.TextField(blank=True, default="")
    hook = models.TextField(blank=True, default="")
    central_conflict = models.TextField(blank=True, default="")
    reader_promise = models.TextField(blank=True, default="")
    world_setting = models.TextField(blank=True, default="")
    era = models.CharField(max_length=100, blank=True, default="")
    magic_tech_rules = models.JSONField(default=list, blank=True)
    factions_overview = models.TextField(blank=True, default="")
    tone_rules = models.JSONField(default=list, blank=True)
    prose_style_guide = models.TextField(blank=True, default="")
    author_constraints = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.APPROVED)

    def __str__(self):
        return f"Bible for {self.project.title}"


class SeriesSpine(UUIDModel):
    """Sparse long-range series roadmap and irreversible milestones."""

    class Status(models.TextChoices):
        PROPOSED = "proposed", "Proposed"
        APPROVED = "approved", "Approved"

    project = models.OneToOneField(Project, on_delete=models.CASCADE, related_name="spine")
    series_promise = models.TextField(blank=True, default="")
    ending_direction = models.TextField(blank=True, default="")
    major_milestones = models.JSONField(default=list, blank=True)
    sparse_volumes_overview = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.APPROVED)

    def __str__(self):
        return f"Spine for {self.project.title}"


class Volume(UUIDModel):
    """Major publishing/narrative segment."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="volumes")
    volume_number = models.PositiveIntegerField()
    title = models.CharField(max_length=200)
    premise = models.TextField(blank=True, default="")
    target_chapters = models.PositiveIntegerField(default=50)
    established_outcomes = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["volume_number"]
        unique_together = ("project", "volume_number")

    def __str__(self):
        return f"Vol {self.volume_number}: {self.title}"


class Arc(UUIDModel):
    """Operational conflict unit containing chapters."""

    volume = models.ForeignKey(Volume, on_delete=models.CASCADE, related_name="arcs")
    arc_number = models.PositiveIntegerField()
    title = models.CharField(max_length=200)
    conflict_goal = models.TextField(blank=True, default="")
    start_chapter = models.PositiveIntegerField()
    end_chapter = models.PositiveIntegerField()
    target_state_transition = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["arc_number"]
        unique_together = ("volume", "arc_number")

    def __str__(self):
        return f"Arc {self.arc_number}: {self.title} (Ch {self.start_chapter}–{self.end_chapter})"


class Chapter(UUIDModel):
    """A single chapter in the manuscript."""

    class Status(models.TextChoices):
        UNPLANNED = "unplanned", "Unplanned"
        PLANNED = "planned", "Planned"
        DRAFTING = "drafting", "Drafting"
        REVIEW = "review", "Under Review"
        APPROVED = "approved", "Approved"
        LOCKED = "locked", "Locked"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="chapters")
    volume = models.ForeignKey(Volume, null=True, blank=True, on_delete=models.SET_NULL, related_name="chapters")
    arc = models.ForeignKey(Arc, null=True, blank=True, on_delete=models.SET_NULL, related_name="chapters")
    chapter_number = models.PositiveIntegerField(validators=[MinValueValidator(1), MaxValueValidator(4000)])
    title = models.CharField(max_length=200, default="")
    pov_character_name = models.CharField(max_length=100, blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.UNPLANNED)

    # Current accepted prose summary and word count
    current_word_count = models.PositiveIntegerField(default=0)
    current_summary = models.TextField(blank=True, default="")
    active_draft_id = models.UUIDField(null=True, blank=True)

    class Meta:
        ordering = ["chapter_number"]
        unique_together = ("project", "chapter_number")
        indexes = [
            models.Index(fields=["project", "chapter_number"]),
            models.Index(fields=["project", "status"]),
        ]

    def __str__(self):
        return f"Chapter {self.chapter_number}: {self.title or 'Untitled'}"


def default_thread_operations():
    return {"advance": [], "reinforce": [], "open": [], "close": []}


class ChapterPlan(UUIDModel):
    """Specific chapter contract defining requirements, beats, and boundaries."""

    class Status(models.TextChoices):
        PROPOSED = "proposed", "Proposed"
        APPROVED = "approved", "Approved"
        LOCKED = "locked", "Locked"
        SUPERSEDED = "superseded", "Superseded"

    chapter = models.OneToOneField(Chapter, on_delete=models.CASCADE, related_name="plan")
    objectives = models.JSONField(default=list, blank=True)
    required_beats = models.JSONField(default=list, blank=True)
    prohibited_outcomes = models.JSONField(default=list, blank=True)
    continuity_requirements = models.JSONField(default=list, blank=True)
    thread_operations = models.JSONField(
        default=default_thread_operations,
        blank=True,
    )
    end_state = models.JSONField(default=list, blank=True)
    target_words = models.PositiveIntegerField(default=2200)
    tolerance_percent = models.PositiveSmallIntegerField(default=15)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PROPOSED)

    def __str__(self):
        return f"Plan for Ch {self.chapter.chapter_number}"


class ScenePlan(UUIDModel):
    """Breakdown of an individual scene inside a chapter."""

    chapter_plan = models.ForeignKey(ChapterPlan, on_delete=models.CASCADE, related_name="scenes")
    scene_order = models.PositiveSmallIntegerField(default=1)
    objective = models.CharField(max_length=300)
    conflict = models.CharField(max_length=300, blank=True, default="")
    characters = models.JSONField(default=list, blank=True)
    setting = models.CharField(max_length=200, blank=True, default="")
    estimated_words = models.PositiveIntegerField(default=1000)

    class Meta:
        ordering = ["scene_order"]

    def __str__(self):
        return f"Scene {self.scene_order}: {self.objective[:40]}"
