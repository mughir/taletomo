"""End-to-End integration tests for:
1. Modular Scene-Studio & Infill
2. Dialogue Stylometry & Voice Guard
3. Foreshadowing Lifespan Matrix & Chekhov's Guns
"""

import json
from unittest.mock import MagicMock
import pytest
from django.urls import reverse

from taletomo.canon.models import Character, CharacterRole, Item, PlotThread, PlotThreadBreadcrumb
from taletomo.canon.services import CanonService
from taletomo.consistency.checker import ContinuityChecker
from taletomo.consistency.models import FindingCategory, FindingSeverity, FindingStatus
from taletomo.generation.copilot import ProseCoPilotService
from taletomo.generation.models import DraftArtifact, DraftStatus
from taletomo.generation.pipeline import GenerationPipeline
from taletomo.planning.models import Chapter, ChapterPlan, Project, ScenePlan
from taletomo.planning.services import PlanningService


@pytest.mark.django_db
def test_e2e_lifecycle_improvements_integration(db, django_user_model, client):
    """Verifies end-to-end integration across Scene Re-roll, Voice Stylometry, and Foreshadowing Matrix."""
    # -------------------------------------------------------------------------
    # 1. SETUP PROJECT, CANON ENTITIES & PLOT THREADS
    # -------------------------------------------------------------------------
    user = django_user_model.objects.create_user(username="author_e2e", password="password123")
    client.force_login(user)

    project = PlanningService.create_project_with_scaffold(
        owner=user,
        title="Chronicles of the Sunstone",
        premise="A rogue warrior uncovers a conspiracy within the Sun Order.",
        genre="Dark Fantasy",
        target_chapters=10,
        target_words_per_chapter=1200,
    )

    inquisitor = Character.objects.create(
        project=project,
        name="High Inquisitor Malakor",
        role=CharacterRole.ANTAGONIST,
        dialogue_style="Aristocratic, archaic, solemn. Never uses contractions or modern slang.",
    )
    rowan = Character.objects.create(
        project=project,
        name="Rowan",
        role=CharacterRole.PROTAGONIST,
        dialogue_style="Terse, cynical, battle-hardened sellsword.",
    )

    sunstone_thread = PlotThread.objects.create(
        project=project,
        title="The Stolen Sunstone",
        setup_chapter=1,
        last_mentioned_chapter=1,
        dormancy_threshold=3,
    )
    sunstone_shard = Item.objects.create(
        project=project,
        name="Sunstone Shard",
        description="A blazing amber relic radiating intense thermal energy.",
    )

    # -------------------------------------------------------------------------
    # 2. CHAPTER 1 INITIAL DRAFT WITH SCENE BREAKS & VOICE DRIFT
    # -------------------------------------------------------------------------
    ch1 = Chapter.objects.get(project=project, chapter_number=1)
    plan1 = ChapterPlan.objects.get(chapter=ch1)
    plan1.status = ChapterPlan.Status.APPROVED
    plan1.save()
    plan1.scenes.all().delete()

    ScenePlan.objects.create(chapter_plan=plan1, scene_order=1, objective="Confrontation at the Cathedral gate", estimated_words=400)
    ScenePlan.objects.create(chapter_plan=plan1, scene_order=2, objective="Verbal spar and ultimatum", estimated_words=400)
    ScenePlan.objects.create(chapter_plan=plan1, scene_order=3, objective="Securing the relic and retreat", estimated_words=400)

    sc1_text = 'The rain pounded against the granite gargoyles. High Inquisitor Malakor barred the threshold. "Halt, infidel," Malakor commanded.'
    sc2_text_drift = 'Malakor smirked and waved his hand dismissively. "Yeah dude, you ain\'t gonna walk outta here with that crystal." Rowan stayed silent.'
    sc3_text = 'Rowan seized the glowing relic. The Sunstone Shard burned against his leather gauntlet as he dove into the storm.'

    composite_initial = f"{sc1_text}\n\n* * *\n\n{sc2_text_drift}\n\n* * *\n\n{sc3_text}"

    draft_v1 = DraftArtifact.objects.create(
        chapter=ch1,
        version_number=1,
        prose_content=composite_initial,
        word_count=len(composite_initial.split()),
        status=DraftStatus.UNDER_REVIEW,
    )
    ch1.active_draft_id = draft_v1.id
    ch1.save()

    # -------------------------------------------------------------------------
    # 3. VOICE AUDIT: ContinuityChecker DETECTS STYLOMETRIC DRIFT
    # -------------------------------------------------------------------------
    findings = ContinuityChecker.run_deterministic_checks(ch1, draft_v1.prose_content)
    voice_findings = [f for f in findings if f.category == FindingCategory.VOICE]

    assert len(voice_findings) >= 1
    malakor_findings = [f for f in voice_findings if "High Inquisitor Malakor" in f.claim]
    assert len(malakor_findings) >= 1
    assert any("gonna" in f.claim or "yeah" in f.claim or "dude" in f.claim for f in malakor_findings)

    # -------------------------------------------------------------------------
    # 4. PROSE CO-PILOT: VOICE ALIGNMENT
    # -------------------------------------------------------------------------
    mock_adapter = MagicMock()
    mock_copilot_resp = MagicMock()
    aligned_dialogue = '"Thou shalt not depart this sacred threshold with the relic alive, insolent dog."'
    mock_copilot_resp.content = aligned_dialogue
    mock_copilot_resp.total_tokens = 50
    mock_copilot_resp.cost_usd = 0.001
    mock_adapter.generate_text.return_value = mock_copilot_resp

    copilot_result = ProseCoPilotService.polish_selection(
        user=user,
        project=project,
        chapter=ch1,
        selected_text='"Yeah dude, you ain\'t gonna walk outta here with that crystal."',
        action="voice_align",
        target_character_name="High Inquisitor Malakor",
        custom_adapter=mock_adapter,
    )

    assert "Thou shalt not depart this sacred threshold" in copilot_result["suggested_text"]
    called_prompt = mock_adapter.generate_text.call_args[1]["prompt"]
    assert "High Inquisitor Malakor" in called_prompt
    assert "Never uses contractions" in called_prompt

    # -------------------------------------------------------------------------
    # 5. MODULAR SCENE RE-ROLL WITH SANDWICH CONTEXT SPLICING
    # -------------------------------------------------------------------------
    reroll_adapter = MagicMock()
    sc2_rerolled = 'Malakor stepped forward, raising his gilded staff. "Thou hast trespassed upon holy soil, Rowan. Surrender the relic or be consumed by the light."'

    def fake_reroll_generate(prompt, **kwargs):
        resp = MagicMock()
        resp.cost_usd = 0.001
        resp.total_tokens = 50
        sys = kwargs.get("system_prompt", "")
        if "TASK: DRAFT_SCENE_REROLL" in prompt:
            resp.content = sc2_rerolled
        elif "CRITIQUE" in prompt or "consistency" in sys.lower():
            resp.content = "[]"
        elif "story-bible" in sys.lower():
            resp.content = json.dumps({
                "events": [{"summary": "Rowan secures the relic", "event_type": "plot_progress"}],
                "claims": [],
                "threads_updated": [],
                "character_updates": [],
            })
        else:
            resp.content = "[]"
        return resp

    reroll_adapter.generate_text.side_effect = fake_reroll_generate

    draft_v2 = GenerationPipeline.execute_scene_reroll(
        chapter=ch1,
        scene_order=2,
        custom_instruction="Make Malakor's ultimatum solemn, archaic, and terrifying.",
        custom_adapter=reroll_adapter,
        user=user,
    )

    assert draft_v2.version_number == 2
    assert draft_v2.parent_draft == draft_v1
    assert draft_v2.status == DraftStatus.UNDER_REVIEW
    ch1.refresh_from_db()
    assert ch1.active_draft_id == draft_v2.id

    # Verify sandwich context passed to LLM during re-roll (first call)
    reroll_call_prompt = reroll_adapter.generate_text.call_args_list[0][1]["prompt"]
    assert sc1_text in reroll_call_prompt  # Sandwich Preceding Context
    assert sc3_text in reroll_call_prompt  # Sandwich Succeeding Context
    assert "solemn, archaic, and terrifying" in reroll_call_prompt

    # Verify composite prose splicing
    expected_composite = f"{sc1_text}\n\n* * *\n\n{sc2_rerolled}\n\n* * *\n\n{sc3_text}"
    assert draft_v2.prose_content == expected_composite

    # Verify voice findings on the re-rolled composite no longer complain about Scene 2 slang
    post_reroll_findings = ContinuityChecker.run_deterministic_checks(ch1, draft_v2.prose_content)
    post_voice_findings = [f for f in post_reroll_findings if f.category == FindingCategory.VOICE]
    assert len(post_voice_findings) == 0

    # -------------------------------------------------------------------------
    # 6. FORESHADOWING MATRIX: CHEKHOV'S GUN LINK & BREADCRUMBS
    # -------------------------------------------------------------------------
    matrix_url = reverse("taletomo:project_threads_matrix", kwargs={"project_id": project.id})

    # Link Sunstone Shard item to PlotThread via Web POST
    resp_link = client.post(matrix_url, {
        "action": "link_item",
        "thread_id": str(sunstone_thread.id),
        "item_id": str(sunstone_shard.id),
    })
    assert resp_link.status_code == 302
    sunstone_shard.refresh_from_db()
    assert sunstone_shard.plot_thread == sunstone_thread

    # Add breadcrumb via Web POST
    resp_bc = client.post(matrix_url, {
        "action": "add_breadcrumb",
        "thread_id": str(sunstone_thread.id),
        "chapter_number": 1,
        "breadcrumb_type": "setup",
        "description": "Rowan seizes the Sunstone Shard from the cathedral sanctuary.",
    })
    assert resp_bc.status_code == 302
    sunstone_thread.refresh_from_db()
    assert sunstone_thread.breadcrumbs.count() == 1
    assert sunstone_thread.last_mentioned_chapter == 1

    # Matrix view GET renders Chekhov Gun & Breadcrumb info
    resp_matrix = client.get(matrix_url)
    assert resp_matrix.status_code == 200
    content = resp_matrix.content.decode("utf-8")
    assert "The Stolen Sunstone" in content
    assert "Sunstone Shard" in content
    assert "cathedral sanctuary" in content

    # -------------------------------------------------------------------------
    # 7. COMMIT CHAPTER 1 CANON
    # -------------------------------------------------------------------------
    ch1.status = Chapter.Status.APPROVED
    ch1.save()
    draft_v2.status = DraftStatus.ACCEPTED
    draft_v2.save()

    CanonService.commit_chapter_canon(
        project=project,
        chapter=ch1,
        expected_head=project.active_branch_head,
        actor=user,
        thread_updates=[{
            "thread_title": "The Stolen Sunstone",
            "operation": "advance",
            "note": "Rowan escapes the cathedral with the relic.",
        }],
    )
    sunstone_thread.refresh_from_db()
    assert sunstone_thread.last_mentioned_chapter == 1
    assert "Rowan escapes the cathedral" in sunstone_thread.notes

    # -------------------------------------------------------------------------
    # 8. ADVANCE STORY TO CHAPTER 5: DORMANCY DETECTION & REPLAN REMINDERS
    # -------------------------------------------------------------------------
    # Threshold is 3 chapters; at Chapter 5, dormancy gap = 5 - 1 = 4 >= 3
    ch5 = Chapter.objects.get(project=project, chapter_number=5)

    dormancy_findings = ContinuityChecker.run_deterministic_checks(ch5, "The cold winds howled across the plains.")
    dormant_plot_findings = [f for f in dormancy_findings if f.category == FindingCategory.PLOT and "Dormant narrative thread" in f.claim]
    assert len(dormant_plot_findings) == 1
    assert "The Stolen Sunstone" in dormant_plot_findings[0].claim
    assert "has not been mentioned for 4 chapters" in dormant_plot_findings[0].claim

    # PlanningService replan frontier should now automatically inject a callback reminder
    replan_result = PlanningService.replan_frontier(project, from_chapter=5, horizon_size=2)
    assert len(replan_result["replanned_chapters"]) == 2

    plan5 = ChapterPlan.objects.get(chapter=ch5)
    assert any("The Stolen Sunstone" in beat and "dormant plot thread" in beat.lower() for beat in plan5.required_beats)

    # -------------------------------------------------------------------------
    # 9. PROSE CO-PILOT: INFILL BRIDGE BETWEEN SCENES
    # -------------------------------------------------------------------------
    mock_bridge_resp = MagicMock()
    mock_bridge_resp.content = "Hours turned into days as Rowan trekked through the frozen pines, watchful for Malakor's scouts."
    mock_bridge_resp.total_tokens = 45
    mock_bridge_resp.cost_usd = 0.001
    mock_adapter.generate_text.side_effect = None
    mock_adapter.generate_text.return_value = mock_bridge_resp

    bridge_result = ProseCoPilotService.polish_selection(
        user=user,
        project=project,
        chapter=ch5,
        selected_text="[Rowan flees the cathedral] ... [Rowan arrives at the mountain pass]",
        action="infill_bridge",
        custom_instruction="Bridge the transition across the wilderness.",
        custom_adapter=mock_adapter,
    )

    assert "frozen pines" in bridge_result["suggested_text"]
    bridge_call_prompt = mock_adapter.generate_text.call_args[1]["prompt"]
    assert "EDITORIAL_ACTION: INFILL_BRIDGE" in bridge_call_prompt
    assert "Bridge the transition across the wilderness" in bridge_call_prompt


