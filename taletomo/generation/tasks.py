import logging
from celery import shared_task
from taletomo.generation.models import GenerationJob, JobStatus
from taletomo.generation.pipeline import GenerationPipeline

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=1)
def generate_chapter_task(self, job_id: str):
    """Asynchronous background Celery task for drafting a chapter."""
    try:
        job = GenerationJob.objects.get(id=job_id)
    except GenerationJob.DoesNotExist:
        logger.error(f"GenerationJob {job_id} not found.")
        return

    if job.status == JobStatus.CANCELLED:
        logger.info(f"Job {job_id} was cancelled before execution.")
        return

    worker_id = f"celery-{self.request.id or 'worker'}"
    try:
        GenerationPipeline.execute_chapter_generation(job, worker_id=worker_id)
    except Exception as e:
        logger.error(f"Job {job_id} execution failed: {e}")
        # Mark as failed in DB
        job.refresh_from_db()
        job.status = JobStatus.FAILED
        job.error_message = str(e)
        job.save(update_fields=["status", "error_message", "updated_at"])
