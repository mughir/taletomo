import logging
from celery import shared_task
from taletomo.generation.models import GenerationJob, JobStatus
from taletomo.generation.pipeline import GenerationPipeline, LeaseAcquisitionError

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = (
    JobStatus.READY,
    JobStatus.FAILED,
    JobStatus.CANCELLED,
)


@shared_task(bind=True, max_retries=1)
def generate_chapter_task(self, job_id: str):
    """Asynchronous background Celery task for drafting a chapter."""
    try:
        job = GenerationJob.objects.get(id=job_id)
    except GenerationJob.DoesNotExist:
        logger.error(f"GenerationJob {job_id} not found.")
        return

    if job.status in (JobStatus.CANCELLED, JobStatus.READY):
        logger.info(f"Job {job_id} is {job.status}; nothing to execute.")
        return

    worker_id = f"celery-{self.request.id or 'worker'}"
    try:
        GenerationPipeline.execute_chapter_generation(job, worker_id=worker_id)
    except LeaseAcquisitionError:
        # Another live worker owns this job. Overwriting its status here would
        # clobber a healthy in-flight run (and could double-dispatch work).
        logger.info(f"Job {job_id} lease is held by another worker; skipping duplicate execution.")
    except Exception as e:
        logger.error(f"Job {job_id} execution failed: {e}")
        # Mark as failed in DB unless the pipeline already reached a terminal state
        job.refresh_from_db()
        if job.status not in TERMINAL_STATUSES:
            job.status = JobStatus.FAILED
            job.error_message = str(e)
            job.save(update_fields=["status", "error_message", "updated_at"])


@shared_task(ignore_result=True)
def reap_stale_jobs_task():
    """Periodic Celery beat task recovering jobs abandoned by dead workers."""
    from taletomo.generation.reaper import reap_stale_jobs

    result = reap_stale_jobs()
    acted = result.requeued + result.failed_unknown_outcome + result.failed_post_provider
    if acted:
        logger.info(
            "Stale-job reaper: requeued=%d failed_unknown_outcome=%d failed_post_provider=%d",
            result.requeued,
            result.failed_unknown_outcome,
            result.failed_post_provider,
        )