@pytest.mark.django_db
def test_e2e_scene_reroll_web_interaction(db, django_user_model, client):
    """Verifies scene re-roll web endpoint error handling and successful redirection."""
    user = django_user_model.objects.create_user(username="web_reroll_author", password="password")
    client.force_login(user)

    project = Project.objects.create(owner=user, title="Web Reroll Tale")
    chapter = Chapter.objects.create(project=project, chapter_number=1, title="Opening")
    plan = ChapterPlan.objects.create(chapter=chapter, target_words=1000)
    ScenePlan.objects.create(chapter_plan=plan, scene_order=1, objective="The Arrival", estimated_words=500)

    DraftArtifact.objects.create(
        chapter=chapter,
        version_number=1,
        prose_content="The hero stepped off the carriage.",
        word_count=6,
    )

    # 1. Invalid scene_order (out of bounds)
    url_invalid = reverse("taletomo:chapter_scene_reroll", kwargs={
        "project_id": project.id,
        "chapter_id": chapter.id,
        "scene_order": 99,
    })
    resp_invalid = client.post(url_invalid, {"directive": "Fix pacing"})
    assert resp_invalid.status_code == 302
    # Verify redirected back to chapter edit
    assert reverse("taletomo:chapter_edit", kwargs={"project_id": project.id, "chapter_id": chapter.id}) in resp_invalid.url

    # 2. Locked chapter cannot be re-rolled
    chapter.status = Chapter.Status.LOCKED
    chapter.save()
    url_valid = reverse("taletomo:chapter_scene_reroll", kwargs={
        "project_id": project.id,
        "chapter_id": chapter.id,
        "scene_order": 1,
    })
    resp_locked = client.post(url_valid, {"directive": "Fix pacing"})
    assert resp_locked.status_code == 302


