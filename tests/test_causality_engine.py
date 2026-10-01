import pytest
from django.contrib.auth import get_user_model
from taletomo.canon.models import CanonFact, Character, Item, Location
from taletomo.consistency.checker import ContinuityChecker
from taletomo.consistency.models import FindingCategory, FindingSeverity
from taletomo.planning.models import Chapter
from taletomo.planning.services import PlanningService

User = get_user_model()


@pytest.fixture
def causality_project(db):
    user = User.objects.create_user(username="causality_author", password="password123")
    project = PlanningService.create_project_with_scaffold(
        owner=user,
        title="Chronicles of Distance and Relics",
        premise="A journey across the scorched frontier.",
        target_chapters=5,
    )

    loc1 = Location.objects.create(
        project=project,
        name="Sunken Citadel",
        coord_x=0.0,
        coord_y=0.0,
        region="Southern Basin",
    )
    loc2 = Location.objects.create(
        project=project,
        name="Frostpeak Fortress",
        coord_x=300.0,
        coord_y=400.0,  # 500 leagues away
        region="Northern Mountains",
    )

    alaric = Character.objects.create(
        project=project,
        name="Alaric",
        role="Protagonist",
    )

    # Prior confirmed canon location
    CanonFact.objects.create(
        project=project,
        subject="Alaric",
        predicate="current_location",
        value="Sunken Citadel",
        canonical_status=CanonFact.Status.CONFIRMED,
    )

    # Significant relic item destroyed in Chapter 1
    relic = Item.objects.create(
        project=project,
        name="Solar Crest",
        is_destroyed=True,
        destroyed_at_chapter=1,
        status_notes="Shattered in the core eruption",
    )

    ch2 = Chapter.objects.get(project=project, chapter_number=2)

    return project, ch2, loc1, loc2, alaric, relic


@pytest.mark.django_db
def test_spatial_distance_and_instantaneous_travel_flagged(causality_project):
    project, ch2, loc1, loc2, alaric, _ = causality_project

    # Test Euclidean distance method on Location
    dist = loc1.distance_to(loc2)
    assert dist == 500.0

    # Prose places Alaric at Frostpeak Fortress with zero transit narration
    prose = "Alaric sat inside Frostpeak Fortress, drinking warm tea and plotting his revenge."
    findings = ContinuityChecker.run_deterministic_checks(ch2, prose)

    location_findings = [f for f in findings if f.category == FindingCategory.LOCATION]
    assert len(location_findings) == 1
    f = location_findings[0]
    assert "Spatial causality" in f.claim
    assert "500.0 leagues" in f.claim
    assert "Frostpeak Fortress" in f.claim
    assert "Sunken Citadel" in f.claim
    assert f.severity == FindingSeverity.WARNING


@pytest.mark.django_db
def test_spatial_travel_with_transit_narrative_passes(causality_project):
    _, ch2, _, _, _, _ = causality_project

    # Prose contains transit words ("journeyed", "after days of travel")
    prose = "After days of travel across the mountain pass, Alaric arrived at Frostpeak Fortress, weary from the journey."
    findings = ContinuityChecker.run_deterministic_checks(ch2, prose)

    location_findings = [f for f in findings if f.category == FindingCategory.LOCATION]
    assert len(location_findings) == 0


@pytest.mark.django_db
def test_destroyed_item_resurrection_flags_blocker(causality_project):
    _, ch2, _, _, _, relic = causality_project

    # Prose shows character actively brandishing/wielding the destroyed Solar Crest
    prose = "Alaric drew the Solar Crest and raised it high, blinding the enemy patrol."
    findings = ContinuityChecker.run_deterministic_checks(ch2, prose)

    item_findings = [
        f for f in findings if f.category == FindingCategory.TIMELINE and "Solar Crest" in f.claim
    ]
    assert len(item_findings) == 1
    f = item_findings[0]
    assert f.severity == FindingSeverity.BLOCKER
    assert "destroyed in Chapter 1" in f.claim


@pytest.mark.django_db
def test_item_destroyed_without_active_use_passes(causality_project):
    _, ch2, _, _, _, _ = causality_project

    # Merely mentioning the destroyed item in memory without active use does NOT trigger contradiction
    prose = "Alaric wept as he remembered the loss of the Solar Crest in the ruins of the lower quarter."
    findings = ContinuityChecker.run_deterministic_checks(ch2, prose)

    item_findings = [
        f for f in findings if f.category == FindingCategory.TIMELINE and "Solar Crest" in f.claim
    ]
    assert len(item_findings) == 0
