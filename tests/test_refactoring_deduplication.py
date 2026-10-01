import pytest
from taletomo.canon.models import (
    Character,
    CharacterRole,
    compute_cosine_similarity,
    find_project_character,
)
from taletomo.context.budget import TokenBudget
from taletomo.context.retrieval import ContextBudgetTracker
from taletomo.planning.models import Project, SeriesBible, SeriesSpine, Volume, Arc, Chapter
from taletomo.planning.services import PlanningService
from django.contrib.auth import get_user_model

User = get_user_model()


@pytest.mark.django_db
def test_character_name_variants_and_resolution():
    user = User.objects.create_user(username="testrefactor", password="password")
    project = Project.objects.create(owner=user, title="Refactor Project", slug="refactor-project")

    char = Character.objects.create(
        project=project,
        name="Seraphina Nightshade",
        aliases=["The Shadow Queen", "Sera"],
        role=CharacterRole.PROTAGONIST,
    )

    variants = char.get_name_variants()
    assert "Seraphina Nightshade" in variants
    assert "Seraphina" in variants
    assert "The Shadow Queen" in variants
    assert "Sera" in variants

    # Resolves via full name
    assert find_project_character(project, "Seraphina Nightshade") == char
    # Resolves via alias
    assert find_project_character(project, "the shadow queen") == char
    assert find_project_character(project, "Sera") == char
    # Resolves via first token
    assert find_project_character(project, "Seraphina") == char
    # Non-existent character
    assert find_project_character(project, "Unknown Stranger") is None


def test_cosine_similarity_edge_cases():
    # Identical vectors
    v1 = [1.0, 0.0, 0.0]
    assert pytest.approx(compute_cosine_similarity(v1, v1), 0.001) == 1.0

    # Orthogonal vectors
    v2 = [0.0, 1.0, 0.0]
    assert compute_cosine_similarity(v1, v2) == 0.0

    # Empty / mismatched length
    assert compute_cosine_similarity([], [1.0]) == 0.0
    assert compute_cosine_similarity([1.0, 2.0], [1.0]) == 0.0

    # Zero norm
    assert compute_cosine_similarity([0.0, 0.0], [0.0, 0.0]) == 0.0


def test_context_budget_tracker_enforcement():
    from taletomo.context.budget import BudgetCalculator

    budget = BudgetCalculator.calculate_budget(model_context_limit=4000, requested_output_tokens=1000)
    tracker = ContextBudgetTracker(budget=budget, model_context_limit=4000)

    # Valid entry within budget
    res = tracker.record_entry("c-1", "state", "Short description of an entity.")
    assert res is not None
    assert tracker.category_spent["state"] > 0
    assert len(tracker.source_entries) == 1

    # Mandatory category overflow raises ValueError
    huge_text = "word " * 500
    with pytest.raises(ValueError, match="Mandatory category 'constraints'"):
        tracker.record_entry("b-1", "constraints", huge_text)


@pytest.mark.django_db
def test_decomposed_branching_helpers():
    user = User.objects.create_user(username="branchuser", password="password")
    project = Project.objects.create(
        owner=user,
        title="Epic Novel",
        slug="epic-novel",
        target_chapters=10,
        target_words_per_chapter=2000,
    )
    SeriesBible.objects.create(
        project=project,
        pitch="Epic pitch",
        world_setting="Fantasy realm",
    )
    SeriesSpine.objects.create(
        project=project,
        series_promise="Hero defeats dragon",
    )
    vol1 = Volume.objects.create(project=project, volume_number=1, title="Volume 1")
    Arc.objects.create(
        volume=vol1,
        arc_number=1,
        title="Arc 1",
        start_chapter=1,
        end_chapter=5,
    )
    Chapter.objects.create(
        project=project,
        volume=vol1,
        chapter_number=1,
        title="Chapter 1",
        status=Chapter.Status.APPROVED,
    )

    branched_project = Project.objects.create(
        owner=user,
        title="Epic Novel (What-If)",
        slug="epic-novel-what-if",
        target_chapters=10,
        parent_project=project,
        branch_point_chapter=1,
        branch_name="What-If",
    )

    # Test individual decomposed helper methods
    PlanningService._clone_bible(project, branched_project)
    assert hasattr(branched_project, "bible")
    assert branched_project.bible.pitch == "Epic pitch"

    PlanningService._clone_spine(project, branched_project)
    assert hasattr(branched_project, "spine")
    assert branched_project.spine.series_promise == "Hero defeats dragon"

    vol_map, arc_map = PlanningService._clone_volumes_and_arcs(project, branched_project)
    assert len(vol_map) == 1
    assert len(arc_map) == 1
    assert branched_project.volumes.count() == 1
