import pytest
from django.contrib.auth import get_user_model
from taletomo.canon.models import Character, WorldRule
from taletomo.consistency.checker import ContinuityChecker
from taletomo.consistency.models import FindingCategory, FindingSeverity
from taletomo.planning.models import Chapter, ChapterPlan, Project
from taletomo.planning.services import PlanningService

User = get_user_model()


@pytest.mark.django_db
def test_continuity_detects_dead_character():
    user = User.objects.create(username="continuity_tester")
    project = PlanningService.create_project_with_scaffold(
        owner=user, title="Dead Character Test", premise="Testing ghost detection."
    )
    ch1 = Chapter.objects.get(project=project, chapter_number=1)

    Character.objects.create(
        project=project,
        name="Lord Malakar",
        is_alive=False,
    )

    bad_prose = "Lord Malakar walked across the throne room and laughed out loud."
    findings = ContinuityChecker.run_deterministic_checks(ch1, bad_prose)

    assert len(findings) >= 1
    f = next(f for f in findings if f.category == FindingCategory.IDENTITY)
    assert f.severity == FindingSeverity.BLOCKER
    assert "Lord Malakar" in f.claim


@pytest.mark.django_db
def test_continuity_detects_injured_limb():
    user = User.objects.create(username="injury_tester")
    project = PlanningService.create_project_with_scaffold(
        owner=user, title="Injury Test", premise="Testing injury invariants."
    )
    ch1 = Chapter.objects.get(project=project, chapter_number=1)

    Character.objects.create(
        project=project,
        name="Alaric",
        wounds_status="Left hand shattered and bandaged",
    )

    contradictory_prose = "Alaric climbed using both hands, pulling himself over the high wall."
    findings = ContinuityChecker.run_deterministic_checks(ch1, contradictory_prose)

    assert len(findings) >= 1
    f = next(f for f in findings if f.category == FindingCategory.INJURY)
    assert f.severity == FindingSeverity.BLOCKER
    assert "Alaric" in f.claim
