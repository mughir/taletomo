from django.core.exceptions import ValidationError
from django.db import models
from taletomo.core.models import UUIDModel
from taletomo.generation.models import DraftArtifact
from taletomo.planning.models import Chapter, Project


class CharacterRole(models.TextChoices):
    PROTAGONIST = "protagonist", "Protagonist"
    ANTAGONIST = "antagonist", "Antagonist"
    ALLY = "ally", "Ally"
    NEUTRAL = "neutral", "Neutral / Supporting"


def compute_cosine_similarity(v1: list[float], v2: list[float]) -> float:
    """Computes cosine similarity between two vector embeddings."""
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    norm_a = sum(a * a for a in v1) ** 0.5
    norm_b = sum(b * b for b in v2) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(dot / (norm_a * norm_b))


_cosine_similarity = compute_cosine_similarity


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
    appearance = models.TextField(
        blank=True, default="", help_text="Physical appearance, distinctive features, clothing, and presence"
    )
    dialogue_style = models.TextField(
        blank=True, default="", help_text="Speech patterns, vocabulary, dialect, cadence, and typical idioms"
    )
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
    embedding = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["project", "name"])]

    def __str__(self):
        return f"{self.name} ({self.get_role_display()})"

    def get_name_variants(self) -> list[str]:
        """Resolves character name variants: full name, first-token (if >= 3 chars), and aliases."""
        variants: list[str] = []
        if self.name and self.name.strip():
            name_clean = self.name.strip()
            variants.append(name_clean)
            for token in name_clean.split():
                if len(token) >= 3 and token.lower() != name_clean.lower():
                    variants.append(token)
        for alias in (self.aliases or []):
            alias_clean = str(alias).strip()
            if len(alias_clean) >= 2:
                variants.append(alias_clean)
        seen = set()
        deduped: list[str] = []
        for v in variants:
            low = v.lower()
            if low not in seen:
                seen.add(low)
                deduped.append(v)
        return deduped

    def clean(self):
        super().clean()
        if self.wounds_status:
            cleaned = self.wounds_status.strip().lower()
            if cleaned in ("none", "healed", "cured", "healthy", "normal", "uninjured", "no wounds", "nil", "n/a"):
                self.wounds_status = ""

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class CharacterRelationship(UUIDModel):
    """Dynamic relationship, interpersonal history, and social tension between two characters."""

    class DynamicStatus(models.TextChoices):
        FRIENDLY = "friendly", "Friendly / Cooperative"
        HOSTILE = "hostile", "Hostile / Antagonistic"
        TENSE = "tense", "Tense / Suspicious"
        NEUTRAL = "neutral", "Neutral / Professional"
        COMPLEX = "complex", "Complex / Shifting"

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="character_relationships"
    )
    source_character = models.ForeignKey(
        Character, on_delete=models.CASCADE, related_name="relationships_from"
    )
    target_character = models.ForeignKey(
        Character, on_delete=models.CASCADE, related_name="relationships_to"
    )
    relationship_type = models.CharField(
        max_length=60, help_text="e.g. Rival, Mentor, Ally, Sibling, Romantic, Estranged, Debtor"
    )
    description = models.TextField(blank=True, default="")
    dynamic_status = models.CharField(
        max_length=40,
        choices=DynamicStatus.choices,
        default=DynamicStatus.NEUTRAL,
    )

    class Meta:
        ordering = ["source_character__name", "target_character__name"]
        unique_together = ("source_character", "target_character")

    def __str__(self):
        return f"{self.source_character.name} -> {self.relationship_type} -> {self.target_character.name}"

    def clean(self):
        super().clean()
        if (
            self.source_character_id
            and self.target_character_id
            and self.source_character_id == self.target_character_id
        ):
            raise ValidationError("A character cannot have a relationship with themselves.")

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class Location(UUIDModel):
    """World location, geographic hierarchy, and travel rules."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="locations")
    name = models.CharField(max_length=150, db_index=True)
    description = models.TextField(blank=True, default="")
    travel_rules = models.TextField(blank=True, default="")
    current_state = models.TextField(blank=True, default="")
    coord_x = models.FloatField(default=0.0)
    coord_y = models.FloatField(default=0.0)
    region = models.CharField(max_length=120, blank=True, default="")
    embedding = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["project", "name"])]

    def __str__(self):
        return self.name

    def distance_to(self, other: "Location") -> float:
        import math

        return math.hypot(self.coord_x - other.coord_x, self.coord_y - other.coord_y)


class Item(UUIDModel):
    """Significant story artifacts, weapons, keys, or relics tracked across the narrative."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="items")
    name = models.CharField(max_length=150, db_index=True)
    description = models.TextField(blank=True, default="")
    current_holder = models.ForeignKey(
        Character, null=True, blank=True, on_delete=models.SET_NULL, related_name="held_items"
    )
    current_location = models.ForeignKey(
        Location, null=True, blank=True, on_delete=models.SET_NULL, related_name="stored_items"
    )
    is_destroyed = models.BooleanField(default=False)
    destroyed_at_chapter = models.PositiveIntegerField(null=True, blank=True)
    plot_thread = models.ForeignKey(
        "PlotThread",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="linked_items",
        help_text="Plot thread or mystery this artifact is tied to",
    )
    status_notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["project", "name"])]

    def __str__(self):
        status = " (Destroyed)" if self.is_destroyed else ""
        return f"{self.name}{status}"



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

    @property
    def story_time(self) -> str:
        return self.story_time_valid_from


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
    last_mentioned_chapter = models.PositiveIntegerField(default=1)
    dormancy_threshold = models.PositiveIntegerField(
        default=10,
        help_text="Number of chapters before an unmentioned thread is flagged as dormant",
    )
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["setup_chapter", "title"]

    def __str__(self):
        return f"[{self.get_status_display()}] {self.title}"

    @property
    def resolution_chapter(self):
        return self.payoff_chapter

    def is_dormant(self, current_chapter_number: int) -> bool:
        """Determines if the thread has not been mentioned for more than the dormancy threshold."""
        if self.status not in [self.Status.OPEN, self.Status.PROGRESSING]:
            return False
        return (current_chapter_number - self.last_mentioned_chapter) >= self.dormancy_threshold

    def chapters_since_mention(self, current_chapter_number: int) -> int:
        return max(0, current_chapter_number - self.last_mentioned_chapter)


