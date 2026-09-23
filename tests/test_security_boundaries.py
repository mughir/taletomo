import pytest
from django.contrib.auth import get_user_model
from taletomo.canon.services import CanonService
from taletomo.consistency.models import ContinuityFinding, FindingCategory, FindingSeverity, FindingStatus
from taletomo.core.models import AuditLog
from taletomo.generation.models import DraftArtifact, DraftStatus
from taletomo.planning.models import Chapter, Project
from taletomo.planning.services import PlanningService
from taletomo.providers.adapters import ProviderGateway
from taletomo.providers.models import ProviderConfig, ProviderType

User = get_user_model()


@pytest.mark.django_db
def test_foreign_actor_cannot_commit_another_authors_chapter():
    owner = User.objects.create_user(username="canon_owner")
    stranger = User.objects.create_user(username="canon_stranger")
    project = PlanningService.create_project_with_scaffold(
        owner=owner, title="Private canon", premise="An author's private story"
    )
    chapter = project.chapters.get(chapter_number=1)
    draft = DraftArtifact.objects.create(
        chapter=chapter,
        version_number=1,
        prose_content="The heroine returns home.",
        word_count=4,
        status=DraftStatus.ACCEPTED,
    )
    chapter.active_draft_id = draft.id
    chapter.status = Chapter.Status.APPROVED
    chapter.save(update_fields=["active_draft_id", "status"])
    expected_head = project.active_branch_head

    with pytest.raises(PermissionError, match="owner"):
        CanonService.commit_chapter_canon(
            project=project,
            chapter=chapter,
            expected_head=expected_head,
            events=[{"summary": "Unauthorized event"}],
            facts=[],
            actor=stranger,
        )

    project.refresh_from_db()
    assert project.active_branch_head == expected_head
    assert not project.story_events.exists()


@pytest.mark.django_db
def test_blocker_override_requires_a_nonempty_rationale():
    owner = User.objects.create_user(username="override_owner")
    project = PlanningService.create_project_with_scaffold(
        owner=owner, title="Override policy", premise="An unresolved contradiction"
    )
    chapter = project.chapters.get(chapter_number=1)
    draft = DraftArtifact.objects.create(
        chapter=chapter,
        version_number=1,
        prose_content="The hero enters the hall.",
        word_count=5,
        status=DraftStatus.ACCEPTED,
    )
    chapter.active_draft_id = draft.id
    chapter.status = Chapter.Status.APPROVED
    chapter.save(update_fields=["active_draft_id", "status"])
    ContinuityFinding.objects.create(
        project=project,
        chapter=chapter,
        category=FindingCategory.IDENTITY,
        severity=FindingSeverity.BLOCKER,
        status=FindingStatus.OPEN,
        claim="Unresolved identity contradiction",
    )
    expected_head = project.active_branch_head

    with pytest.raises(ValueError, match="rationale"):
        CanonService.commit_chapter_canon(
            project=project,
            chapter=chapter,
            expected_head=expected_head,
            events=[{"summary": "Override without explanation"}],
            facts=[],
            actor=owner,
            override_blockers=True,
            override_rationale="",
        )

    project.refresh_from_db()
    assert project.active_branch_head == expected_head
    assert not project.story_events.exists()

    rationale = "The evidence shows the king is a recorded illusion."
    CanonService.commit_chapter_canon(
        project=project,
        chapter=chapter,
        expected_head=expected_head,
        events=[{"summary": "Authorized override"}],
        facts=[],
        actor=owner,
        override_blockers=True,
        override_rationale=rationale,
    )
    audit = AuditLog.objects.get(action="CANON_COMMIT", target_id=str(chapter.id))
    assert rationale in audit.reason


@pytest.mark.django_db
def test_provider_gateway_rejects_another_users_explicit_config():
    owner = User.objects.create_user(username="provider_config_owner")
    stranger = User.objects.create_user(username="provider_config_stranger")
    stranger_project = PlanningService.create_project_with_scaffold(
        owner=stranger, title="Other provider", premise="Must not use foreign credentials"
    )
    foreign_config = ProviderConfig.objects.create(
        user=owner,
        name="Owner-only provider",
        provider_type=ProviderType.FAKE,
        is_active=True,
    )

    with pytest.raises(PermissionError, match="does not belong"):
        ProviderGateway.get_adapter(
            config=foreign_config,
            user=stranger,
            project=stranger_project,
        )


