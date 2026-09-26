from decimal import Decimal

import httpx
import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from taletomo.canon.models import CanonFact, Character, ProposedCanonItem
from taletomo.canon.services import CanonService
from taletomo.consistency.checker import ContinuityChecker
from taletomo.consistency.models import FindingCategory, FindingSeverity, FindingStatus
from taletomo.context.budget import BudgetCalculator
from taletomo.context.retrieval import ContextAssembler
from taletomo.generation.models import DraftStatus, GenerationJob, JobStatus
from taletomo.generation.pipeline import GenerationPipeline
from taletomo.planning.models import Chapter
from taletomo.planning.services import PlanningService
from taletomo.providers.adapters import FakeProviderAdapter, OpenAICompatibleAdapter
from taletomo.providers.models import BudgetReservation, ProviderConfig, ProviderType

User = get_user_model()


def _project(user, title):
    return PlanningService.create_project_with_scaffold(owner=user, title=title, premise="Quality fixture")


# --- Output sizing and budget derivation ------------------------------------


@pytest.mark.django_db
def test_output_token_budget_tracks_target_words():
    assert BudgetCalculator.estimate_output_tokens(2200) == 3300
    assert BudgetCalculator.estimate_output_tokens(500) == 1000  # floor clamp
    assert BudgetCalculator.estimate_output_tokens(10000) == 15000
    assert BudgetCalculator.estimate_output_tokens(10000, max_output=8000) == 8000


@pytest.mark.django_db
def test_pipeline_uses_word_target_for_output_and_reservation():
    user = User.objects.create_user(username="sizing_author")
    project = _project(user, "Sizing Chronicle")
    chapter = project.chapters.get(chapter_number=1)
    job = GenerationJob.objects.create(
        project=project,
        user=user,
        job_type="chapter_draft",
        idempotency_key="sizing-job",
        target_chapter_id=chapter.id,
    )
    config = ProviderConfig.objects.create(user=user, name="Fake", provider_type=ProviderType.FAKE)

    draft = GenerationPipeline.execute_chapter_generation(
        job=job, worker_id="sizing-worker", custom_adapter=FakeProviderAdapter(config)
    )

    expected_output = BudgetCalculator.estimate_output_tokens(chapter.plan.target_words)
    assert expected_output == 3300  # default 2200-word chapters

    reservation = BudgetReservation.objects.get(job_id=job.id)
    manifest = draft.context_manifest
    assert reservation.reserved_tokens == manifest.total_assembled_tokens + expected_output
    # The fake provider has no pricing profile: an honest zero-cost allowance.
    assert reservation.reserved_cost_usd == Decimal("0.0000")
    job.refresh_from_db()
    assert job.status == JobStatus.READY


# --- Prompt quality -----------------------------------------------------------


@pytest.mark.django_db
def test_context_includes_previous_chapter_ending():
    user = User.objects.create(username="ending_author")
    project = _project(user, "Chronicle of Endings")
    ch1 = project.chapters.get(chapter_number=1)
    ch2 = project.chapters.get(chapter_number=2)

    closing = "The brass gate groaned shut behind them, and the lamps of the lower ward dimmed one by one."
    from taletomo.generation.models import DraftArtifact

    draft = DraftArtifact.objects.create(
        chapter=ch1,
        version_number=1,
        prose_content="Opening line of the chapter. " * 20 + closing,
        word_count=220,
        status=DraftStatus.UNDER_REVIEW,
    )
    ch1.active_draft_id = draft.id
    ch1.save(update_fields=["active_draft_id"])

    package = ContextAssembler.assemble_chapter_context(ch2)

    assert "### PREVIOUS CHAPTER ENDING" in package.user_prompt
    assert closing in package.user_prompt
    entry_ids = [entry["id"] for entry in package.manifest.source_entries]
    assert f"prev-ending-{ch1.id}" in entry_ids


@pytest.mark.django_db
def test_scene_prompts_carry_word_budgets():
    user = User.objects.create(username="scene_words_author")
    project = _project(user, "Chronicle of Scene Budgets")
    chapter = project.chapters.get(chapter_number=1)

    package = ContextAssembler.assemble_chapter_context(chapter)

    first_scene = chapter.plan.scenes.first()
    assert f"Scene {first_scene.scene_order} (~{first_scene.estimated_words} words)" in package.user_prompt


