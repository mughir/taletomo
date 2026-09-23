import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from taletomo.planning.models import Chapter
from taletomo.planning.services import PlanningService


@pytest.mark.django_db
def test_signed_out_pages_hide_workspace_navigation(client):
    response = client.get(reverse("login"))
    page = response.content.decode()

    assert response.status_code == 200
    assert "styles.css?v=studio-20260923b" in page
    assert 'href="/projects/"' not in page
    assert 'href="/jobs/"' not in page
    assert 'action="/accounts/logout/"' not in page


@pytest.mark.django_db
def test_signed_in_shell_has_clear_workspace_navigation_and_post_logout(client):
    user = get_user_model().objects.create_user(username="author", password="Safe-Test-Pass-8729")
    client.force_login(user)

    response = client.get(reverse("taletomo:home"))
    page = response.content.decode()

    assert response.status_code == 200
    assert "Writing desk" in page
    assert 'href="/projects/"' in page
    assert 'href="/jobs/"' in page
    assert 'action="/accounts/logout/" method="post"' in page


@pytest.mark.django_db
def test_empty_dashboard_has_a_clear_first_novel_action(client):
    user = get_user_model().objects.create_user(username="new_author", password="Safe-Test-Pass-8729")
    client.force_login(user)

    response = client.get(reverse("taletomo:home"))
    page = response.content.decode()

    assert response.status_code == 200
    assert "Your writing desk is ready" in page
    assert "Start your first novel" in page
    assert reverse("taletomo:project_new") in page


@pytest.mark.django_db
def test_novel_setup_is_presented_as_a_clear_foundation_form_not_a_fake_wizard(client):
    user = get_user_model().objects.create_user(username="setup_author", password="Safe-Test-Pass-8729")
    client.force_login(user)

    response = client.get(reverse("taletomo:project_new"))
    page = response.content.decode()

    assert response.status_code == 200
    assert "Start with the story" in page
    assert "Publishing shape" in page
    assert "Narrative voice" in page
    assert "Novel Creation Wizard" not in page


@pytest.mark.django_db
def test_chapter_editor_reads_like_a_focused_writing_workbench(client):
    owner = get_user_model().objects.create_user(username="workbench_author", password="Safe-Test-Pass-8729")
    project = PlanningService.create_project_with_scaffold(
        owner=owner,
        title="The Green Archive",
        premise="A keeper discovers a living library.",
        target_chapters=5,
    )
    chapter = Chapter.objects.get(project=project, chapter_number=1)
    client.force_login(owner)

    response = client.get(reverse("taletomo:chapter_edit", args=[project.id, chapter.id]))
    page = response.content.decode()

    assert response.status_code == 200
    assert "writer-workbench" in page
    assert "Manuscript" in page
    assert "id=\"prose-textarea\"" in page
    assert "Review before it becomes canon" in page
    assert "Save a new version" in page
