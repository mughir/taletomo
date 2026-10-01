import datetime
from decimal import Decimal
import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from taletomo.canon.extraction import CanonExtractionService
from taletomo.canon.models import Character, ProposedCanonItem
from taletomo.canon.services import CanonService
from taletomo.consistency.checker import ContinuityChecker
from taletomo.consistency.models import ContinuityFinding, FindingCategory, FindingSeverity, FindingStatus
from taletomo.generation.models import DraftArtifact, DraftStatus, GenerationJob, JobStatus
from taletomo.generation.pipeline import GenerationPipeline
from taletomo.planning.models import Chapter, ChapterPlan, Project
from taletomo.planning.services import PlanningService
from taletomo.providers.adapters import FakeProviderAdapter
from taletomo.providers.models import ProviderConfig, ProviderType

User = get_user_model()


@pytest.mark.django_db
def test_manual_edit_on_approved_chapter_resets_status_to_review_and_allows_approval(client):
    """Bug 1 Regression:
    Editing an approved chapter must transition chapter status back to REVIEW so the author
    can approve the new draft before committing canon, avoiding an unrecoverable UI state.
    """
    owner = User.objects.create_user(username="author1")
    client.force_login(owner)
    project = PlanningService.create_project_with_scaffold(
        owner=owner, title="Deadlock Test", premise="Testing edit after approval", target_chapters=2
    )
    ch1 = project.chapters.get(chapter_number=1)

    # 1. Create and accept initial draft
    d1 = DraftArtifact.objects.create(
        chapter=ch1,
        version_number=1,
        prose_content="The guard stood vigilant at the iron gates.",
        word_count=8,
        status=DraftStatus.ACCEPTED,
    )
    ch1.active_draft_id = d1.id
    ch1.status = Chapter.Status.APPROVED
    ch1.current_word_count = 8
    ch1.save()

    # 2. Author makes a manual edit in chapter_edit
    edit_url = reverse("taletomo:chapter_edit", args=[project.id, ch1.id])
    res = client.post(edit_url, {"prose_content": "The guard stood vigilant at the iron gates, watching the snow."})
    assert res.status_code == 302

    ch1.refresh_from_db()
    d2 = DraftArtifact.objects.get(id=ch1.active_draft_id)
    assert d2.version_number == 2
    assert d2.status == DraftStatus.UNDER_REVIEW
    # Chapter status must be REVIEW (not stuck in APPROVED)
    assert ch1.status == Chapter.Status.REVIEW
    assert ch1.current_word_count == 11

    # 3. Step 1 (Approve draft) is accessible and functions cleanly
    approve_url = reverse("taletomo:chapter_approve_draft", args=[project.id, ch1.id])
    res_approve = client.post(approve_url)
    assert res_approve.status_code == 302

    ch1.refresh_from_db()
    d2.refresh_from_db()
    assert ch1.status == Chapter.Status.APPROVED
    assert d2.status == DraftStatus.ACCEPTED

    # 4. Step 2 (Commit canon) now succeeds without domain policy errors
    commit_url = reverse("taletomo:chapter_commit_canon", args=[project.id, ch1.id])
    res_commit = client.post(
        commit_url,
        {
            "expected_head": project.active_branch_head,
            "summary_text": "Guard watches snow by the iron gates.",
        },
    )
    assert res_commit.status_code == 302
    ch1.refresh_from_db()
    assert ch1.status == Chapter.Status.LOCKED


@pytest.mark.django_db
def test_job_retry_and_cancel_clear_lease_fields(client):
    """Bug 2 Regression:
    job_retry and job_cancel must clear lease_worker_id and lease_expires_at so that
    retried tasks can be immediately leased by another worker and not skipped.
    """
    owner = User.objects.create_user(username="retry_user")
    client.force_login(owner)
    project = PlanningService.create_project_with_scaffold(
        owner=owner, title="Lease Retry Test", premise="Testing lease clearance", target_chapters=2
    )
    ch1 = project.chapters.get(chapter_number=1)

    future_expiry = timezone.now() + datetime.timedelta(seconds=90)
    job = GenerationJob.objects.create(
        user=owner,
        project=project,
        target_chapter_id=ch1.id,
        status=JobStatus.FAILED,
        stage="Prior run failed",
        lease_worker_id="old-worker-dead",
        lease_expires_at=future_expiry,
    )

    # 1. Retry clears lease
    retry_url = reverse("taletomo:job_retry", args=[job.id])
    res = client.post(retry_url)
    assert res.status_code == 302

    job.refresh_from_db()
    assert job.status == JobStatus.QUEUED
    assert job.lease_worker_id is None
    assert job.lease_expires_at is None

    # A new worker must be able to acquire the lease immediately
    acquired = job.acquire_lease(worker_id="celery-new-worker-1", duration_seconds=60)
    assert acquired is True
    assert job.lease_worker_id == "celery-new-worker-1"

    # 2. Cancel also clears lease
    cancel_url = reverse("taletomo:job_cancel", args=[job.id])
    res_cancel = client.post(cancel_url)
    assert res_cancel.status_code == 302

    job.refresh_from_db()
    assert job.status == JobStatus.CANCELLED
    assert job.lease_worker_id is None
    assert job.lease_expires_at is None


