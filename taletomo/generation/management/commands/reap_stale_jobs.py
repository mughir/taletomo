from django.core.management.base import BaseCommand
from taletomo.generation.reaper import reap_stale_jobs


class Command(BaseCommand):
    help = (
        "Recover generation jobs abandoned by dead workers: requeue jobs that never "
        "reached the provider, fail unknown-outcome jobs with reservations held, and "
        "re-dispatch queued jobs whose broker dispatch was likely lost."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--queued-grace-seconds",
            type=int,
            default=None,
            help="How long a QUEUED job must sit untouched before its dispatch is considered lost.",
        )

    def handle(self, *args, **options):
        result = reap_stale_jobs(queued_grace_seconds=options["queued_grace_seconds"])
        self.stdout.write(
            self.style.SUCCESS(
                f"Stale-job reaper finished: requeued={result.requeued}, "
                f"failed_unknown_outcome={result.failed_unknown_outcome}, "
                f"failed_post_provider={result.failed_post_provider}, "
                f"skipped={result.skipped}"
            )
        )
