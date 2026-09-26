import datetime
import time

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone
from taletomo.generation.heartbeat import LeaseHeartbeat
from taletomo.generation.models import DraftArtifact, GenerationJob, JobAttempt, JobStatus
from taletomo.generation.pipeline import GenerationPipeline
from taletomo.generation.tasks import generate_chapter_task
from taletomo.planning.services import PlanningService
from taletomo.providers.adapters import FakeProviderAdapter
from taletomo.providers.models import BudgetReservation, ProviderConfig, ProviderType

User = get_user_model()


def _make_job(user, key, status=JobStatus.QUEUED, **kwargs):
    project = kwargs.pop("project", None) or PlanningService.create_project_with_scaffold(
        owner=user, title=f"Reliability fixture {key}", premise="Job reliability test"
    )
    kwargs.setdefault("target_chapter_id", project.chapters.order_by("chapter_number").first().id)
    return project, GenerationJob.objects.create(
        project=project,
        user=user,
        job_type="chapter_draft",
        idempotency_key=key,
        status=status,
        **kwargs,
    )


def _expire_lease(job, worker="dead-worker"):
    job.lease_worker_id = worker
    job.lease_expires_at = timezone.now() - datetime.timedelta(seconds=30)
    job.status = JobStatus.GENERATING
    job.save()


@pytest.mark.django_db(transaction=True)
def test_lease_heartbeat_advances_expiry_and_stops_cleanly():
    """The heartbeat renews from its own DB connection, so this test commits for real."""
    user = User.objects.create(username="heartbeat_author")
    _project, job = _make_job(user, "heartbeat-unit")
    assert job.acquire_lease(worker_id="hb-worker", duration_seconds=2)
    initial_expiry = job.lease_expires_at

    heartbeat = LeaseHeartbeat(job, worker_id="hb-worker", interval_seconds=0.5)
    heartbeat.start()
    time.sleep(1.6)
    heartbeat.stop()

    job.refresh_from_db()
    assert heartbeat.renewals >= 2
    assert job.lease_expires_at > initial_expiry

    frozen_expiry = job.lease_expires_at
    time.sleep(0.8)
    job.refresh_from_db()
    assert job.lease_expires_at == frozen_expiry


@pytest.mark.django_db(transaction=True)
def test_lease_heartbeat_records_lost_lease_without_raising():
    user = User.objects.create(username="heartbeat_loser")
    _project, job = _make_job(user, "heartbeat-lost")
    assert job.acquire_lease(worker_id="owner-worker", duration_seconds=90)

    heartbeat = LeaseHeartbeat(job, worker_id="other-worker", interval_seconds=0.5)
    heartbeat.start()
    time.sleep(0.8)
    heartbeat.stop()

    assert heartbeat.renewal_failures >= 1
    assert heartbeat.renewals == 0


@pytest.mark.django_db(transaction=True)
def test_heartbeat_lets_slow_generation_outlive_lease_without_lease_theft():
    """A provider call longer than the lease window must not let a second worker steal the job."""
    user = User.objects.create(username="slow_gen_author")
    project, job = _make_job(user, "slow-generation-heartbeat")
    config = ProviderConfig.objects.create(
        user=user, name="Slow fake", provider_type=ProviderType.FAKE
    )

    class SlowStealingAdapter(FakeProviderAdapter):
        """Simulates a provider call that outlives the initial lease window."""

        def __init__(self, cfg, target_job):
            super().__init__(cfg)
            self.target_job = target_job
            self.steal_result = None

        def generate_text(self, **kwargs):
            time.sleep(2.3)
            # Lease (2s) has lapsed by now; only the heartbeat keeps it alive.
            self.steal_result = self.target_job.acquire_lease(
                worker_id="thief-worker", duration_seconds=90
            )
            return super().generate_text(**kwargs)

    adapter = SlowStealingAdapter(config, job)
    draft = GenerationPipeline.execute_chapter_generation(
        job=job,
        worker_id="patient-worker",
        custom_adapter=adapter,
        lease_seconds=2,
        heartbeat_interval=0.5,
    )

    assert adapter.steal_result is False, "Expired lease must not be acquirable while the heartbeat renews it"
    job.refresh_from_db()
    assert job.status == JobStatus.READY
    assert isinstance(draft, DraftArtifact)
    assert draft.word_count > 2  # Real prose, not a scripted critique stub


@pytest.mark.django_db
def test_reaper_requeues_job_that_never_reached_provider(
    monkeypatch, django_capture_on_commit_callbacks
):
    user = User.objects.create(username="reaper_requeue_author")
    _project, job = _make_job(user, "reaper-requeue")
    _expire_lease(job)

    dispatched = []
    monkeypatch.setattr(
        "taletomo.generation.reaper.generate_chapter_task.delay",
        lambda job_id: dispatched.append(job_id),
    )

    from taletomo.generation.reaper import reap_stale_jobs

    with django_capture_on_commit_callbacks(execute=True):
        result = reap_stale_jobs()

    job.refresh_from_db()
    assert result.requeued == 1
    assert job.status == JobStatus.QUEUED
    assert "Requeued" in job.stage
    assert job.lease_worker_id is None
    assert dispatched == [str(job.id)]


