import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from taletomo.generation.models import GenerationJob, JobStatus
from taletomo.generation.pipeline import GenerationPipeline
from taletomo.planning.services import PlanningService
from taletomo.providers.adapters import FakeProviderAdapter
from taletomo.providers.models import BudgetReservation, ProviderConfig, ProviderType

User = get_user_model()


@pytest.mark.django_db
def test_job_with_unknown_provider_outcome_cannot_be_blindly_retried(monkeypatch):
    user = User.objects.create_user(username="unknown_outcome_author")
    project = PlanningService.create_project_with_scaffold(
        owner=user, title="Unknown billing state", premise="A timeout may still be billed"
    )
    job = GenerationJob.objects.create(
        project=project,
        user=user,
        job_type="chapter_draft",
        idempotency_key="unknown-provider-outcome-retry",
        status=JobStatus.FAILED,
        stage="Unknown provider outcome after timeout",
        error_message="Billing status unknown",
        error_details={"unknown_outcome": True, "billing_status": "unverified"},
    )
    client = Client()
    client.force_login(user)

    def forbidden_dispatch(*args, **kwargs):
        raise AssertionError("Unknown-cost generation must not be automatically resubmitted")

    monkeypatch.setattr("taletomo.web.views.generate_chapter_task.delay", forbidden_dispatch)
    response = client.post(reverse("taletomo:job_retry", args=[job.id]), follow=True)

    job.refresh_from_db()
    assert job.status == JobStatus.FAILED
    assert job.error_details["unknown_outcome"] is True
    assert "billing status is unknown" in response.content.decode().lower()
    assert b"Retry Job" not in response.content


@pytest.mark.django_db
def test_unknown_provider_outcome_keeps_budget_reservation_held():
    user = User.objects.create_user(username="timeout_budget_author")
    project = PlanningService.create_project_with_scaffold(
        owner=user, title="Timeout accounting", premise="Keep cost allowance reserved"
    )
    chapter = project.chapters.get(chapter_number=1)
    job = GenerationJob.objects.create(
        project=project,
        user=user,
        job_type="chapter_draft",
        idempotency_key="timeout-reservation-hold",
        target_chapter_id=chapter.id,
    )
    config = ProviderConfig.objects.create(
        user=user,
        name="Timeout fixture",
        provider_type=ProviderType.FAKE,
    )
    adapter = FakeProviderAdapter(config)
    adapter.simulate_failure = "timeout"

    with pytest.raises(TimeoutError):
        GenerationPipeline.execute_chapter_generation(
            job=job,
            worker_id="timeout-test-worker",
            custom_adapter=adapter,
        )

    job.refresh_from_db()
    reservation = BudgetReservation.objects.get(job_id=job.id)
    assert job.error_details["unknown_outcome"] is True
    assert reservation.status == BudgetReservation.Status.RESERVED
