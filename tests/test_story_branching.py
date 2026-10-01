import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from taletomo.canon.models import CanonFact, Character, ProposedCanonItem
from taletomo.exporting.services import ExportService
from taletomo.generation.models import DraftArtifact
from taletomo.planning.models import Chapter, Project
from taletomo.planning.services import PlanningService

User = get_user_model()


@pytest.fixture
def branching_fixture(db):
    user = User.objects.create_user(username="timeline_traveler", password="password123")
    project = PlanningService.create_project_with_scaffold(
        owner=user,
        title="Chronicles of the Shattered Clock",
        premise="An airship mechanist battles temporal paradoxes.",
        target_chapters=6,
    )

    ch1 = project.chapters.get(chapter_number=1)
    d1 = DraftArtifact.objects.create(
        chapter=ch1,
        version_number=1,
        prose_content="The escapement wheel clicked forward with a sharp metallic snap.",
        word_count=10,
        model_name="test-model",
        status="accepted",
    )
    ch1.active_draft_id = d1.id
    ch1.status = Chapter.Status.APPROVED
    ch1.save()

    ch2 = project.chapters.get(chapter_number=2)
    d2 = DraftArtifact.objects.create(
        chapter=ch2,
        version_number=1,
        prose_content="Alaric peered into the temporal vortex and made his fateful choice.",
        word_count=11,
        model_name="test-model",
        status="approved",
    )
    ch2.active_draft_id = d2.id
    ch2.status = Chapter.Status.APPROVED
    ch2.save()

    # Confirmed fact
    CanonFact.objects.create(
        project=project,
        subject="Alaric",
        predicate="current_location",
        value="Temporal Observatory",
        canonical_status=CanonFact.Status.CONFIRMED,
    )

    # Proposed canon item under review
    ProposedCanonItem.objects.create(
        project=project,
        chapter=ch2,
        kind=ProposedCanonItem.Kind.FACT,
        summary="Alaric absorbed a fragment of the chronometer.",
        payload={"subject": "Alaric", "predicate": "absorbed", "value": "Chronometer Fragment"},
        status=ProposedCanonItem.Status.PROPOSED,
    )

    return project, user


@pytest.mark.django_db
def test_backup_includes_proposed_canon_review_state(branching_fixture):
    project, user = branching_fixture

    # 1. Export JSON backup
    backup_data = ExportService.export_json_backup(project)
    assert "canon" in backup_data
    assert "proposed_items" in backup_data["canon"]
    assert len(backup_data["canon"]["proposed_items"]) == 1
    p_item = backup_data["canon"]["proposed_items"][0]
    assert p_item["chapter_number"] == 2
    assert "absorbed a fragment" in p_item["summary"]

    # 2. Restore into new project
    restored = ExportService.restore_from_json(owner=user, backup_data=backup_data)
    assert restored.id != project.id
    restored_ch2 = restored.chapters.get(chapter_number=2)
    proposals = restored_ch2.proposed_canon_items.all()
    assert proposals.count() == 1
    assert proposals[0].status == ProposedCanonItem.Status.PROPOSED
    assert "Chronometer Fragment" in str(proposals[0].payload)


@pytest.mark.django_db
def test_branch_project_clones_state_up_to_branch_point(branching_fixture):
    project, user = branching_fixture

    # Fork timeline at Chapter 2: "What If Alaric Took The Dark Pact"
    branched = PlanningService.branch_project(
        source_project=project,
        from_chapter=2,
        branch_name="Dark Pact Alternate",
    )

    assert branched.id != project.id
    assert branched.parent_project == project
    assert branched.branch_point_chapter == 2
    assert "Dark Pact Alternate" in branched.title
    assert branched.owner == user

    # Chapters 1 and 2 must have their approved prose preserved
    b_ch1 = branched.chapters.get(chapter_number=1)
    assert b_ch1.status == Chapter.Status.APPROVED
    assert b_ch1.active_draft_id is not None
    d1 = DraftArtifact.objects.get(id=b_ch1.active_draft_id)
    assert "escapement wheel clicked" in d1.prose_content

    b_ch2 = branched.chapters.get(chapter_number=2)
    assert b_ch2.status == Chapter.Status.APPROVED
    d2 = DraftArtifact.objects.get(id=b_ch2.active_draft_id)
    assert "fateful choice" in d2.prose_content

    # Chapters 3+ must be initialized for alternate timeline drafting
    b_ch3 = branched.chapters.get(chapter_number=3)
    assert b_ch3.active_draft_id is None


@pytest.mark.django_db
def test_branching_timeline_isolation(branching_fixture):
    project, _ = branching_fixture

    branched = PlanningService.branch_project(
        source_project=project,
        from_chapter=2,
        branch_name="Rebel Timeline",
    )

    # Add a fact to the branched project
    CanonFact.objects.create(
        project=branched,
        subject="Alaric",
        predicate="alignment",
        value="Dark Warlock",
        canonical_status=CanonFact.Status.CONFIRMED,
    )

    # Verify parent project is completely unaffected
    assert not CanonFact.objects.filter(project=project, value="Dark Warlock").exists()
    assert CanonFact.objects.filter(project=branched, value="Dark Warlock").exists()


@pytest.mark.django_db
def test_project_branch_view_and_tenant_isolation(client, branching_fixture):
    project, user = branching_fixture
    other_user = User.objects.create_user(username="other_author", password="password123")
    branch_url = reverse("taletomo:project_branch", kwargs={"project_id": project.id})

    # 1. Unauthenticated -> 302
    resp = client.post(branch_url, {"from_chapter": 2, "branch_name": "Test Branch"})
    assert resp.status_code == 302
    assert "/login" in resp.url or "/accounts/login" in resp.url

    # 2. Authenticated as other user -> 404
    client.force_login(other_user)
    resp = client.post(branch_url, {"from_chapter": 2, "branch_name": "Test Branch"})
    assert resp.status_code == 404

    # 3. Authenticated as owner -> 302 to new branched project overview
    client.force_login(user)
    resp = client.post(branch_url, {"from_chapter": 2, "branch_name": "Sovereign Branch"})
    assert resp.status_code == 302

    forked = Project.objects.get(title__contains="Sovereign Branch")
    assert forked.parent_project == project
    assert reverse("taletomo:project_overview", kwargs={"project_id": forked.id}) in resp.url
