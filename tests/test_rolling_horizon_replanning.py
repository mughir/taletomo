import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from taletomo.canon.models import CanonFact, Character, PlotThread
from taletomo.planning.models import Chapter, ChapterPlan, ScenePlan
from taletomo.planning.services import PlanningService
from taletomo.providers.adapters import FakeProviderAdapter

User = get_user_model()


@pytest.fixture
def project_with_established_canon(db):
    user = User.objects.create_user(username="frontier_author", password="password123")
    project = PlanningService.create_project_with_scaffold(
        owner=user,
        title="Chronicles of the Iron Horizon",
        premise="A rebellion brews in the steam caverns under the capital.",
        target_chapters=8,
    )

    # Chapter 1 is committed/approved
    ch1 = Chapter.objects.get(project=project, chapter_number=1)
    ch1.status = Chapter.Status.APPROVED
    ch1.current_summary = "Alaric infiltrated the foundry and sabotaged the pressure valves."
    ch1.save()

    # Active character with a severe wound
    Character.objects.create(
        project=project,
        name="Alaric",
        role="Protagonist",
        wounds_status="Shattered left wrist and acid burn on collarbone",
    )

    # Open plot thread
    PlotThread.objects.create(
        project=project,
        title="Foundry Guard Pursuit",
        category="conflict",
        status="open",
        setup_chapter=1,
    )

    # Confirmed canon fact
    CanonFact.objects.create(
        project=project,
        subject="Foundry Pressure Valve",
        predicate="status",
        value="Destroyed",
        canonical_status=CanonFact.Status.CONFIRMED,
    )

    return project, user


@pytest.mark.django_db
def test_replan_frontier_adapts_to_wounds_and_open_threads(project_with_established_canon):
    project, _ = project_with_established_canon

    # Replan horizon of 3 chapters starting from chapter 2
    result = PlanningService.replan_frontier(project, from_chapter=2, horizon_size=3)

    assert result["replanned_chapters"] == [2, 3, 4]
    assert 2 in result["replanned_chapters"]
    assert 3 in result["replanned_chapters"]
    assert 4 in result["replanned_chapters"]

    ch2 = Chapter.objects.get(project=project, chapter_number=2)
    assert ch2.status == Chapter.Status.PLANNED
    assert hasattr(ch2, "plan")
    plan2 = ch2.plan
    assert plan2.status == ChapterPlan.Status.PROPOSED
    assert len(plan2.objectives) > 0
    assert len(plan2.required_beats) > 0

    # Verify active wound is carried into continuity requirements or beats
    continuity_str = " ".join(plan2.continuity_requirements) + " ".join(plan2.prohibited_outcomes) + " ".join(plan2.objectives)
    assert "Shattered left wrist" in continuity_str or "Alaric" in continuity_str or "Foundry Guard Pursuit" in continuity_str

    # Verify scenes generated
    scenes = plan2.scenes.all()
    assert scenes.count() >= 2
    for s in scenes:
        assert s.objective != ""
        assert s.estimated_words > 0


@pytest.mark.django_db
def test_replan_frontier_strictly_preserves_locked_and_approved_chapters(project_with_established_canon):
    project, _ = project_with_established_canon

    ch1 = Chapter.objects.get(project=project, chapter_number=1)
    ch1.status = Chapter.Status.LOCKED
    ch1.title = "Original Locked Chapter 1"
    ch1.save()

    ch2 = Chapter.objects.get(project=project, chapter_number=2)
    ch2.status = Chapter.Status.APPROVED
    ch2.title = "Original Approved Chapter 2"
    ch2.save()

    # Replan from chapter 1 with horizon 4
    result = PlanningService.replan_frontier(project, from_chapter=1, horizon_size=4)

    # Chapters 1 and 2 must be in skipped_chapters and NEVER modified
    assert 1 in result["skipped_chapters"]
    assert 2 in result["skipped_chapters"]
    assert result["replanned_chapters"] == [3, 4]

    ch1.refresh_from_db()
    assert ch1.status == Chapter.Status.LOCKED
    assert ch1.title == "Original Locked Chapter 1"

    ch2.refresh_from_db()
    assert ch2.status == Chapter.Status.APPROVED
    assert ch2.title == "Original Approved Chapter 2"


@pytest.mark.django_db
def test_replan_frontier_with_provider_adapter(project_with_established_canon):
    project, user = project_with_established_canon
    from taletomo.providers.models import ProviderConfig, ProviderType

    cfg = ProviderConfig.objects.create(
        user=user,
        name="Fake Provider",
        provider_type=ProviderType.FAKE,
        is_default=True,
    )
    adapter = FakeProviderAdapter(cfg)

    result = PlanningService.replan_frontier(
        project, from_chapter=2, horizon_size=2, custom_adapter=adapter
    )
    assert result["replanned_chapters"] == [2, 3]

    ch2 = Chapter.objects.get(project=project, chapter_number=2)
    assert "Frontier Trial" in ch2.title
    assert any("injuries" in obj or "canon" in obj for obj in ch2.plan.objectives)
    assert ch2.plan.scenes.count() == 2


@pytest.mark.django_db
def test_replan_horizon_view_and_tenant_isolation(client, project_with_established_canon):
    project, user = project_with_established_canon
    other_user = User.objects.create_user(username="intruder", password="password123")
    replan_url = reverse("taletomo:project_replan_horizon", kwargs={"project_id": project.id})

    # 1. Unauthenticated -> 302 to login
    resp = client.post(replan_url, {"horizon_size": 3})
    assert resp.status_code == 302
    assert "/login" in resp.url or "/accounts/login" in resp.url

    # 2. Authenticated as intruder -> 404 (tenant isolation)
    client.force_login(other_user)
    resp = client.post(replan_url, {"horizon_size": 3})
    assert resp.status_code == 404

    # 3. Authenticated as project owner -> 302 redirect with success message
    client.force_login(user)
    resp = client.post(replan_url, {"horizon_size": 3, "from_chapter": 2})
    assert resp.status_code == 302
    assert reverse("taletomo:project_outline", kwargs={"project_id": project.id}) in resp.url