@pytest.mark.django_db
def test_reaper_fails_unknown_outcome_and_holds_reservation(monkeypatch):
    user = User.objects.create(username="reaper_unknown_author")
    _project, job = _make_job(user, "reaper-unknown")
    _expire_lease(job)
    JobAttempt.objects.create(job=job, attempt_number=1, outcome="started")
    reservation = BudgetReservation.objects.create(
        user=user,
        project_id=job.project_id,
        job_id=job.id,
        reserved_tokens=8000,
        reserved_cost_usd="0.0500",
    )

    dispatched = []
    monkeypatch.setattr(
        "taletomo.generation.reaper.generate_chapter_task.delay",
        lambda job_id: dispatched.append(job_id),
    )

    from taletomo.generation.reaper import reap_stale_jobs

    result = reap_stale_jobs()

    job.refresh_from_db()
    reservation.refresh_from_db()
    assert result.failed_unknown_outcome == 1
    assert result.requeued == 0
    assert dispatched == []
    assert job.status == JobStatus.FAILED
    assert job.error_details["unknown_outcome"] is True
    assert job.error_details["reason"] == "worker_lease_expired"
    # Reservation stays held: the provider may have billed before the worker died.
    assert reservation.status == BudgetReservation.Status.RESERVED


@pytest.mark.django_db
def test_reaper_fails_post_provider_death_without_unknown_flag(monkeypatch):
    user = User.objects.create(username="reaper_postprov_author")
    _project, job = _make_job(user, "reaper-postprov")
    _expire_lease(job)
    JobAttempt.objects.create(job=job, attempt_number=1, outcome="completed")

    dispatched = []
    monkeypatch.setattr(
        "taletomo.generation.reaper.generate_chapter_task.delay",
        lambda job_id: dispatched.append(job_id),
    )

    from taletomo.generation.reaper import reap_stale_jobs

    result = reap_stale_jobs()

    job.refresh_from_db()
    assert result.failed_post_provider == 1
    assert dispatched == []
    assert job.status == JobStatus.FAILED
    assert job.error_details["unknown_outcome"] is False
    assert job.error_details["reason"] == "worker_lease_expired_post_provider"
    # A manual retry is permitted: billing was confirmed on the completed attempt.
    assert "Retry manually" in job.error_message


@pytest.mark.django_db
def test_reaper_recovers_lost_queued_dispatch_after_grace(
    monkeypatch, django_capture_on_commit_callbacks
):
    user = User.objects.create(username="reaper_queued_author")
    project, fresh_job = _make_job(user, "reaper-queued-fresh")
    _project2, stale_job = _make_job(user, "reaper-queued-stale", project=project)

    GenerationJob.objects.filter(id=stale_job.id).update(
        updated_at=timezone.now() - datetime.timedelta(seconds=1800)
    )

    dispatched = []
    monkeypatch.setattr(
        "taletomo.generation.reaper.generate_chapter_task.delay",
        lambda job_id: dispatched.append(job_id),
    )

    from taletomo.generation.reaper import reap_stale_jobs

    with django_capture_on_commit_callbacks(execute=True):
        result = reap_stale_jobs(queued_grace_seconds=900)

    fresh_job.refresh_from_db()
    stale_job.refresh_from_db()
    assert result.requeued == 1
    assert fresh_job.status == JobStatus.QUEUED  # Inside grace window: untouched
    assert stale_job.status == JobStatus.QUEUED
    assert "Requeued" in stale_job.stage
    assert sorted(dispatched) == sorted([str(stale_job.id)])


@pytest.mark.django_db
def test_reaper_ignores_jobs_with_live_leases():
    user = User.objects.create(username="reaper_live_author")
    _project, job = _make_job(user, "reaper-live")
    job.lease_worker_id = "live-worker"
    job.lease_expires_at = timezone.now() + datetime.timedelta(seconds=60)
    job.status = JobStatus.GENERATING
    job.save()

    from taletomo.generation.reaper import reap_stale_jobs

    result = reap_stale_jobs()

    job.refresh_from_db()
    assert result.acted == 0
    assert job.status == JobStatus.GENERATING


@pytest.mark.django_db
def test_worker_task_does_not_clobber_running_job_on_lease_conflict():
    user = User.objects.create(username="clobber_author")
    _project, job = _make_job(user, "clobber-guard")
    job.lease_worker_id = "worker-a"
    job.lease_expires_at = timezone.now() + datetime.timedelta(seconds=60)
    job.status = JobStatus.GENERATING
    job.save()

    generate_chapter_task.run(str(job.id))

    job.refresh_from_db()
    assert job.status == JobStatus.GENERATING
    assert job.lease_worker_id == "worker-a"
    assert job.error_message == ""


@pytest.mark.django_db
def test_reap_stale_jobs_management_command_runs(capsys):
    user = User.objects.create(username="reaper_cmd_author")
    _project, job = _make_job(user, "reaper-cmd")
    _expire_lease(job)

    call_command("reap_stale_jobs")

    job.refresh_from_db()
    out = capsys.readouterr().out
    assert "requeued=1" in out
    assert job.status == JobStatus.QUEUED
