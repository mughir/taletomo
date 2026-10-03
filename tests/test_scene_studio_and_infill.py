import pytest
from unittest.mock import MagicMock
from django.urls import reverse
from taletomo.generation.copilot import ProseCoPilotService
from taletomo.generation.models import DraftArtifact, DraftStatus
from taletomo.generation.pipeline import GenerationPipeline
from taletomo.planning.models import Chapter, ChapterPlan, Project, ScenePlan


@pytest.mark.django_db
def test_execute_scene_reroll_sandwich_context_and_splicing(db, django_user_model):
    user = django_user_model.objects.create_user(username="testrerollauthor", password="password")
    project = Project.objects.create(owner=user, title="Reroll Chronicles", target_words_per_chapter=1500)
    chapter = Chapter.objects.create(project=project, chapter_number=1, title="The Ancient Gate")

    plan = ChapterPlan.objects.create(chapter=chapter, target_words=1500)
    sc1 = ScenePlan.objects.create(chapter_plan=plan, scene_order=1, objective="Approach the sealed gate", estimated_words=500)
    sc2 = ScenePlan.objects.create(chapter_plan=plan, scene_order=2, objective="Solve the glyph puzzle", estimated_words=500)
    sc3 = ScenePlan.objects.create(chapter_plan=plan, scene_order=3, objective="Enter the dark interior", estimated_words=500)

    original_sc1 = "Alaric walked towards the enormous stone gate covered in ancient moss."
    original_sc2 = "He tried to turn the rusty iron wheel, but it squeaked loudly."
    original_sc3 = "Beyond the threshold, shadows clung to the stone pillars of the hall."
    initial_prose = f"{original_sc1}\n\n* * *\n\n{original_sc2}\n\n* * *\n\n{original_sc3}"

    draft_v1 = DraftArtifact.objects.create(
        chapter=chapter,
        version_number=1,
        prose_content=initial_prose,
        word_count=len(initial_prose.split()),
        status=DraftStatus.ACCEPTED,
    )
    chapter.active_draft_id = draft_v1.id
    chapter.save()

    # Mock provider adapter
    mock_adapter = MagicMock()
    mock_resp = MagicMock()
    rerolled_sc2 = "He pressed three illuminated runes in sequence: sun, moon, and weeping eye. The mechanism clicked open with a deep rumble."
    mock_resp.content = rerolled_sc2
    mock_resp.total_tokens = 60
    mock_resp.cost_usd = 0.001
    mock_adapter.generate_text.return_value = mock_resp

    # Re-roll Scene 2
    new_draft = GenerationPipeline.execute_scene_reroll(
        chapter=chapter,
        scene_order=2,
        custom_instruction="Make the puzzle feel more arcane and mysterious",
        custom_adapter=mock_adapter,
        user=user,
    )

    assert new_draft.version_number == 2
    assert new_draft.parent_draft == draft_v1

    # Verify sandwich context passed to model during drafting (first call)
    first_call_args = mock_adapter.generate_text.call_args_list[0][1]
    prompt = first_call_args["prompt"]
    assert "TASK: DRAFT_SCENE_REROLL (Scene 2 of 3)" in prompt
    assert original_sc1 in prompt  # Preceding tail
    assert original_sc3 in prompt  # Succeeding head
    assert "arcane and mysterious" in prompt

    # Verify composite text spliced properly
    expected_composite = f"{original_sc1}\n\n* * *\n\n{rerolled_sc2}\n\n* * *\n\n{original_sc3}"
    assert new_draft.prose_content == expected_composite
    assert chapter.active_draft_id == new_draft.id


@pytest.mark.django_db
def test_chapter_scene_reroll_web_view(db, django_user_model, client):
    user = django_user_model.objects.create_user(username="testviewreroll", password="password")
    client.force_login(user)
    project = Project.objects.create(owner=user, title="Web Reroll Project")
    chapter = Chapter.objects.create(project=project, chapter_number=1, title="Chapter 1")

    plan = ChapterPlan.objects.create(chapter=chapter, target_words=1000)
    ScenePlan.objects.create(chapter_plan=plan, scene_order=1, objective="Opening scene", estimated_words=500)
    ScenePlan.objects.create(chapter_plan=plan, scene_order=2, objective="Closing scene", estimated_words=500)

    initial_prose = "First scene text.\n\n* * *\n\nSecond scene old text."
    draft_v1 = DraftArtifact.objects.create(
        chapter=chapter,
        version_number=1,
        prose_content=initial_prose,
        word_count=len(initial_prose.split()),
    )
    chapter.active_draft_id = draft_v1.id
    chapter.save()

    url = reverse("taletomo:chapter_scene_reroll", kwargs={
        "project_id": project.id,
        "chapter_id": chapter.id,
        "scene_order": 2,
    })

    resp = client.post(url, {"directive": "Make it rain heavily"})
    assert resp.status_code == 302

    chapter.refresh_from_db()
    assert chapter.active_draft_id != draft_v1.id
    new_draft = DraftArtifact.objects.get(id=chapter.active_draft_id)
    assert new_draft.version_number == 2


@pytest.mark.django_db
def test_copilot_infill_bridge_action(db, django_user_model):
    user = django_user_model.objects.create_user(username="testinfill", password="password")
    project = Project.objects.create(owner=user, title="Infill Project")
    chapter = Chapter.objects.create(project=project, chapter_number=1, title="Bridge Scene")

    mock_adapter = MagicMock()
    mock_resp = MagicMock()
    mock_resp.content = "They rode through the pine forest in silence, the carriage wheels churning deep ruts in the wet loam."
    mock_resp.total_tokens = 40
    mock_adapter.generate_text.return_value = mock_resp

    result = ProseCoPilotService.polish_selection(
        user=user,
        project=project,
        chapter=chapter,
        selected_text="[Bridge scene needed]",
        action="infill_bridge",
        context_before="The tavern door slammed shut behind them.",
        context_after="At midnight, the castle gates loomed out of the fog.",
        custom_instruction="Describe the dreary carriage ride between the two places.",
        custom_adapter=mock_adapter,
    )

    assert result["success"] is True
    assert result["action"] == "infill_bridge"
    assert "narrative bridge" in result["explanation"]
    assert "pine forest" in result["suggested_text"]
