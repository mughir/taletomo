"""Worker lease heartbeat for long-running generation work.

A GenerationJob lease (default 90s) can legitimately expire while the worker
is still healthy: a single provider call may run for 120s or more. Without
renewal, a second worker can acquire the expired lease and generate the same
chapter again (duplicate drafts, duplicate spend). The heartbeat renews the
lease on a daemon thread for as long as the owning worker is alive.
"""

import logging
import threading

logger = logging.getLogger(__name__)


class LeaseHeartbeat:
    """Renews a job lease every `interval_seconds` until stopped.

    `renewals` counts successful renewals so callers and tests can verify the
    heartbeat actually ran. A failed renewal (lease stolen or worker died) is
    logged and counted, never raised: the provider call in the main thread
    cannot be safely aborted mid-flight either way.
    """

    def __init__(self, job, worker_id: str, interval_seconds: float = 15.0):
        self.job = job
        self.worker_id = worker_id
        self.interval_seconds = max(0.5, float(interval_seconds))
        self.renewals = 0
        self.renewal_failures = 0
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name=f"lease-heartbeat-{self.job.id}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=self.interval_seconds + 5.0)
        self._thread = None

    def _run(self) -> None:
        while not self._stop_event.wait(self.interval_seconds):
            try:
                if self.job.renew_lease(worker_id=self.worker_id):
                    self.renewals += 1
                else:
                    self.renewal_failures += 1
                    logger.error(
                        "Lease heartbeat lost job %s to another worker; the in-flight "
                        "provider call may duplicate work.",
                        self.job.id,
                    )
            except Exception:
                self.renewal_failures += 1
                logger.exception("Lease heartbeat renewal error for job %s", self.job.id)
