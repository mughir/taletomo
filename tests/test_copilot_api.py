import json
import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from taletomo.planning.services import PlanningService
from taletomo.providers.models import ProviderConfig, ProviderType

User = get_user_model()


@pytest.mark.django_db
def test_copilot_api_requires_login(client):
    url = "/projects/00000000-0000-0000-0000-000000000000/chapters/00000000-0000-0000-0000-000000000000/copilot/"
    res = client.post(url, json.dumps({"action": "expand", "selected_text": "sample"}), content_type="application/json")
    assert res.status_code == 302


@pytest.mark.django_db
def test_copilot_api_rejects_cross_tenant_access(client):
    author_a = User.objects.create_user(username="author_a")
    author_b = User.objects.create_user(username="author_b")
    project_a = PlanningService.create_project_with_scaffold(
        owner=author_a, title="Project A", premise="Tenant boundary test", target_chapters=2
    )
    ch_a = project_a.chapters.first()

    # Login as Author B and attempt to call co-pilot on Author A's chapter
    client.force_login(author_b)
    url = reverse("taletomo:chapter_copilot_api", args=[project_a.id, ch_a.id])
    res = client.post(
        url,
        json.dumps({"action": "expand", "selected_text": "prose from author a"}),
        content_type="application/json",
    )
    assert res.status_code == 404


@pytest.mark.django_db
def test_copilot_api_validates_selected_text(client):
    owner = User.objects.create_user(username="copilot_author")
    client.force_login(owner)
    project = PlanningService.create_project_with_scaffold(
        owner=owner, title="Validation Project", premise="Testing empty validation", target_chapters=2
    )
    ch = project.chapters.first()

    url = reverse("taletomo:chapter_copilot_api", args=[project.id, ch.id])
    res = client.post(
        url,
        json.dumps({"action": "expand", "selected_text": "   "}),
        content_type="application/json",
    )
    assert res.status_code == 400
    data = res.json()
    assert not data["success"]
    assert "No prose text selected" in data["error"]


@pytest.mark.django_db
def test_copilot_api_executes_actions_and_returns_suggestions(client):
    owner = User.objects.create_user(username="copilot_success_author")
    client.force_login(owner)
    project = PlanningService.create_project_with_scaffold(
        owner=owner, title="Writing Studio Project", premise="Co-Pilot generation test", target_chapters=2
    )
    ch = project.chapters.first()

    # Configure custom copilot model in provider
    ProviderConfig.objects.create(
        user=owner,
        name="Fake Provider",
        provider_type=ProviderType.FAKE,
        task_routing={"copilot": "mock-copilot-specialized"},
        is_default=True,
    )

    url = reverse("taletomo:chapter_copilot_api", args=[project.id, ch.id])
    for action in ["expand", "show_not_tell", "punch_up_dialogue", "fix_continuity"]:
        res = client.post(
            url,
            json.dumps({
                "action": action,
                "selected_text": "He walked across the cold street in silence.",
                "custom_instruction": "Add heavy autumn fog and carriage lanterns.",
            }),
            content_type="application/json",
        )
        assert res.status_code == 200
        data = res.json()
        assert data["success"]
        assert data["action"] == action
        assert len(data["suggested_text"]) > 0
