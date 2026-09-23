from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from taletomo.generation.models import GenerationJob
from taletomo.planning.services import PlanningService

User = get_user_model()


@pytest.mark.django_db(transaction=True)
def test_postgres_serializes_simultaneous_lease_claims():
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL row-lock behavior requires PostgreSQL")

    user = User.objects.create_user(username="parallel_lease_author")
    project = PlanningService.create_project_with_scaffold(
        owner=user, title="Concurrent lease test", premise="Only one worker may own the job"
    )
    job = GenerationJob.objects.create(
        project=project,
        user=user,
        job_type="chapter_draft",
        idempotency_key="simultaneous-postgres-lease",
    )
    start_together = Barrier(2)

    def claim(worker_id):
        close_old_connections()
        try:
            local_job = GenerationJob.objects.get(pk=job.pk)
            start_together.wait(timeout=10)
            return local_job.acquire_lease(worker_id=worker_id, duration_seconds=60)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, ["worker-a", "worker-b"]))

    assert sorted(claims) == [False, True]
