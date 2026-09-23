import pytest
from django.contrib.auth import get_user_model
from taletomo.canon.models import Character, WorldRule
from taletomo.consistency.checker import ContinuityChecker
from taletomo.consistency.models import FindingCategory, FindingSeverity
from taletomo.planning.models import Chapter, ChapterPlan, Project
from taletomo.planning.services import PlanningService
from taletomo.providers.adapters import FakeProviderAdapter
from taletomo.providers.models import ProviderConfig, ProviderType

User = get_user_model()


@pytest.mark.django_db
def test_dead_character_mentions_are_review_warnings_not_blockers():
    user = User.objects.create_user(username="flashback_tester")
    project = PlanningService.create_project_with_scaffold(
        owner=user, title="Portrait in the hall", premise="A story remembers the dead"
    )
    chapter = Chapter.objects.get(project=project, chapter_number=1)
    Character.objects.create(project=project, name="Lord Malakar", is_alive=False)

    prose = "Lord Malakar's portrait watched over the empty throne room."
    findings = ContinuityChecker.run_deterministic_checks(chapter, prose)

    identity_findings = [f for f in findings if f.category == FindingCategory.IDENTITY]
    assert not any(f.severity == FindingSeverity.BLOCKER for f in identity_findings)
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
    assert f.severity == FindingSeverity.WARNING
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


@pytest.mark.django_db
def test_non_list_model_critique_response_is_not_silently_treated_as_clean():
    user = User.objects.create_user(username="malformed_critique_author")
    project = PlanningService.create_project_with_scaffold(
        owner=user, title="Malformed critique", premise="A parser error is not a clean report"
    )
    chapter = Chapter.objects.get(project=project, chapter_number=1)
    config = ProviderConfig.objects.create(
        user=user,
        name="Malformed critique fixture",
        provider_type=ProviderType.FAKE,
    )
    adapter = FakeProviderAdapter(config, custom_script={"Analyze continuity and output JSON:": "{\"findings\": []}"})

    findings = ContinuityChecker.run_model_critique(
        adapter=adapter,
        chapter=chapter,
        prose="The protagonist returns to the tower.",
        contract_text="{}",
    )

    assert len(findings) == 1
    assert findings[0].category == FindingCategory.AUTOMATION
    assert findings[0].severity == FindingSeverity.ADVISORY
    assert "could not be fully parsed" in findings[0].claim
