from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from bookings.models import SessionRequest


class Command(BaseCommand):
    help = (
        "Permanently remove soft-deleted session requests older than a threshold. "
        "This cascades their status history and cannot be undone."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--older-than",
            type=int,
            required=True,
            help="Minimum age in days of the deleted_at timestamp.",
        )
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Actually delete rows. Without this flag the command only reports.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be deleted even when --commit is present.",
        )

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=options["older_than"])
        queryset = SessionRequest.all_objects.filter(
            deleted_at__isnull=False,
            deleted_at__lt=cutoff,
        )
        count = queryset.count()
        age = options["older_than"]

        if not options["commit"] or options["dry_run"]:
            self.stdout.write(
                f"{count} deleted request(s) older than {age} day(s) would be purged. "
                "Re-run with --commit to delete them permanently."
            )
            return

        queryset.delete()
        self.stdout.write(
            self.style.SUCCESS(
                f"Permanently purged {count} deleted request(s) older than {age} day(s)."
            )
        )