@pytest.mark.django_db
def test_pipeline_sets_chapter_current_word_count_and_trailing_slash_result_url():
    """Bug 3 & 8 Regression:
    execute_chapter_generation must set chapter.current_word_count to draft.word_count,
    and job.result_url must end with a trailing slash.
    """
    owner = User.objects.create_user(username="gen_word_count_user")
    project = PlanningService.create_project_with_scaffold(
        owner=owner, title="Word Count Test", premise="Testing word count", target_chapters=2
    )
    ch1 = project.chapters.get(chapter_number=1)
    config = ProviderConfig.objects.create(
        user=owner,
        name="Pipeline Config",
        provider_type=ProviderType.FAKE,
    )

    job = GenerationJob.objects.create(
        user=owner,
        project=project,
        target_chapter_id=ch1.id,
        status=JobStatus.QUEUED,
        stage="Queued",
    )

    draft = GenerationPipeline.execute_chapter_generation(
        job=job,
        worker_id="worker-wc-test",
        custom_adapter=FakeProviderAdapter(config),
    )

    ch1.refresh_from_db()
    job.refresh_from_db()

    assert draft is not None
    assert ch1.current_word_count == draft.word_count
    assert ch1.current_word_count > 0
    assert job.result_url.endswith("/")
    assert job.result_url == f"/projects/{project.id}/chapters/{ch1.id}/edit/"


@pytest.mark.django_db
def test_continuity_checker_character_first_name_and_alias_matching():
    """Bug 5 Regression:
    Character name matching must resolve first-tokens and aliases in both dead character
    and injury checks.
    """
    owner = User.objects.create_user(username="char_matcher")
    project = PlanningService.create_project_with_scaffold(
        owner=owner, title="Char Matcher", premise="Testing character matching", target_chapters=2
    )
    ch1 = project.chapters.get(chapter_number=1)

    # Wounded character with multi-part name and alias
    Character.objects.create(
        project=project,
        name="Alaric Vance",
        aliases=["The Hawk"],
        wounds_status="Left arm broken and bandaged",
    )

    # Deceased character with multi-part name and alias
    Character.objects.create(
        project=project,
        name="Eldrin Thorne",
        aliases=["The Shadow"],
        is_alive=False,
    )

    # 1. First-name reference to injured character
    prose_first_name_injury = "Alaric swung with his left arm at the training dummy."
    findings1 = ContinuityChecker.run_deterministic_checks(ch1, prose_first_name_injury)
    injury_findings1 = [f for f in findings1 if f.category == FindingCategory.INJURY]
    assert len(injury_findings1) >= 1
    assert injury_findings1[0].severity == FindingSeverity.BLOCKER
    assert "Alaric Vance" in injury_findings1[0].claim

    # 2. Alias reference to injured character
    prose_alias_injury = "The Hawk lifted the heavy boulder with his left arm."
    findings2 = ContinuityChecker.run_deterministic_checks(ch1, prose_alias_injury)
    injury_findings2 = [f for f in findings2 if f.category == FindingCategory.INJURY]
    assert len(injury_findings2) >= 1
    assert injury_findings2[0].severity == FindingSeverity.BLOCKER

    # 3. First-name reference to deceased character
    prose_first_name_dead = "Eldrin crossed the courtyard in broad daylight."
    findings3 = ContinuityChecker.run_deterministic_checks(ch1, prose_first_name_dead)
    dead_findings1 = [f for f in findings3 if f.category == FindingCategory.IDENTITY]
    assert len(dead_findings1) >= 1
    assert "Eldrin Thorne" in dead_findings1[0].claim

    # 4. Alias reference to deceased character
    prose_alias_dead = "The Shadow crossed the courtyard in broad daylight."
    findings4 = ContinuityChecker.run_deterministic_checks(ch1, prose_alias_dead)
    dead_findings2 = [f for f in findings4 if f.category == FindingCategory.IDENTITY]
    assert len(dead_findings2) >= 1
    assert "Eldrin Thorne" in dead_findings2[0].claim