@pytest.mark.django_db
def test_e2e_matrix_edge_cases_and_unlink(db, django_user_model, client):
    """Verifies matrix unlinking Chekhov gun items and breadcrumb tracking."""
    user = django_user_model.objects.create_user(username="matrix_author", password="password")
    client.force_login(user)

    project = Project.objects.create(owner=user, title="Matrix Story")
    thread = PlotThread.objects.create(
        project=project,
        title="The Whispering Key",
        setup_chapter=1,
        last_mentioned_chapter=1,
    )
    key_item = Item.objects.create(
        project=project,
        name="Skeleton Key",
        plot_thread=thread,
    )

    matrix_url = reverse("taletomo:project_threads_matrix", kwargs={"project_id": project.id})

    # Unlink item via Web POST
    resp_unlink = client.post(matrix_url, {
        "action": "unlink_item",
        "item_id": str(key_item.id),
    })
    assert resp_unlink.status_code == 302
    key_item.refresh_from_db()
    assert key_item.plot_thread is None

    # Link item back
    resp_link = client.post(matrix_url, {
        "action": "link_item",
        "thread_id": str(thread.id),
        "item_id": str(key_item.id),
    })
    assert resp_link.status_code == 302
    key_item.refresh_from_db()
    assert key_item.plot_thread == thread

