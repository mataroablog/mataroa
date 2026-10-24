import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.models import Max
from django.utils import timezone

from main import models, promotional_spam

LOCK_ID = 684291037


class Command(BaseCommand):
    help = "Reclassify every blog in resumable batches; never delete or contact blogs."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=100)
        parser.add_argument(
            "--max-blogs",
            type=int,
            default=0,
            help="Stop after this many checked blogs; 0 means finish the pass.",
        )
        parser.add_argument(
            "--full",
            action="store_true",
            help="Compatibility option: every pass now reanalyzes all blogs.",
        )
        parser.add_argument(
            "--pause", type=float, default=0.1, help="Seconds to pause between batches."
        )

    def handle(self, *args, **options):
        if (
            not 1 <= options["batch_size"] <= 200
            or options["max_blogs"] < 0
            or options["pause"] < 0
        ):
            raise CommandError(
                "Use batch-size 1–200 and nonnegative max-blogs and pause."
            )
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [LOCK_ID])
            if not cursor.fetchone()[0]:
                self.stdout.write("Another promotional spam scan is already running.")
                return
            cursor.execute("SHOW statement_timeout")
            old_timeout = cursor.fetchone()[0]
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('statement_timeout', '30s', false)")
            self.scan(options)
        finally:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config('statement_timeout', %s, false)", [old_timeout]
                )
                cursor.execute("SELECT pg_advisory_unlock(%s)", [LOCK_ID])

    def scan(self, options):
        state, _ = models.PromotionalSpamScan.objects.get_or_create(pk=1)
        if (
            state.started_at
            and not state.completed_at
            and state.rule_version == promotional_spam.VERSION
        ):
            self.stdout.write(f"Resuming after blog ID {state.cursor}.")
        else:
            state.started_at = timezone.now()
            state.completed_at = None
            state.cursor = state.checked = state.analyzed = 0
            state.upper_bound = (
                models.User.objects.aggregate(last=Max("pk"))["last"] or 0
            )
            state.rule_version = promotional_spam.VERSION
            state.heartbeat_at = timezone.now()
            state.save()
        processed = 0
        while True:
            size = options["batch_size"]
            if options["max_blogs"]:
                size = min(size, options["max_blogs"] - processed)
                if size == 0:
                    self.stdout.write(
                        "Checkpoint saved; run the command again to resume."
                    )
                    return
            owners = promotional_spam.owner_batch(state.cursor, state.upper_bound, size)
            if not owners:
                state.completed_at = state.last_completed_at = timezone.now()
                state.heartbeat_at = timezone.now()
                state.save()
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Complete: {state.checked} blogs checked; {state.analyzed} analyzed."
                    )
                )
                return
            analyzed = promotional_spam.scan_batch(owners)
            state.cursor = owners[-1].pk
            state.checked += len(owners)
            state.analyzed += analyzed
            state.heartbeat_at = timezone.now()
            state.save()
            processed += len(owners)
            self.stdout.write(
                f"Checked {state.checked}; analyzed {state.analyzed}; checkpoint {state.cursor}/{state.upper_bound}."
            )
            self.stdout.flush()
            time.sleep(options["pause"])