@pytest.mark.django_db
def test_continuity_checker_deduplicates_in_single_run_and_persisted_batches():
    """Bug 4 Regression:
    _match_injury_actions and check_and_persist must deduplicate matches within a single batch
    and against existing open findings in the database.
    """
    owner = User.objects.create_user(username="dedup_user")
    project = PlanningService.create_project_with_scaffold(
        owner=owner, title="Dedup Test", premise="Testing deduplication", target_chapters=2
    )
    ch1 = project.chapters.get(chapter_number=1)

    Character.objects.create(
        project=project,
        name="Seraphina",
        wounds_status="Left hand shattered",
    )

    # Prose matching multiple action verbs for the same limb
    prose = "Seraphina grasped with both hands, pulled with both hands, and lifted with both hands."

    # 1. First run: check_and_persist creates unique findings without internal duplicates
    findings = ContinuityChecker.check_and_persist(ch1, prose)
    db_findings = ContinuityFinding.objects.filter(chapter=ch1, status=FindingStatus.OPEN)
    assert db_findings.count() == len(findings)
    # Claims in db_findings must all be unique
    claims = list(db_findings.values_list("claim", flat=True))
    assert len(claims) == len(set(claims))

    # 2. Second run: re-saving must not pile up duplicate findings
    second_findings = ContinuityChecker.check_and_persist(ch1, prose)
    assert len(second_findings) == 0
    assert ContinuityFinding.objects.filter(chapter=ch1, status=FindingStatus.OPEN).count() == len(claims)


@pytest.mark.django_db
def test_canon_extraction_cleans_up_stale_proposals_from_earlier_drafts():
    """Bug 6 Regression:
    Re-extraction replaces unreviewed pending proposals across all prior drafts on the chapter,
    while preserving author-reviewed proposals.
    """
    owner = User.objects.create_user(username="extraction_cleaner")
    project = PlanningService.create_project_with_scaffold(
        owner=owner, title="Extraction Clean", premise="Testing clean extraction", target_chapters=2
    )
    ch1 = project.chapters.get(chapter_number=1)
    config = ProviderConfig.objects.create(
        user=owner,
        name="Extraction Config",
        provider_type=ProviderType.FAKE,
    )
    adapter = FakeProviderAdapter(config)

    # Draft 1
    d1 = DraftArtifact.objects.create(
        chapter=ch1, version_number=1, prose_content="Draft 1 content", word_count=3
    )
    CanonExtractionService.extract_from_draft(ch1, d1, adapter)
    assert ch1.proposed_canon_items.count() == 4

    # Author approves 1 proposal, leaves 3 pending
    keeper = ch1.proposed_canon_items.first()
    keeper.status = ProposedCanonItem.Status.APPROVED
    keeper.save()

    # Draft 2 generated / extracted
    d2 = DraftArtifact.objects.create(
        chapter=ch1, version_number=2, prose_content="Draft 2 content", word_count=3
    )
    CanonExtractionService.extract_from_draft(ch1, d2, adapter)

    # Reviewed proposal from Draft 1 is preserved
    keeper.refresh_from_db()
    assert keeper.status == ProposedCanonItem.Status.APPROVED
    assert keeper.draft_id == d1.id

    # All pending proposals are now anchored to Draft 2, none leaked from Draft 1
    pending_items = ch1.proposed_canon_items.filter(status=ProposedCanonItem.Status.PROPOSED)
    assert pending_items.count() == 4
    assert all(item.draft_id == d2.id for item in pending_items)


@pytest.mark.django_db
def test_chapter_canon_review_renders_event_proposal_details(client):
    """Bug 7 Regression:
    chapter_canon_review template must display event proposal metadata (event_type and summary).
    """
    owner = User.objects.create_user(username="event_viewer")
    client.force_login(owner)
    project = PlanningService.create_project_with_scaffold(
        owner=owner, title="Event View", premise="Testing event rendering", target_chapters=2
    )
    ch1 = project.chapters.get(chapter_number=1)
    d1 = DraftArtifact.objects.create(
        chapter=ch1, version_number=1, prose_content="Draft content", word_count=2
    )

    ProposedCanonItem.objects.create(
        project=project,
        chapter=ch1,
        draft=d1,
        kind=ProposedCanonItem.Kind.EVENT,
        payload={"event_type": "plot_twist", "summary": "The citadel gates collapsed."},
        summary="The citadel gates collapsed.",
        status=ProposedCanonItem.Status.PROPOSED,
    )

    url = reverse("taletomo:chapter_canon_review", args=[project.id, ch1.id])
    res = client.get(url)
    assert res.status_code == 200
    content = res.content.decode("utf-8")
    assert "Event type:" in content
    assert "plot_twist" in content
    assert "The citadel gates collapsed." in content
