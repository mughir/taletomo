from django.db import models
from taletomo.core.models import UUIDModel
from taletomo.planning.models import Chapter, Project


class CharacterRole(models.TextChoices):
    PROTAGONIST = "protagonist", "Protagonist"
    ANTAGONIST = "antagonist", "Antagonist"
    ALLY = "ally", "Ally"
    NEUTRAL = "neutral", "Neutral / Supporting"


class Character(UUIDModel):
    """Structured character record with wounds, abilities, and internal beliefs."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="characters")
    name = models.CharField(max_length=150, db_index=True)
    aliases = models.JSONField(default=list, blank=True)
    role = models.CharField(
        max_length=30, choices=CharacterRole.choices, default=CharacterRole.NEUTRAL
    )
    traits = models.JSONField(default=list, blank=True)
    goals = models.TextField(blank=True, default="")
    internal_need = models.TextField(blank=True, default="")
    wounds_status = models.TextField(
        blank=True, default="", help_text="Current physical wounds, injuries, or status impairments"
    )
    is_alive = models.BooleanField(default=True)
    beliefs = models.JSONField(
        default=list,
        blank=True,
        help_text="What this character believes is true vs world truth",
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["project", "name"])]

    def __str__(self):
        return f"{self.name} ({self.get_role_display()})"


class Location(UUIDModel):
    """World location, geographic hierarchy, and travel rules."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="locations")
    name = models.CharField(max_length=150, db_index=True)
    description = models.TextField(blank=True, default="")
    travel_rules = models.TextField(blank=True, default="")
    current_state = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["project", "name"])]

    def __str__(self):
        return self.name


class Faction(UUIDModel):
    """Faction, house, or political force."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="factions")
    name = models.CharField(max_length=150)
    goals = models.TextField(blank=True, default="")
    resources = models.TextField(blank=True, default="")
    members = models.JSONField(default=list, blank=True)
    alliances = models.JSONField(default=list, blank=True)

    def __str__(self):
        return self.name


class WorldRule(UUIDModel):
    """Magic, tech, physical, or social constraints that cannot be broken."""

    class Category(models.TextChoices):
        MAGIC = "magic", "Magic System"
        TECH = "tech", "Technology / Science"
        SOCIAL = "social", "Social / Cultural"
        PHYSICAL = "physical", "Physical / Environmental"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="rules")
    category = models.CharField(
        max_length=30, choices=Category.choices, default=Category.MAGIC
    )
    title = models.CharField(max_length=200)
    rule_statement = models.TextField()
    forbidden_violations = models.TextField(
        blank=True, default="", help_text="Explicitly prohibited outcomes"
    )

    def __str__(self):
        return f"[{self.get_category_display()}] {self.title}"


class TimelineEvent(UUIDModel):
    """Chronologically ordered historical and narrative events."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="timeline_events")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    story_time_valid_from = models.CharField(max_length=100, blank=True, default="")
    story_time_valid_until = models.CharField(max_length=100, blank=True, default="")
    real_order = models.PositiveIntegerField(default=1)
    is_canon = models.BooleanField(default=True)

    class Meta:
        ordering = ["real_order"]

    def __str__(self):
        return f"#{self.real_order} {self.title}"


class PlotThread(UUIDModel):
    """Tracks narrative promises, mysteries, debts, and payoffs."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        PROGRESSING = "progressing", "Progressing"
        RESOLVED = "resolved", "Resolved"
        ABANDONED = "abandoned", "Abandoned"

    class Category(models.TextChoices):
        PROMISE = "promise", "Dramatic Promise"
        MYSTERY = "mystery", "Mystery / Clue"
        DEBT = "debt", "Personal Debt / Obligation"
        DEADLINE = "deadline", "Impending Deadline"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="plot_threads")
    title = models.CharField(max_length=200)
    category = models.CharField(
        max_length=30, choices=Category.choices, default=Category.PROMISE
    )
    status = models.CharField(
        max_length=30, choices=Status.choices, default=Status.OPEN
    )
    setup_chapter = models.PositiveIntegerField(default=1)
    payoff_chapter = models.PositiveIntegerField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["setup_chapter", "title"]

    def __str__(self):
        return f"[{self.get_status_display()}] {self.title}"


class TruthScope(models.TextChoices):
    WORLD_TRUTH = "world_truth", "World Truth (Objective)"
    NARRATOR_ASSERTION = "narrator_assertion", "Narrator Assertion"
    CHARACTER_BELIEF = "character_belief", "Character Belief"
    RUMOR = "rumor", "Rumor / Unconfirmed"
    PROPHECY = "prophecy", "Prophecy"
    PLAN_ONLY = "plan_only", "Plan Only (Future Intent)"
    AUTHOR_NOTE = "author_note", "Author Meta-Note"
    NONCANONICAL_DRAFT = "noncanonical_draft", "Noncanonical Draft Claim"


class CanonFact(UUIDModel):
    """Granular machine-checkable fact with temporal scope and provenance."""

    class Status(models.TextChoices):
        PROPOSED = "proposed", "Proposed"
        CONFIRMED = "confirmed", "Confirmed"
        DEPRECATED = "deprecated", "Deprecated"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="canon_facts")
    subject = models.CharField(max_length=150, db_index=True)
    predicate = models.CharField(max_length=100, db_index=True)
    value = models.TextField()
    truth_scope = models.CharField(
        max_length=40, choices=TruthScope.choices, default=TruthScope.WORLD_TRUTH
    )
    story_time_valid_from = models.CharField(max_length=100, blank=True, default="")
    story_time_valid_until = models.CharField(max_length=100, blank=True, default="")
    revision_valid_from = models.CharField(max_length=50, default="rev_1")
    revision_valid_until = models.CharField(max_length=50, blank=True, default="")
    provenance = models.CharField(max_length=200, blank=True, default="")
    confidence = models.FloatField(default=1.0)
    canonical_status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.CONFIRMED
    )

    class Meta:
        indexes = [
            models.Index(fields=["project", "canonical_status"]),
            models.Index(fields=["project", "subject", "predicate"]),
        ]

    def __str__(self):
        return f"{self.subject} -> {self.predicate}: {self.value[:30]} ({self.truth_scope})"


class StoryEvent(UUIDModel):
    """Immutable state change produced by committed prose."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="story_events")
    chapter = models.ForeignKey(Chapter, on_delete=models.CASCADE, related_name="story_events")
    event_type = models.CharField(max_length=100, default="plot_progress")
    summary = models.TextField()
    payload = models.JSONField(default=dict, blank=True)
    committed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["committed_at"]

    def __str__(self):
        return f"Event in Ch {self.chapter.chapter_number}: {self.summary[:50]}"


class StorySnapshot(UUIDModel):
    """Materialized canonical state at an exact project revision."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="snapshots")
    revision = models.CharField(max_length=50, db_index=True)
    materialized_state = models.JSONField(default=dict)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Snapshot {self.revision} for {self.project.title}"
