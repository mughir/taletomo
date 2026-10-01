import json
import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from taletomo.generation.copilot import ProseCoPilotService
from taletomo.planning.models import Chapter, Project
from taletomo.planning.services import PlanningService
from taletomo.providers.adapters import FakeProviderAdapter
from taletomo.providers.models import ProviderConfig, ProviderType

User = get_user_model()


@pytest.fixture
def copilot_fixture(db):
    user = User.objects.create_user(username="prose_stylist", password="password123")
    other_user = User.objects.create_user(username="intruder", password="password123")

    cfg = ProviderConfig.objects.create(
        user=user,
        provider_type=ProviderType.FAKE,
        encrypted_api_key="fake-token",
    )

    project = PlanningService.create_project_with_scaffold(
        owner=user,
        title="Chronicles of the Iron Mist",
        premise="A lone chronomancer uncovers the machinery beneath reality.",
        target_chapters=10,
        genre="Fantasy / Steampunk",
        tone="Atmospheric, Gritty, Tense",
    )
    chapter = project.chapters.get(chapter_number=1)
    chapter.title = "The Fractured Escapement"
    chapter.save()

    return project, chapter, user, other_user, cfg


def test_copilot_word_diff_computation():
    """Verify that compute_word_diff accurately generates visual HTML diffs with correct insertion and deletion counts."""
    orig = "He was very angry and yelled at the guard."
    sugg = "He slammed his fist against the iron gate, teeth bared."

    diff_html, words_added, words_removed = ProseCoPilotService.compute_word_diff(orig, sugg)

    assert '<del class="copilot-diff-del">' in diff_html
    assert '<ins class="copilot-diff-ins">' in diff_html
    assert "very" in diff_html
    assert "angry" in diff_html
    assert "iron gate" in diff_html
    assert words_added > 0
    assert words_removed > 0


@pytest.mark.django_db
def test_copilot_service_actions_and_context(copilot_fixture):
    """Verify polish_selection handles show_not_tell, sensory_immersion, punch_up_dialogue, and intensify_tension."""
    project, chapter, user, _, cfg = copilot_fixture
    adapter = FakeProviderAdapter(cfg)

    # 1. Show Don't Tell
    res_show = ProseCoPilotService.polish_selection(
        user=user,
        project=project,
        chapter=chapter,
        selected_text="Alaric was furious when he heard the news.",
        action="show_not_tell",
        context_before="The herald finished reading the proclamation.",
        context_after="The courtiers looked away in silence.",
        custom_adapter=adapter,
    )
    assert res_show["success"] is True
    assert res_show["action"] == "show_not_tell"
    assert "Alaric's jaw tightened" in res_show["suggested_text"]
    assert '<ins class="copilot-diff-ins">' in res_show["diff_html"]
    assert res_show["words_suggested"] > 0

    # 2. Sensory Immersion
    res_sensory = ProseCoPilotService.polish_selection(
        user=user,
        project=project,
        chapter=chapter,
        selected_text="It was raining outside in the dark street.",
        action="sensory_immersion",
        custom_adapter=adapter,
    )
    assert res_sensory["success"] is True
    assert "acrid reek of ozone" in res_sensory["suggested_text"]
    assert "glass needles" in res_sensory["suggested_text"]

    # 3. Punch-up Dialogue
    res_dial = ProseCoPilotService.polish_selection(
        user=user,
        project=project,
        chapter=chapter,
        selected_text="'I do not trust you,' Alaric said to Malakor.",
        action="punch_up_dialogue",
        custom_adapter=adapter,
    )
    assert res_dial["success"] is True
    assert "Keep your hollow promises" in res_dial["suggested_text"]

    # 4. Intensify Tension
    res_tension = ProseCoPilotService.polish_selection(
        user=user,
        project=project,
        chapter=chapter,
        selected_text="The bomb was about to go off and he had to run.",
        action="intensify_tension",
        custom_adapter=adapter,
    )
    assert res_tension["success"] is True
    assert "Three heartbeats left" in res_tension["suggested_text"]


@pytest.mark.django_db
def test_copilot_api_endpoint_workflow(copilot_fixture):
    """Verify HTTP API endpoint integration with session auth, diff delivery, and error handling."""
    project, chapter, user, _, _ = copilot_fixture
    client = Client()
    client.force_login(user)

    url = reverse("taletomo:chapter_copilot_api", kwargs={"project_id": project.id, "chapter_id": chapter.id})

    # Successful call with context
    payload = {
        "action": "sensory_immersion",
        "selected_text": "The wind was cold as he stood on the bridge.",
        "context_before": "He pulled his collar high against the storm.",
        "context_after": "Below, the river churned like black glass.",
        "custom_instruction": "Add smell of salt and cold iron",
    }
    response = client.post(url, data=json.dumps(payload), content_type="application/json")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["action"] == "sensory_immersion"
    assert "acrid reek" in data["suggested_text"]
    assert "diff_html" in data
    assert '<ins class="copilot-diff-ins">' in data["diff_html"]
    assert data["words_added"] > 0

    # Validation: empty selection
    bad_payload = {"action": "expand", "selected_text": "   "}
    bad_res = client.post(url, data=json.dumps(bad_payload), content_type="application/json")
    assert bad_res.status_code == 400
    assert bad_res.json()["success"] is False


@pytest.mark.django_db
def test_copilot_api_tenant_isolation(copilot_fixture):
    """Verify that unauthorized users cannot call co-pilot on another user's novel."""
    project, chapter, _, other_user, _ = copilot_fixture
    client = Client()
    client.force_login(other_user)

    url = reverse("taletomo:chapter_copilot_api", kwargs={"project_id": project.id, "chapter_id": chapter.id})
    payload = {
        "action": "show_not_tell",
        "selected_text": "Unauthorized spy reading text.",
    }
    response = client.post(url, data=json.dumps(payload), content_type="application/json")
    # Returns 404 because get_object_or_404 filters by owner=request.user
    assert response.status_code == 404