# --- Continuity quality --------------------------------------------------------


@pytest.mark.django_db
def test_generalized_injury_detection_matches_limbs_from_wound_records():
    user = User.objects.create(username="generalized_injury_author")
    project = _project(user, "Chronicle of Hurting Knees")
    chapter = project.chapters.get(chapter_number=1)
    Character.objects.create(project=project, name="Boris", wounds_status="Right knee shattered and splinted")

    flagged = ContinuityChecker.run_deterministic_checks(
        chapter, "Boris sprinted across the courtyard, and his right knee screamed with every stride."
    )
    injury = next(f for f in flagged if f.category == FindingCategory.INJURY)
    assert injury.severity == FindingSeverity.BLOCKER
    assert "Boris" in injury.claim

    # An unrelated activity does not trigger the knee record.
    clean = ContinuityChecker.run_deterministic_checks(
        chapter, "Boris walked to the market and bought apples without hurry."
    )
    assert not any(f.category == FindingCategory.INJURY for f in clean)

    # A leg injury does not prohibit two-handed actions (precision improvement
    # over the old keyword list).
    both_hands = ContinuityChecker.run_deterministic_checks(
        chapter, "Boris climbed using both hands, hauling himself onto the wall."
    )
    assert not any(f.category == FindingCategory.INJURY for f in both_hands)


@pytest.mark.django_db
def test_non_physical_wound_notes_never_block_prose():
    user = User.objects.create(username="nonphysical_author")
    project = _project(user, "Chronicle of Calm Fears")
    chapter = project.chapters.get(chapter_number=1)
    Character.objects.create(project=project, name="Calm", wounds_status="afraid of crowds")

    findings = ContinuityChecker.run_deterministic_checks(
        chapter, "Calm clapped with both hands and cheered the players."
    )
    assert not any(f.category == FindingCategory.INJURY for f in findings)


@pytest.mark.django_db
def test_manual_saves_run_deduplicated_continuity_checks():
    user = User.objects.create_user(username="manual_check_author")
    project = _project(user, "Chronicle of Manual Edits")
    chapter = project.chapters.get(chapter_number=1)
    Character.objects.create(project=project, name="Alaric", wounds_status="Left hand shattered and bandaged")

    client = Client()
    client.force_login(user)
    prose = "Alaric held the sword in his left hand despite the pain."
    url = reverse("taletomo:chapter_edit", args=[project.id, chapter.id])

    client.post(url, {"prose_content": prose})
    findings = chapter.continuity_findings.filter(category=FindingCategory.INJURY)
    assert findings.count() == 1
    assert findings.first().severity == FindingSeverity.BLOCKER

    # Saving a new version with the same contradiction must not duplicate the
    # open finding.
    client.post(url, {"prose_content": prose + " A second pass."})
    assert (
        chapter.continuity_findings.filter(
            category=FindingCategory.INJURY, status=FindingStatus.OPEN
        ).count()
        == 1
    )


# --- Canon memory quality ------------------------------------------------------


@pytest.mark.django_db
def test_extraction_proposes_character_updates_and_commit_applies_them():
    user = User.objects.create_user(username="charupdate_author")
    project = _project(user, "Chronicle of Changing Wounds")
    chapter = project.chapters.get(chapter_number=1)
    alaric = Character.objects.create(project=project, name="Alaric Vance", wounds_status="")
    config = ProviderConfig.objects.create(user=user, name="Fake", provider_type=ProviderType.FAKE)
    job = GenerationJob.objects.create(
        project=project,
        user=user,
        job_type="chapter_draft",
        idempotency_key="charupdate-job",
        target_chapter_id=chapter.id,
    )

    GenerationPipeline.execute_chapter_generation(
        job=job, worker_id="charupdate-worker", custom_adapter=FakeProviderAdapter(config)
    )

    updates = list(chapter.proposed_canon_items.filter(kind=ProposedCanonItem.Kind.CHARACTER_UPDATE))
    # "Alaric" resolves to Alaric Vance; the unknown stranger is never proposed.
    assert len(updates) == 1
    assert updates[0].payload == {
        "name": "Alaric Vance",
        "field": "wounds_status",
        "value": "Left wrist shattered and freshly bandaged",
        "note": "Prose: his shattered left wrist throbbing in the damp cold",
    }

    # Approve and commit: the character record updates inside the canon commit.
    updates[0].status = ProposedCanonItem.Status.APPROVED
    updates[0].save()
    chapter.refresh_from_db()  # the pipeline updated active_draft_id on the DB row
    draft = chapter.drafts.get(version_number=1)
    draft.status = DraftStatus.ACCEPTED
    draft.save()
    chapter.status = Chapter.Status.APPROVED
    chapter.save(update_fields=["status"])

    CanonService.commit_chapter_canon(
        project=project,
        chapter=chapter,
        expected_head=project.active_branch_head,
        events=[{"summary": "Alaric escapes.", "event_type": "plot_progress"}],
        facts=[],
        actor=user,
        character_updates=[updates[0].payload],
    )

    alaric.refresh_from_db()
    assert alaric.wounds_status == "Left wrist shattered and freshly bandaged"
    # The service is proposal-agnostic; consuming approved proposals is the
    # view's responsibility (covered by the approve→commit view loop test).
    updates[0].refresh_from_db()
    assert updates[0].status == ProposedCanonItem.Status.APPROVED


