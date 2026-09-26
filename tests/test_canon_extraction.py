import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from taletomo.canon.extraction import CanonExtractionError, CanonExtractionService
from taletomo.canon.models import CanonFact, PlotThread, ProposedCanonItem
from taletomo.generation.models import DraftStatus, GenerationJob, JobStatus
from taletomo.generation.pipeline import GenerationPipeline
from taletomo.planning.models import Chapter
from taletomo.planning.services import PlanningService
from taletomo.providers.adapters import BaseProviderAdapter, FakeProviderAdapter
from taletomo.providers.models import ProviderConfig, ProviderType

User = get_user_model()


def _setup(usernamed_suffix: str):
    user = User.objects.create_user(username=f"canon_author_{usernamed_suffix}")
    project = PlanningService.create_project_with_scaffold(
        owner=user,
        title=f"Canon Memory {usernamed_suffix}",
        premise="Testing the extraction and review loop",
    )
    config = ProviderConfig.objects.create(
        user=user,
        name="Fake provider fixture",
        provider_type=ProviderType.FAKE,
        is_default=True,
        is_active=True,
    )
    chapter = project.chapters.get(chapter_number=1)
    job = GenerationJob.objects.create(
        project=project,
        user=user,
        job_type="chapter_draft",
        idempotency_key=f"canon-extract-{usernamed_suffix}",
        target_chapter_id=chapter.id,
    )
    return user, project, config, chapter, job


@pytest.mark.django_db
def test_pipeline_persists_canon_proposals_for_review():
    user, project, config, chapter, job = _setup("pipeline")
    draft = GenerationPipeline.execute_chapter_generation(
        job=job, worker_id="test-worker", custom_adapter=FakeProviderAdapter(config)
    )

    job.refresh_from_db()
    assert job.status == JobStatus.READY

    proposals = list(chapter.proposed_canon_items.all())
    kinds = sorted(p.kind for p in proposals)
    assert kinds == ["event", "fact", "fact", "thread_update"]
    assert all(p.draft_id == draft.id for p in proposals)
    assert all(p.status == ProposedCanonItem.Status.PROPOSED for p in proposals)

    fact_payloads = [p.payload for p in proposals if p.kind == ProposedCanonItem.Kind.FACT]
    assert {"subject": "Alaric", "predicate": "current_location", "value": "Old Aqueduct"} in [
        {k: v for k, v in payload.items() if k in ("subject", "predicate", "value")}
        for payload in fact_payloads
    ]

    thread = next(p for p in proposals if p.kind == ProposedCanonItem.Kind.THREAD_UPDATE)
    assert thread.payload["thread_title"] == "City Guard Investigation"
    assert thread.payload["operation"] == "advance"


@pytest.mark.django_db
def test_extraction_replaces_pending_but_preserves_reviewed():
    user, project, config, chapter, job = _setup("replace")
    draft = GenerationPipeline.execute_chapter_generation(
        job=job, worker_id="test-worker", custom_adapter=FakeProviderAdapter(config)
    )
    assert chapter.proposed_canon_items.count() == 4

    keeper = chapter.proposed_canon_items.first()
    keeper.status = ProposedCanonItem.Status.APPROVED
    keeper.save()

    CanonExtractionService.extract_from_draft(
        chapter=chapter, draft=draft, adapter=FakeProviderAdapter(config)
    )

    statuses = list(chapter.proposed_canon_items.values_list("status", flat=True))
    assert statuses.count(ProposedCanonItem.Status.APPROVED) == 1  # Reviewed item kept
    assert statuses.count(ProposedCanonItem.Status.PROPOSED) == 4  # Pending replaced


