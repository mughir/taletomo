import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from taletomo.canon.models import CanonFact, ProposedCanonItem, TruthScope
from taletomo.generation.models import DraftArtifact, DraftStatus, GenerationJob
from taletomo.planning.autopilot import AutoPilotService
from taletomo.planning.models import Chapter, ChapterPlan, Project
from taletomo.planning.services import PlanningService
from taletomo.providers.models import ProviderConfig, ProviderType

User = get_user_model()


@pytest.fixture
def reader_fixture(db):
    user = User.objects.create_user(username="casual_reader", password="password123")
    cfg = ProviderConfig.objects.create(
        user=user,
        provider_type=ProviderType.FAKE,
        encrypted_api_key="reader-test-key",
    )
    project = PlanningService.create_project_with_scaffold(
        owner=user,
        title="Chronicles of the Wind Walker",
        premise="A nomad discovers forgotten wind magic and takes to the skies.",
        genre="Xianxia / Adventure",
        target_chapters=10,
        target_words_per_chapter=2000,
    )
    return user, project, cfg


@pytest.mark.django_db
def test_project_new_action_read_starts_generation_and_redirects(reader_fixture):
    """When a casual reader creates a project with action='read', it triggers Ch1 drafting and opens reader mode."""
    user, _, _ = reader_fixture
    client = Client()
    client.force_login(user)

    response = client.post(
        reverse("taletomo:project_new"),
        {
            "title": "Starlight Odyssey",
            "premise": "A navigator explores uncharted astral currents.",
            "genre": "Sci-Fi / Space Opera",
            "tone": "Wonder, Hopeful",
            "pacing": "Fast-Paced",
            "target_chapters": "20",
            "length_preset": "standard",
            "action": "read",
        },
    )

    assert response.status_code == 302
    project = Project.objects.get(owner=user, title="Starlight Odyssey")
    ch1 = project.chapters.get(chapter_number=1)
    assert response.url == reverse("taletomo:chapter_read", args=[project.id, ch1.id])

    # Verify that Chapter 1 contract was auto-approved and generation job started
    assert ch1.plan.status == ChapterPlan.Status.APPROVED
    assert project.generation_jobs.filter(target_chapter_id=ch1.id).exists()


@pytest.mark.django_db
def test_project_read_redirects_to_appropriate_chapter(reader_fixture):
    """project_read jumps directly to Chapter 1 or the latest chapter with a draft."""
    user, project, _ = reader_fixture
    client = Client()
    client.force_login(user)

    ch1 = project.chapters.get(chapter_number=1)
    ch2 = project.chapters.get(chapter_number=2)

    # Initially without drafts, redirects to Chapter 1
    resp1 = client.get(reverse("taletomo:project_read", args=[project.id]))
    assert resp1.status_code == 302
    assert resp1.url == reverse("taletomo:chapter_read", args=[project.id, ch1.id])

    # When Chapter 2 has a draft, project_read jumps straight to Chapter 2
    draft2 = DraftArtifact.objects.create(
        chapter=ch2,
        version_number=1,
        prose_content="The storm howled across the crimson canyons.",
        word_count=8,
        status=DraftStatus.ACCEPTED,
    )
    ch2.active_draft_id = draft2.id
    ch2.save(update_fields=["active_draft_id"])

    resp2 = client.get(reverse("taletomo:project_read", args=[project.id]))
    assert resp2.status_code == 302
    assert resp2.url == reverse("taletomo:chapter_read", args=[project.id, ch2.id])


