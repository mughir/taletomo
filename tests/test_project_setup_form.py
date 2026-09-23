import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from taletomo.planning.models import Project

User = get_user_model()


@pytest.mark.django_db
def test_project_form_persists_custom_chapter_word_target():
    user = User.objects.create_user(username="custom_length_author")
    client = Client()
    client.force_login(user)

    response = client.post(
        reverse("taletomo:project_new"),
        {
            "title": "Custom length novel",
            "premise": "Test custom chapter length",
            "target_chapters": "12",
            "length_preset": "custom",
            "custom_target_words": "3500",
        },
    )

    assert response.status_code == 302
    project = Project.objects.get(owner=user, title="Custom length novel")
    assert project.target_words_per_chapter == 3500
    assert project.length_preset == "custom"


@pytest.mark.django_db
@pytest.mark.parametrize("chapter_count", ["0", "4001", "not-a-number"])
def test_project_form_rejects_invalid_chapter_count_without_creating_project(chapter_count):
    user = User.objects.create_user(username=f"invalid_chapters_{chapter_count}")
    client = Client()
    client.force_login(user)

    response = client.post(
        reverse("taletomo:project_new"),
        {
            "title": "Invalid chapter project",
            "premise": "Must be rejected",
            "target_chapters": chapter_count,
        },
    )

    assert response.status_code == 200
    assert not Project.objects.filter(owner=user, title="Invalid chapter project").exists()
    assert "between 1 and 4,000" in response.content.decode()