class PlotThreadBreadcrumb(UUIDModel):
    """Foreshadowing clue, progression beat, or payoff milestone linked to a narrative thread."""

    class BreadcrumbType(models.TextChoices):
        CLUE = "clue", "Foreshadowing / Clue"
        PROGRESSION = "progression", "Progress / Escalation"
        TWIST = "twist", "Revelation / Complication"
        PAYOFF = "payoff", "Climax / Payoff"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="thread_breadcrumbs")
    plot_thread = models.ForeignKey(PlotThread, on_delete=models.CASCADE, related_name="breadcrumbs")
    chapter_number = models.PositiveIntegerField(default=1)
    breadcrumb_type = models.CharField(
        max_length=30, choices=BreadcrumbType.choices, default=BreadcrumbType.CLUE
    )
    description = models.TextField()
    is_discovered = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["chapter_number", "created_at"]

    def __str__(self):
        return f"Ch {self.chapter_number} [{self.get_breadcrumb_type_display()}]: {self.plot_thread.title}"


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
    embedding = models.JSONField(default=list, blank=True)

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
    embedding = models.JSONField(default=list, blank=True)

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


class ProposedCanonItem(UUIDModel):
    """Author-reviewable canon proposal extracted from prose.

    Extraction runs after drafting; nothing here touches canonical state until
    the author approves it and commits the chapter. Approving flows into the
    canon commit as facts, events, and plot-thread updates; committing marks
    consumed items so the review trail stays auditable.
    """

    class Kind(models.TextChoices):
        FACT = "fact", "Canon Fact"
        EVENT = "event", "Story Event"
        THREAD_UPDATE = "thread_update", "Plot Thread Update"
        CHARACTER_UPDATE = "character_update", "Character Update"

    class Status(models.TextChoices):
        PROPOSED = "proposed", "Proposed"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CONSUMED = "consumed", "Consumed by Canon Commit"

    # Fields a character update may legally touch: the ones the continuity
    # checker and context assembly actually consume.
    APPLICABLE_CHARACTER_FIELDS = ("wounds_status", "is_alive", "goals", "appearance")

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="proposed_canon_items")
    chapter = models.ForeignKey(Chapter, on_delete=models.CASCADE, related_name="proposed_canon_items")
    draft = models.ForeignKey(
        DraftArtifact, null=True, blank=True, on_delete=models.SET_NULL, related_name="proposed_canon_items"
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    payload = models.JSONField(default=dict, help_text="Structured proposal; shape depends on kind")
    summary = models.CharField(max_length=300, blank=True, default="")
    confidence = models.FloatField(default=0.8)
    extracted_by_model = models.CharField(max_length=100, blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PROPOSED)
    review_note = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["chapter", "status"]),
            models.Index(fields=["project", "status"]),
        ]

    def __str__(self):
        return f"[{self.get_kind_display()}] {self.summary[:60]} ({self.status})"


def find_project_character(project, name: str):
    """Resolves a cast member by name: exact, alias, or first-token match (case-insensitive)."""
    if not name or not str(name).strip():
        return None
    candidate = str(name).strip().lower()
    if len(candidate) < 3:
        return None
    for char in project.characters.all():
        if candidate in (v.lower() for v in char.get_name_variants()):
            return char
    return None


def parse_alive_value(value):
    """Parses prose-ish alive/dead wording into a boolean, or None when unclear."""
    if isinstance(value, bool):
        return value
    lowered = str(value).strip().lower()
    if lowered in ("alive", "true", "yes", "1", "living", "resurrected", "revived"):
        return True
    if lowered in ("dead", "false", "no", "0", "deceased", "slain", "killed", "dies", "died"):
        return False
    return None