@pytest.mark.django_db
def test_extraction_invalid_json_raises_without_touching_proposals():
    user, project, config, chapter, job = _setup("badjson")

    class GarbageAdapter(BaseProviderAdapter):
        def generate_text(self, **kwargs):
            from taletomo.providers.adapters import ProviderResponse
            from decimal import Decimal

            return ProviderResponse(content="not json at all", cost_usd=Decimal("0"))

    chapter.proposed_canon_items.create(
        project=project,
        kind=ProposedCanonItem.Kind.EVENT,
        payload={"summary": "existing proposal", "event_type": "plot_progress"},
        summary="existing proposal",
    )

    from taletomo.generation.models import DraftArtifact

    draft_obj = DraftArtifact.objects.create(
        chapter=chapter,
        version_number=1,
        prose_content="Some prose",
        word_count=2,
        status=DraftStatus.UNDER_REVIEW,
    )

    with pytest.raises(CanonExtractionError):
        CanonExtractionService.extract_from_draft(
            chapter=chapter, draft=draft_obj, adapter=GarbageAdapter(config)
        )

    assert chapter.proposed_canon_items.count() == 1
    assert chapter.proposed_canon_items.first().summary == "existing proposal"


@pytest.mark.django_db
def test_extraction_coerces_unknown_or_noncanonical_scopes():
    from taletomo.canon.models import TruthScope

    user, project, config, chapter, _job = _setup("scopes")
    config = ProviderConfig.objects.get(id=config.id)

    class ScriptedAdapter(FakeProviderAdapter):
        def generate_text(self, **kwargs):
            resp = super().generate_text(**kwargs)
            if "TASK: EXTRACT_CANON" in kwargs.get("prompt", ""):
                resp.content = (
                    '{"events": [], "claims": ['
                    '{"subject": "A", "predicate": "p1", "value": "v1", "scope": "character_belief"},'
                    '{"subject": "B", "predicate": "p2", "value": "v2", "scope": "garbage_scope"},'
                    '{"subject": "C", "predicate": "p3", "value": "v3", "scope": "plan_only"}],'
                    '"threads_updated": []}'
                )
            return resp

    draft = GenerationPipeline.execute_chapter_generation(
        job=GenerationJob.objects.create(
            project=project,
            user=user,
            job_type="chapter_draft",
            idempotency_key="scope-coercion-job",
            target_chapter_id=chapter.id,
        ),
        worker_id="test-worker",
        custom_adapter=ScriptedAdapter(config),
    )

    scopes = {
        p.payload["subject"]: p.payload["scope"]
        for p in chapter.proposed_canon_items.filter(kind=ProposedCanonItem.Kind.FACT)
    }
    assert scopes["A"] == TruthScope.CHARACTER_BELIEF  # Valid scope preserved
    assert scopes["B"] == TruthScope.WORLD_TRUTH  # Unknown scope coerced
    assert scopes["C"] == TruthScope.WORLD_TRUTH  # Non-canonical draft scope coerced


