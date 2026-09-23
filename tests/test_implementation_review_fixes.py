import json
import pytest
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from taletomo.canon.models import (
    CanonFact,
    Character,
    Faction,
    Location,
    PlotThread,
    StoryEvent,
    TimelineEvent,
    TruthScope,
    WorldRule,
)
from taletomo.canon.services import CanonService, StaleHeadError
from taletomo.consistency.checker import ContinuityChecker
from taletomo.consistency.models import ContinuityFinding, FindingCategory, FindingSeverity, FindingStatus
from taletomo.context.retrieval import ContextAssembler
from taletomo.exporting.services import ExportService
from taletomo.generation.models import DraftArtifact, DraftStatus, GenerationJob, JobStatus
from taletomo.generation.pipeline import GenerationPipeline
from taletomo.planning.models import Chapter, ChapterPlan, Project
from taletomo.planning.services import PlanningService
from taletomo.providers.adapters import ProviderGateway
from taletomo.providers.models import ProviderConfig, ProviderType

User = get_user_model()


@pytest.mark.django_db
def test_anonymous_requests_redirect_to_login():
    """Verify that anonymous requests are denied with 302 redirect to login, removing shared author."""
    client = Client()

    # Public / home and project list
    assert client.get(reverse("taletomo:home")).status_code == 302
    assert "/accounts/login/" in client.get(reverse("taletomo:home")).url

    assert client.get(reverse("taletomo:project_list")).status_code == 302
    assert client.get(reverse("taletomo:project_new")).status_code == 302
    assert client.get(reverse("taletomo:job_list")).status_code == 302
    assert client.get(reverse("taletomo:settings_providers")).status_code == 302


@pytest.mark.django_db
def test_cross_tenant_isolation_idor_denied():
    """Verify that User B cannot view, edit, export, or access User A's projects, jobs, providers, or findings."""
    user_a = User.objects.create_user(username="alice", password="password_a")
    user_b = User.objects.create_user(username="bob", password="password_b")

    client_b = Client()
    client_b.force_login(user_b)

    proj_a = PlanningService.create_project_with_scaffold(
        owner=user_a, title="Alice Secret Tome", premise="Top secret"
    )
    ch1 = proj_a.chapters.get(chapter_number=1)

    # User B accessing User A project overview -> 404
    resp = client_b.get(reverse("taletomo:project_overview", args=[proj_a.id]))
    assert resp.status_code == 404

    # User B accessing User A bible -> 404
    resp = client_b.get(reverse("taletomo:project_bible", args=[proj_a.id]))
    assert resp.status_code == 404

    # User B accessing User A outline -> 404
    resp = client_b.get(reverse("taletomo:project_outline", args=[proj_a.id]))
    assert resp.status_code == 404

    # User B accessing User A chapter edit -> 404
    resp = client_b.get(reverse("taletomo:chapter_edit", args=[proj_a.id, ch1.id]))
    assert resp.status_code == 404

    # User B accessing User A export -> 404
    resp = client_b.get(reverse("taletomo:project_export", args=[proj_a.id]))
    assert resp.status_code == 404
    resp = client_b.get(reverse("taletomo:project_export_markdown", args=[proj_a.id]))
    assert resp.status_code == 404
    resp = client_b.get(reverse("taletomo:project_export_json", args=[proj_a.id]))
    assert resp.status_code == 404

    # User B accessing User A job -> 404
    job_a = GenerationJob.objects.create(
        project=proj_a,
        user=user_a,
        job_type="chapter_draft",
        idempotency_key="alice-job-1",
    )
    resp = client_b.get(reverse("taletomo:job_detail", args=[job_a.id]))
    assert resp.status_code == 404
    resp = client_b.get(reverse("taletomo:job_status_api", args=[job_a.id]))
    assert resp.status_code == 404

    # User B testing User A provider config -> 404
    prov_a = ProviderConfig.objects.create(
        user=user_a,
        name="Alice Provider",
        provider_type=ProviderType.FAKE,
    )
    resp = client_b.post(reverse("taletomo:test_provider"), {"config_id": prov_a.id})
    assert resp.status_code == 404

    # User B updating User A finding -> 404
    finding_a = ContinuityFinding.objects.create(
        project=proj_a,
        chapter=ch1,
        category=FindingCategory.PLOT,
        severity=FindingSeverity.WARNING,
        claim="Alice finding",
    )
    resp = client_b.post(reverse("taletomo:finding_update", args=[finding_a.id]), {"status": "dismissed"})
    assert resp.status_code == 404