@pytest.mark.django_db
def test_extraction_skips_facts_canon_already_confirms():
    user = User.objects.create_user(username="factdedupe_author")
    project = _project(user, "Chronicle of Known Facts")
    chapter = project.chapters.get(chapter_number=1)
    config = ProviderConfig.objects.create(user=user, name="Fake", provider_type=ProviderType.FAKE)
    job = GenerationJob.objects.create(
        project=project,
        user=user,
        job_type="chapter_draft",
        idempotency_key="factdedupe-job",
        target_chapter_id=chapter.id,
    )

    CanonFact.objects.create(
        project=project,
        subject="Alaric",
        predicate="current_location",
        value="Old Aqueduct",
        canonical_status=CanonFact.Status.CONFIRMED,
    )

    GenerationPipeline.execute_chapter_generation(
        job=job, worker_id="factdedupe-worker", custom_adapter=FakeProviderAdapter(config)
    )

    fact_proposals = list(chapter.proposed_canon_items.filter(kind=ProposedCanonItem.Kind.FACT))
    # The Alaric/current_location claim is already confirmed; only the fresh
    # Captain Vance claim is proposed.
    assert [p.payload["subject"] for p in fact_proposals] == ["Captain Vance"]


# --- Provider robustness --------------------------------------------------------


def _chat_response():
    return httpx.Response(
        200,
        json={
            "id": "resp-1",
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        },
        request=httpx.Request("POST", "https://api.example.com/v1/chat/completions"),
    )


def _adapter_with_handler(handler):
    config = ProviderConfig(
        name="Retry fixture",
        provider_type=ProviderType.OPENAI_COMPATIBLE,
        endpoint_url="https://api.example.com/v1",
        default_drafting_model="test-model",
    )
    # The allowlist short-circuits SSRF DNS resolution so the test stays offline.
    return OpenAICompatibleAdapter(
        config,
        allowlist=["api.example.com"],
        transport=httpx.MockTransport(handler),
        retry_backoff_seconds=0,
    )


@pytest.mark.django_db
def test_openai_adapter_retries_connection_failures_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("connection refused")
        return _chat_response()

    response = _adapter_with_handler(handler).generate_text(prompt="hello", max_tokens=10)
    assert response.content == "ok"
    assert calls["n"] == 2


@pytest.mark.django_db
def test_openai_adapter_retries_rate_limits_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, text="slow down", request=request)
        return _chat_response()

    response = _adapter_with_handler(handler).generate_text(prompt="hello", max_tokens=10)
    assert response.content == "ok"
    assert calls["n"] == 2


@pytest.mark.django_db
def test_openai_adapter_persistent_connection_failure_raises():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.ConnectError("connection refused")

    with pytest.raises(RuntimeError, match="could not be reached"):
        _adapter_with_handler(handler).generate_text(prompt="hello", max_tokens=10)
    assert calls["n"] == 2  # exactly one retry, no more


@pytest.mark.django_db
def test_openai_adapter_read_timeout_stays_unknown_outcome():
    """A timeout after submission must raise TimeoutError with no retry: billing is unknown."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.ReadTimeout("read timed out")

    with pytest.raises(TimeoutError):
        _adapter_with_handler(handler).generate_text(prompt="hello", max_tokens=10)
    assert calls["n"] == 1
