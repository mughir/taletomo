import re

from django.conf import settings
from django.db import models
from django.utils.text import slugify

from taletomo.core.models import UUIDModel


class StyleField(models.TextChoices):
    """The project style axes backed by the dictionary."""

    GENRE = "genre", "Genre"
    SUBGENRE = "subgenre", "Subgenre"
    TONE = "tone", "Tone"
    POV = "pov", "Point of View"
    TENSE = "tense", "Narrative Tense"
    PACING = "pacing", "Pacing"


class StyleTerm(UUIDModel):
    """One dictionary entry: a style term with its definition and example.

    Builtin terms are seeded reference data every author sees; authors can
    add their own terms. During generation, the terms matching the project's
    chosen genre/subgenre/tone/pov/tense/pacing values are resolved and their
    definitions and examples ride into the drafting prompt, so "Xianxia" or a
    house-invented genre means the same thing to the model as to the author.
    """

    field = models.CharField(max_length=20, choices=StyleField.choices, db_index=True)
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120, help_text="Case-insensitive identity within the field")
    definition = models.TextField()
    example = models.TextField(blank=True, default="")
    is_builtin = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="style_terms",
    )

    class Meta:
        ordering = ["field", "name"]
        constraints = [
            models.UniqueConstraint(fields=["field", "slug"], name="unique_style_term_per_field")
        ]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:120]
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_field_display()} — {self.name}"

    @classmethod
    def visible_to(cls, user):
        """Builtin terms plus the user's own additions."""
        if user is None or not getattr(user, "is_authenticated", False):
            return cls.objects.filter(is_builtin=True)
        return cls.objects.filter(models.Q(is_builtin=True) | models.Q(created_by=user))

    @classmethod
    def resolve_for_project(cls, project, max_terms: int = 10) -> list:
        """Resolves the project's style values to visible dictionary terms.

        Style values are free text that may hold several choices ("Grim,
        Mysterious" or "Fantasy / Xianxia"), so each comma/slash-separated
        token is matched case-insensitively. Unmatched tokens simply do not
        contribute — custom typing always stays valid.
        """
        terms = cls.objects.filter(
            models.Q(is_builtin=True) | models.Q(created_by=project.owner)
        )
        by_key = {}
        for term in terms:
            by_key.setdefault((term.field, term.name.lower()), term)

        resolved = []
        seen = set()
        for field_value, raw in (
            (StyleField.GENRE, project.genre),
            (StyleField.SUBGENRE, project.subgenre),
            (StyleField.TONE, project.tone),
            (StyleField.POV, project.pov),
            (StyleField.TENSE, project.tense),
            (StyleField.PACING, project.pacing),
        ):
            if not raw:
                continue
            for part in re.split(r"[,;/]", raw):
                part = part.strip()
                if not part:
                    continue
                term = by_key.get((field_value, part.lower()))
                if term is not None and term.pk not in seen:
                    seen.add(term.pk)
                    resolved.append(term)
        return resolved[:max_terms]