@pytest.mark.django_db
def test_chapter_read_view_renders_prose_and_controls(reader_fixture):
    """chapter_read renders distraction-free reading typography, TOC, and word counts."""
    user, project, _ = reader_fixture
    client = Client()
    client.force_login(user)

    ch1 = project.chapters.get(chapter_number=1)
    prose = (
        "The morning mist rose over the jagged spires of the Outer Reach.\n\n"
        "Ren leaned against his glider, testing the tension of the silk cords.\n\n"
        "* * *\n\n"
        "Far below, the dust sea churned like liquid copper."
    )
    draft1 = DraftArtifact.objects.create(
        chapter=ch1,
        version_number=1,
        prose_content=prose,
        word_count=35,
        status=DraftStatus.ACCEPTED,
    )
    ch1.active_draft_id = draft1.id
    ch1.save(update_fields=["active_draft_id"])

    response = client.get(reverse("taletomo:chapter_read", args=[project.id, ch1.id]))
    assert response.status_code == 200
    content = response.content.decode("utf-8")

    assert "The morning mist rose over the jagged spires" in content
    assert "✦ ✦ ✦" in content  # Scene divider ornament
    assert "Table of Contents" in content
    assert "35 words" in content
    assert "Settings" in content or "Theme" in content
    assert reverse("taletomo:chapter_edit", args=[project.id, ch1.id]) in content


@pytest.mark.django_db
def test_chapter_read_next_auto_pilot_advancement(reader_fixture):
    """Clicking Next Chapter auto-approves draft, commits extracted canon, and generates next chapter."""
    user, project, _ = reader_fixture
    client = Client()
    client.force_login(user)

    ch1 = project.chapters.get(chapter_number=1)
    ch2 = project.chapters.get(chapter_number=2)

    # Setup Chapter 1 with draft UNDER_REVIEW and proposed canon fact
    draft1 = DraftArtifact.objects.create(
        chapter=ch1,
        version_number=1,
        prose_content="Ren learned the forbidden wind technique from the hermit.",
        word_count=9,
        status=DraftStatus.UNDER_REVIEW,
    )
    ch1.active_draft_id = draft1.id
    ch1.status = Chapter.Status.REVIEW
    ch1.save(update_fields=["active_draft_id", "status"])

    item = ProposedCanonItem.objects.create(
        project=project,
        chapter=ch1,
        kind=ProposedCanonItem.Kind.FACT,
        payload={
            "subject": "Ren",
            "predicate": "knows_art",
            "value": "Wind Dance",
            "scope": TruthScope.WORLD_TRUTH,
        },
        summary="Ren knows Wind Dance",
        status=ProposedCanonItem.Status.PROPOSED,
    )

    initial_head = project.active_branch_head

    # Reader clicks Next Chapter!
    response = client.post(reverse("taletomo:chapter_read_next", args=[project.id, ch1.id]))
    assert response.status_code == 302
    assert response.url == reverse("taletomo:chapter_read", args=[project.id, ch2.id])

    # 1. Chapter 1 draft was auto-accepted
    draft1.refresh_from_db()
    assert draft1.status == DraftStatus.ACCEPTED

    # 2. Chapter 1 canon was committed and locked
    ch1.refresh_from_db()
    assert ch1.status == Chapter.Status.LOCKED

    # 3. Branch head advanced from rev_1 to rev_2
    project.refresh_from_db()
    assert project.active_branch_head != initial_head

    # 4. Proposed fact was consumed into canon
    item.refresh_from_db()
    assert item.status == ProposedCanonItem.Status.CONSUMED
    assert CanonFact.objects.filter(project=project, subject="Ren", predicate="knows_art").exists()

    # 5. Chapter 2 generation was automatically initiated in background
    assert project.generation_jobs.filter(target_chapter_id=ch2.id).exists()


@pytest.mark.django_db
def test_chapter_read_generate_view(reader_fixture):
    """POST to chapter_read_generate queues a job and redirects back to reader mode."""
    user, project, _ = reader_fixture
    client = Client()
    client.force_login(user)

    ch1 = project.chapters.get(chapter_number=1)
    response = client.post(reverse("taletomo:chapter_read_generate", args=[project.id, ch1.id]))

    assert response.status_code == 302
    assert response.url == reverse("taletomo:chapter_read", args=[project.id, ch1.id])
    assert project.generation_jobs.filter(target_chapter_id=ch1.id).exists()
