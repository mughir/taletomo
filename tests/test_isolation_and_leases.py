import datetime
import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone
from taletomo.generation.models import GenerationJob, JobStatus
from taletomo.planning.models import Project
from taletomo.planning.services import PlanningService
from taletomo.providers.models import BudgetReservation, ProviderConfig, ProviderType

User = get_user_model()


@pytest.mark.django_db
def test_tenant_isolation_project_ownership():
    user_a = User.objects.create(username="user_alice")
    user_b = User.objects.create(username="user_bob")

    project_a = PlanningService.create_project_with_scaffold(
        owner=user_a, title="Alice's Secret Tome", premise="Private novel"
    )

    # User B projects list should NOT include User A's project
    b_projects = Project.objects.filter(owner=user_b)
    assert not b_projects.filter(id=project_a.id).exists()


@pytest.mark.django_db
def test_job_idempotency_unique_constraint():
    user = User.objects.create(username="job_author_idemp")
    project = PlanningService.create_project_with_scaffold(
        owner=user, title="Job Novel Idemp", premise="Job test"
    )

    idemp_key = "unique-key-xyz-123"
    GenerationJob.objects.create(
        project=project,
        user=user,
        job_type="chapter_draft",
        idempotency_key=idemp_key,
    )

    # Attempting to create duplicate job with same idempotency key fails unique constraint
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            GenerationJob.objects.create(
                project=project,
                user=user,
                job_type="chapter_draft",
                idempotency_key=idemp_key,
            )


@pytest.mark.django_db
def test_worker_lease_lifecycle():
    user = User.objects.create(username="lease_author")
    project = PlanningService.create_project_with_scaffold(
        owner=user, title="Lease Novel", premise="Lease test"
    )

    job = GenerationJob.objects.create(
        project=project,
        user=user,
        job_type="chapter_draft",
        idempotency_key="lease-job-key-456",
    )

    # Worker lease check
    now = timezone.now()
    job.lease_worker_id = "worker-primary"
    job.lease_expires_at = now + datetime.timedelta(seconds=60)
    job.save()

    job.refresh_from_db()
    assert job.lease_worker_id == "worker-primary"
    assert job.lease_expires_at > now


@pytest.mark.django_db
def test_provider_budget_reservation_reconcile():
    user = User.objects.create(username="budget_author")
    project = PlanningService.create_project_with_scaffold(
        owner=user, title="Budget Novel", premise="Budget test"
    )

    reservation = BudgetReservation.objects.create(
        user=user,
        project_id=project.id,
        job_id=project.id,
        reserved_tokens=5000,
        reserved_cost_usd=0.01,
    )
    assert reservation.status == BudgetReservation.Status.RESERVED

    # Reconcile after actual completion
    from decimal import Decimal
    reservation.reconcile(tokens_used=4200, cost_usd=Decimal("0.0084"))

    reservation.refresh_from_db()
    assert reservation.status == BudgetReservation.Status.RECONCILED
    assert reservation.confirmed_tokens == 4200
    assert reservation.confirmed_cost_usd == Decimal("0.0084")