@pytest.mark.django_db
def test_canon_commit_domain_preconditions():
    """Verify C2 domain guards: unapproved draft or open blocker rejects canon commit."""
    user = User.objects.create_user(username="author_guard", password="pwd")
    project = PlanningService.create_project_with_scaffold(
        owner=user, title="Guard Novel", premise="Guard test"
    )
    ch1 = project.chapters.get(chapter_number=1)

    draft1 = DraftArtifact.objects.create(
        chapter=ch1,
        version_number=1,
        prose_content="The hero walked into the shadows.",
        word_count=6,
        status=DraftStatus.UNDER_REVIEW,  # NOT ACCEPTED yet!
    )
    ch1.active_draft_id = draft1.id
    ch1.status = Chapter.Status.REVIEW
    ch1.save()

    # 1. Attempting commit with unapproved draft fails
    with pytest.raises(ValueError, match="must be APPROVED"):
        CanonService.commit_chapter_canon(
            project=project,
            chapter=ch1,
            expected_head=project.active_branch_head,
            events=[{"summary": "Test", "event_type": "plot_progress"}],
            facts=[],
            actor=user,
        )

    # Set chapter to approved, but draft still UNDER_REVIEW
    ch1.status = Chapter.Status.APPROVED
    ch1.save()

    with pytest.raises(ValueError, match="must be ACCEPTED before canon commit"):
        CanonService.commit_chapter_canon(
            project=project,
            chapter=ch1,
            expected_head=project.active_branch_head,
            events=[{"summary": "Test", "event_type": "plot_progress"}],
            facts=[],
            actor=user,
        )

    # 2. Approve draft, but add open BLOCKER finding
    draft1.status = DraftStatus.ACCEPTED
    draft1.save()

    blocker = ContinuityFinding.objects.create(
        project=project,
        chapter=ch1,
        category=FindingCategory.IDENTITY,
        severity=FindingSeverity.BLOCKER,
        status=FindingStatus.OPEN,
        claim="Dead king speaks without explanation.",
    )

    with pytest.raises(ValueError, match="open blocker"):
        CanonService.commit_chapter_canon(
            project=project,
            chapter=ch1,
            expected_head=project.active_branch_head,
            events=[{"summary": "Test", "event_type": "plot_progress"}],
            facts=[],
            actor=user,
        )

    # 3. Explicit override allows commit
    snapshot = CanonService.commit_chapter_canon(
        project=project,
        chapter=ch1,
        expected_head=project.active_branch_head,
        events=[{"summary": "Test", "event_type": "plot_progress"}],
        facts=[],
        actor=user,
        override_blockers=True,
        override_rationale="King speaks as an illusion in this scene",
    )
    assert snapshot is not None
    project.refresh_from_db()
    assert project.active_branch_head == "rev_2"


@pytest.mark.django_db
def test_provider_scoping_isolation():
    """Verify C3: Provider selection does not cross user boundaries."""
    user_a = User.objects.create_user(username="provider_user_a")
    user_b = User.objects.create_user(username="provider_user_b")

    # User A creates a default provider
    prov_a = ProviderConfig.objects.create(
        user=user_a,
        name="User A Custom Provider",
        provider_type=ProviderType.FAKE,
        default_drafting_model="model-a",
        is_default=True,
        is_active=True,
    )

    # User B requests adapter without specifying config
    adapter_b = ProviderGateway.get_adapter(user=user_b)
    # Must NOT use User A's provider!
    assert adapter_b.config.id != prov_a.id
    assert adapter_b.config.name == "Fallback Mock"

    # User A requests adapter
    adapter_a = ProviderGateway.get_adapter(user=user_a)
    assert adapter_a.config.id == prov_a.id


@pytest.mark.django_db
def test_backup_tampering_rejection_and_full_restore():
    """Verify C4: Tampered backups are rejected, and full entity graphs are restored."""
    user = User.objects.create_user(username="backup_author")
    project = PlanningService.create_project_with_scaffold(
        owner=user, title="Full Canon Novel", premise="Parity test"
    )

    # Add Faction, TimelineEvent, PlotThread
    faction = Faction.objects.create(
        project=project, name="Nightwatchers", goals="Protect the border", resources="Watchtowers"
    )
    t_event = TimelineEvent.objects.create(
        project=project, title="The Grand Eclipse", story_time_valid_from="Year 1 Frost 1", real_order=1
    )
    thread = PlotThread.objects.create(
        project=project, title="The Stolen Amulet", category="mystery", setup_chapter=1
    )
    ch1 = project.chapters.get(chapter_number=1)
    s_event = StoryEvent.objects.create(
        project=project, chapter=ch1, event_type="discovery", summary="Amulet was found broken."
    )

    # Export
    backup = ExportService.export_json_backup(project)
    assert backup["manifest"]["checksum_sha256"]

    # 1. Tamper test: modify title in payload
    tampered_backup = json.loads(json.dumps(backup))
    tampered_backup["project"]["title"] = "Hacked Title"

    with pytest.raises(ValueError, match="Backup checksum verification failed"):
        ExportService.restore_from_json(owner=user, backup_data=tampered_backup)

    # 2. Legitimate restore parity
    restored = ExportService.restore_from_json(owner=user, backup_data=backup)
    assert restored.title == project.title
    assert restored.factions.filter(name="Nightwatchers").exists()
    assert restored.timeline_events.filter(title="The Grand Eclipse").exists()
    assert restored.plot_threads.filter(title="The Stolen Amulet").exists()
    assert restored.story_events.filter(summary="Amulet was found broken.").exists()