@pytest.mark.django_db
def test_approve_review_commit_loop_creates_canon_and_advances_threads():
    user, project, config, chapter, job = _setup("commit")
    GenerationPipeline.execute_chapter_generation(
        job=job, worker_id="test-worker", custom_adapter=FakeProviderAdapter(config)
    )
    draft = chapter.drafts.get(version_number=1)

    client = Client()
    client.force_login(user)

    # Phase 1: approve prose
    client.post(reverse("taletomo:chapter_approve_draft", args=[project.id, chapter.id]))
    chapter.refresh_from_db()
    assert chapter.status == Chapter.Status.APPROVED

    # Review proposals: approve facts + thread update, reject the event
    items = list(chapter.proposed_canon_items.all())
    thread_item = next(i for i in items if i.kind == ProposedCanonItem.Kind.THREAD_UPDATE)
    event_item = next(i for i in items if i.kind == ProposedCanonItem.Kind.EVENT)
    fact_items = [i for i in items if i.kind == ProposedCanonItem.Kind.FACT]
    for item in fact_items + [thread_item]:
        response = client.post(
            reverse("taletomo:proposed_canon_update", args=[item.id]),
            {"action": "approve", "review_note": "Looks right"},
            follow=True,
        )
        assert response.status_code == 200
    client.post(reverse("taletomo:proposed_canon_update", args=[event_item.id]), {"action": "reject"})

    thread_item.refresh_from_db()
    event_item.refresh_from_db()
    assert thread_item.status == ProposedCanonItem.Status.APPROVED
    assert thread_item.review_note == "Looks right"
    assert event_item.status == ProposedCanonItem.Status.REJECTED

    # Phase 2: commit canon
    response = client.post(
        reverse("taletomo:chapter_commit_canon", args=[project.id, chapter.id]),
        {"expected_head": project.active_branch_head, "summary": "Alaric escapes."},
        follow=True,
    )
    assert response.status_code == 200

    project.refresh_from_db()
    chapter.refresh_from_db()
    assert project.active_branch_head == "rev_2"
    assert chapter.status == Chapter.Status.LOCKED

    # Approved facts committed with provenance; rejected event excluded
    facts = CanonFact.objects.filter(project=project)
    assert facts.count() == 2
    assert all(f.provenance == "Chapter 1" for f in facts)

    thread = PlotThread.objects.get(project=project, title="City Guard Investigation")
    assert thread.status == PlotThread.Status.PROGRESSING
    assert "Vance discovered the lab" in thread.notes

    for item in chapter.proposed_canon_items.all():
        if item.id in {approved.id for approved in fact_items + [thread_item]}:
            assert item.status == ProposedCanonItem.Status.CONSUMED
        else:
            assert item.status == ProposedCanonItem.Status.REJECTED


@pytest.mark.django_db
def test_canon_review_pages_and_actions_are_owner_scoped():
    owner, project, config, chapter, job = _setup("scope_owner")
    GenerationPipeline.execute_chapter_generation(
        job=job, worker_id="test-worker", custom_adapter=FakeProviderAdapter(config)
    )
    intruder = User.objects.create_user(username="canon_intruder")

    review_url = reverse("taletomo:chapter_canon_review", args=[project.id, chapter.id])
    item = chapter.proposed_canon_items.first()
    update_url = reverse("taletomo:proposed_canon_update", args=[item.id])

    client = Client()
    client.force_login(intruder)
    assert client.get(review_url).status_code == 404
    assert client.post(update_url, {"action": "approve"}).status_code == 404

    item.refresh_from_db()
    assert item.status == ProposedCanonItem.Status.PROPOSED

    # The owner can render the review page with pending proposals visible.
    owner_client = Client()
    owner_client.force_login(owner)
    response = owner_client.get(review_url)
    assert response.status_code == 200
    content = response.content.decode()
    assert "Pending proposals" in content
    assert "City Guard Investigation" in content


@pytest.mark.django_db
def test_manual_extraction_view_handles_missing_draft_and_locked_chapter():
    user, project, config, chapter, _job = _setup("manual")
    client = Client()
    client.force_login(user)
    extract_url = reverse("taletomo:chapter_extract_canon", args=[project.id, chapter.id])

    # No active draft yet
    response = client.post(extract_url, follow=True)
    assert "No active draft" in response.content.decode()

    # Locked chapters refuse extraction
    chapter.status = Chapter.Status.LOCKED
    chapter.save()
    response = client.post(extract_url, follow=True)
    assert "locked" in response.content.decode().lower()


@pytest.mark.django_db
def test_reviewed_items_cannot_be_re_reviewed():
    user, project, config, chapter, _job = _setup("rereview")
    item = chapter.proposed_canon_items.create(
        project=project,
        kind=ProposedCanonItem.Kind.FACT,
        payload={"subject": "S", "predicate": "p", "value": "v", "scope": "world_truth"},
        summary="S p v",
    )
    item.status = ProposedCanonItem.Status.APPROVED
    item.save()

    client = Client()
    client.force_login(user)
    client.post(reverse("taletomo:proposed_canon_update", args=[item.id]), {"action": "reject"})

    item.refresh_from_db()
    assert item.status == ProposedCanonItem.Status.APPROVED
