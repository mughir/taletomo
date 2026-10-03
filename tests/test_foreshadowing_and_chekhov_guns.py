import pytest
from django.urls import reverse
from taletomo.canon.models import Item, PlotThread, PlotThreadBreadcrumb
from taletomo.canon.services import CanonService
from taletomo.consistency.checker import ContinuityChecker
from taletomo.consistency.models import FindingCategory, FindingSeverity
from taletomo.planning.models import Chapter, ChapterPlan, Project
from taletomo.planning.services import PlanningService


@pytest.mark.django_db
def test_plot_thread_dormancy_calculation(db, django_user_model):
    user = django_user_model.objects.create_user(username="testdormancy", password="password")
    project = Project.objects.create(owner=user, title="Dormancy Novel")
    thread = PlotThread.objects.create(
        project=project,
        title="Identity of the Shadow Monarch",
        setup_chapter=1,
        last_mentioned_chapter=2,
        dormancy_threshold=5,
    )

    # At chapter 5: gap is 5 - 2 = 3 < 5 -> not dormant
    assert thread.is_dormant(current_chapter_number=5) is False
    assert thread.chapters_since_mention(5) == 3

    # At chapter 7: gap is 7 - 2 = 5 >= 5 -> dormant!
    assert thread.is_dormant(current_chapter_number=7) is True
    assert thread.chapters_since_mention(7) == 5


@pytest.mark.django_db
def test_continuity_checker_detects_dormant_thread(db, django_user_model):
    user = django_user_model.objects.create_user(username="testcontdorm", password="password")
    project = Project.objects.create(owner=user, title="Checker Dormancy")
    chapter = Chapter.objects.create(project=project, chapter_number=15, title="The Deep Mountains")
    thread = PlotThread.objects.create(
        project=project,
        title="The Stolen Dragon Egg",
        setup_chapter=1,
        last_mentioned_chapter=3,
        dormancy_threshold=8,
    )

    findings = ContinuityChecker.run_deterministic_checks(chapter, "The wind wailed across the empty pass.")
    dormant_findings = [f for f in findings if f.category == FindingCategory.PLOT and "Dormant narrative thread" in f.claim]
    assert len(dormant_findings) == 1
    assert "The Stolen Dragon Egg" in dormant_findings[0].claim
    assert dormant_findings[0].severity == FindingSeverity.WARNING


@pytest.mark.django_db
def test_replan_frontier_injects_dormant_thread_reminders(db, django_user_model):
    user = django_user_model.objects.create_user(username="testreplandorm", password="password")
    project = PlanningService.create_project_with_scaffold(
        owner=user,
        title="Replan Horizon Story",
        premise="A hero seeks redemption.",
        target_chapters=30,
        target_words_per_chapter=2000,
    )
    # Thread setup in Ch 1, last mentioned Ch 1, threshold 5
    thread = PlotThread.objects.create(
        project=project,
        title="The Cursed Amulet",
        setup_chapter=1,
        last_mentioned_chapter=1,
        dormancy_threshold=5,
    )

    # Replan starting from chapter 8 (dormant relative to ch 8!)
    result = PlanningService.replan_frontier(project, from_chapter=8, horizon_size=3)
    assert len(result["replanned_chapters"]) == 3

    ch8 = Chapter.objects.get(project=project, chapter_number=8)
    plan8 = ChapterPlan.objects.get(chapter=ch8)
    assert any("The Cursed Amulet" in beat for beat in plan8.required_beats)


@pytest.mark.django_db
def test_chekhov_gun_item_link_and_breadcrumbs(db, django_user_model, client):
    user = django_user_model.objects.create_user(username="testchekhov", password="password")
    client.force_login(user)
    project = Project.objects.create(owner=user, title="Chekhov Project")
    chapter = Chapter.objects.create(project=project, chapter_number=1, title="Chapter 1")

    thread = PlotThread.objects.create(
        project=project,
        title="The Secret of the Obsidian Dagger",
        setup_chapter=1,
        last_mentioned_chapter=1,
    )
    dagger = Item.objects.create(
        project=project,
        name="Obsidian Dagger",
        description="Carved with ancient runes.",
    )

    # 1. Test Linking Item (Chekhov's Gun)
    url = reverse("taletomo:project_threads_matrix", kwargs={"project_id": project.id})
    resp = client.post(url, {
        "action": "link_item",
        "thread_id": str(thread.id),
        "item_id": str(dagger.id),
    })
    assert resp.status_code == 302
    dagger.refresh_from_db()
    assert dagger.plot_thread == thread

    # 2. Test Adding Breadcrumb
    resp_bc = client.post(url, {
        "action": "add_breadcrumb",
        "thread_id": str(thread.id),
        "chapter_number": 4,
        "breadcrumb_type": "clue",
        "description": "Alaric discovers the dagger glows in moonlight.",
    })
    assert resp_bc.status_code == 302
    thread.refresh_from_db()
    assert thread.last_mentioned_chapter == 4
    assert thread.breadcrumbs.count() == 1
    bc = thread.breadcrumbs.first()
    assert bc.chapter_number == 4
    assert "glows in moonlight" in bc.description

    # 3. Test View Rendering
    resp_view = client.get(url)
    assert resp_view.status_code == 200
    assert b"Foreshadowing &amp; Chekhov's Gun Matrix" in resp_view.content
    assert b"The Secret of the Obsidian Dagger" in resp_view.content
    assert b"Obsidian Dagger" in resp_view.content
