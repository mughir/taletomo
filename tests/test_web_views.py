import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from taletomo.planning.models import Chapter
from taletomo.planning.services import PlanningService

User = get_user_model()


@pytest.mark.django_db
def test_web_routes_and_views():
    client = Client()
    user = User.objects.create_user(username="author_tester", password="password123")
    client.force_login(user)

    # 1. Home
    resp = client.get(reverse("taletomo:home"))
    assert resp.status_code == 200

    # 2. Project List
    resp = client.get(reverse("taletomo:project_list"))
    assert resp.status_code == 200

    # 3. Project New Wizard POST
    resp = client.post(
        reverse("taletomo:project_new"),
        {
            "title": "Aetherium Chronicles",
            "premise": "Airships battle over floating sky-islands.",
            "target_chapters": 40,
            "genre": "Steampunk / Fantasy",
            "tone": "Adventurous",
            "pov": "Third Person Limited",
            "tense": "Past Tense",
        },
    )
    assert resp.status_code == 302
    assert "projects/" in resp.url

    # Find created project
    from taletomo.planning.models import Project
    project = Project.objects.get(title="Aetherium Chronicles")

    # 4. Project Overview
    resp = client.get(reverse("taletomo:project_overview", args=[project.id]))
    assert resp.status_code == 200
    assert "Aetherium Chronicles" in resp.content.decode("utf-8")

    # 5. Project Bible
    resp = client.get(reverse("taletomo:project_bible", args=[project.id]))
    assert resp.status_code == 200

    # 6. Outline
    resp = client.get(reverse("taletomo:project_outline", args=[project.id]))
    assert resp.status_code == 200

    # 7. Chapter Plan & Edit
    ch1 = Chapter.objects.get(project=project, chapter_number=1)
    resp = client.get(reverse("taletomo:chapter_plan", args=[project.id, ch1.id]))
    assert resp.status_code == 200

    resp = client.get(reverse("taletomo:chapter_edit", args=[project.id, ch1.id]))
    assert resp.status_code == 200

    # 8. Providers Page
    resp = client.get(reverse("taletomo:settings_providers"))
    assert resp.status_code == 200
