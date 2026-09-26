import os
from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("taletomo")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# Periodic recovery of generation jobs abandoned by dead workers. The lease
# makes duplicate dispatch safe; the reaper's billing policy decides between
# requeue and fail-with-held-reservation per job.
app.conf.beat_schedule = {
    "reap-stale-generation-jobs": {
        "task": "taletomo.generation.tasks.reap_stale_jobs_task",
        "schedule": 60.0,
    },
}


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f"Request: {self.request!r}")