@pytest.mark.django_db
def test_locked_chapter_rejects_modifications(client):
    owner = User.objects.create_user(username="locked_author")
    client.force_login(owner)
    project = PlanningService.create_project_with_scaffold(
        owner=owner, title="Locked Novel", premise="Story about locked canon", target_chapters=2
    )
    ch1 = project.chapters.get(chapter_number=1)
    ch1.status = Chapter.Status.LOCKED
    ch1.save(update_fields=["status"])

    # 1. Reject draft generation on locked chapter
    res = client.post(f"/projects/{project.id}/chapters/{ch1.id}/generate/")
    assert res.status_code == 302
    assert not ch1.drafts.exists()

    # 2. Reject manual edits on locked chapter
    res = client.post(f"/projects/{project.id}/chapters/{ch1.id}/edit/", {"prose_content": "Illegal new prose"})
    assert res.status_code == 302
    assert not ch1.drafts.exists()

    # 3. Reject contract updates on locked chapter
    res = client.post(f"/projects/{project.id}/chapters/{ch1.id}/plan/", {"target_words": 3000})
    assert res.status_code == 302


@pytest.mark.django_db
def test_first_manual_draft_created_when_no_active_draft(client):
    owner = User.objects.create_user(username="first_draft_author")
    client.force_login(owner)
    project = PlanningService.create_project_with_scaffold(
        owner=owner, title="First Draft Novel", premise="Fresh story", target_chapters=2
    )
    ch1 = project.chapters.get(chapter_number=1)
    assert ch1.active_draft_id is None
    assert ch1.drafts.count() == 0

    res = client.post(f"/projects/{project.id}/chapters/{ch1.id}/edit/", {"prose_content": "The first words ever written."})
    assert res.status_code == 302

    ch1.refresh_from_db()
    assert ch1.drafts.count() == 1
    assert ch1.active_draft_id is not None
    assert ch1.status == Chapter.Status.REVIEW
    assert ch1.drafts.first().version_number == 1
    assert "first words" in ch1.drafts.first().prose_content


@pytest.mark.django_db
def test_compare_drafts_rejects_cross_chapter_diffing(client):
    owner = User.objects.create_user(username="diff_author")
    client.force_login(owner)
    project = PlanningService.create_project_with_scaffold(
        owner=owner, title="Diff Novel", premise="Comparing chapters", target_chapters=3
    )
    ch1 = project.chapters.get(chapter_number=1)
    ch2 = project.chapters.get(chapter_number=2)

    d1 = DraftArtifact.objects.create(chapter=ch1, version_number=1, prose_content="Chapter 1 content")
    d2 = DraftArtifact.objects.create(chapter=ch2, version_number=1, prose_content="Chapter 2 content")

    res = client.get(f"/projects/{project.id}/drafts/{d1.id}/compare/{d2.id}/")
    assert res.status_code == 404


@pytest.mark.django_db
def test_job_cancel_releases_budget_reservation(client):
    from decimal import Decimal
    from taletomo.generation.models import GenerationJob, JobStatus
    from taletomo.providers.models import BudgetReservation

    owner = User.objects.create_user(username="canceller")
    client.force_login(owner)
    project = PlanningService.create_project_with_scaffold(
        owner=owner, title="Budget Novel", premise="Budget test", target_chapters=2
    )
    ch1 = project.chapters.get(chapter_number=1)
    job = GenerationJob.objects.create(
        project=project,
        user=owner,
        job_type="chapter_draft",
        idempotency_key="job-cancel-test",
        target_chapter_id=ch1.id,
        status=JobStatus.QUEUED,
    )
    res = BudgetReservation.objects.create(
        user=owner,
        project_id=project.id,
        job_id=job.id,
        reserved_tokens=2000,
        reserved_cost_usd=Decimal("0.0400"),
        status=BudgetReservation.Status.RESERVED,
    )

    client.post(f"/jobs/{job.id}/cancel/")

    job.refresh_from_db()
    res.refresh_from_db()
    assert job.status == JobStatus.CANCELLED
    assert res.status == BudgetReservation.Status.RELEASED


@pytest.mark.django_db
def test_context_processor_scopes_provider_to_current_user(rf):
    from taletomo.web.context_processors import taletomo_context
    user1 = User.objects.create_user(username="user1")
    user2 = User.objects.create_user(username="user2")

    cfg1 = ProviderConfig.objects.create(
        user=user1, name="User1 Config", is_active=True
    )
    cfg2 = ProviderConfig.objects.create(
        user=user2, name="User2 Config", is_active=True
    )

    request1 = rf.get("/")
    request1.user = user1
    ctx1 = taletomo_context(request1)
    assert ctx1["active_provider"] == cfg1

    request2 = rf.get("/")
    request2.user = user2
    ctx2 = taletomo_context(request2)
    assert ctx2["active_provider"] == cfg2

    from django.contrib.auth.models import AnonymousUser
    request_anon = rf.get("/")
    request_anon.user = AnonymousUser()
    ctx_anon = taletomo_context(request_anon)
    assert ctx_anon["active_provider"] is None