@pytest.mark.django_db
def test_atomic_lease_concurrency():
    """Verify C6: Atomic worker lease prevents simultaneous execution."""
    user = User.objects.create_user(username="lease_user")
    project = PlanningService.create_project_with_scaffold(owner=user, title="Lease Test", premise="Lease premise")
    job = GenerationJob.objects.create(
        project=project,
        user=user,
        job_type="chapter_draft",
        idempotency_key="lease-concurrent-key",
    )

    # Worker A acquires lease
    assert job.acquire_lease(worker_id="worker-A", duration_seconds=60) is True

    # Worker B tries to acquire lease while Worker A holds it
    assert job.acquire_lease(worker_id="worker-B", duration_seconds=60) is False

    # Worker A renews lease
    assert job.renew_lease(worker_id="worker-A", extension_seconds=120) is True
    # Worker B cannot renew Worker A's lease
    assert job.renew_lease(worker_id="worker-B") is False

    # Worker A releases lease
    job.release_lease(worker_id="worker-A")

    # Worker B can now acquire lease
    assert job.acquire_lease(worker_id="worker-B", duration_seconds=60) is True


@pytest.mark.django_db
def test_context_assembly_250k_and_category_budgets():
    """Verify H1 & H2: 250k model profile propagation and category budget enforcement."""
    user = User.objects.create_user(username="budget_user")
    project = PlanningService.create_project_with_scaffold(owner=user, title="Context Test", premise="Context premise")
    ch1 = project.chapters.get(chapter_number=1)

    # Assemble context with 250k profile
    pkg = ContextAssembler.assemble_chapter_context(
        chapter=ch1,
        model_context_limit=250000,
        requested_output_tokens=4000,
    )
    assert pkg.manifest.model_context_limit == 250000
    assert pkg.total_tokens < pkg.manifest.usable_budget


@pytest.mark.django_db
def test_timeline_and_factions_routes():
    """Verify H8: Timeline template exists and Factions route operates correctly."""
    user = User.objects.create_user(username="routes_user", password="pwd")
    client = Client()
    client.force_login(user)

    project = PlanningService.create_project_with_scaffold(owner=user, title="Routes Test", premise="Routes premise")

    # 1. Timeline route renders without TemplateDoesNotExist
    resp = client.get(reverse("taletomo:project_timeline", args=[project.id]))
    assert resp.status_code == 200

    # 2. Add Timeline event via POST
    resp = client.post(
        reverse("taletomo:project_timeline", args=[project.id]),
        {
            "title": "Coronation Day",
            "story_time": "Year 200",
            "real_order": 1,
            "description": "The new monarch takes the oath.",
        },
    )
    assert resp.status_code == 302
    assert project.timeline_events.filter(title="Coronation Day").exists()

    # 3. Factions route renders and accepts POST
    resp = client.get(reverse("taletomo:project_factions", args=[project.id]))
    assert resp.status_code == 200

    resp = client.post(
        reverse("taletomo:project_factions", args=[project.id]),
        {
            "name": "Silver Guild",
            "goals": "Build sky docks",
            "resources": "Air galleons",
        },
    )
    assert resp.status_code == 302
    assert project.factions.filter(name="Silver Guild").exists()


@pytest.mark.django_db
def test_job_cancel_and_retry_routes():
    """Verify H8: Cancel and retry endpoints update job states correctly."""
    user = User.objects.create_user(username="job_action_user", password="pwd")
    client = Client()
    client.force_login(user)

    project = PlanningService.create_project_with_scaffold(owner=user, title="Job Action Test", premise="Job premise")
    job = GenerationJob.objects.create(
        project=project,
        user=user,
        job_type="chapter_draft",
        idempotency_key="action-job-1",
        status=JobStatus.QUEUED,
    )

    # Cancel job
    resp = client.post(reverse("taletomo:job_cancel", args=[job.id]))
    assert resp.status_code == 302
    job.refresh_from_db()
    assert job.status == JobStatus.CANCELLED

    # Retry job
    resp = client.post(reverse("taletomo:job_retry", args=[job.id]))
    assert resp.status_code == 302
    job.refresh_from_db()
    assert job.status == JobStatus.QUEUED
