from django.core.management.base import BaseCommand

from notifications.reminders import send_due_reminders


class Command(BaseCommand):
    help = "Send 24-hour and 2-hour reminders for approved sessions."

    def handle(self, *args, **options):
        result = send_due_reminders()
        self.stdout.write(
            self.style.SUCCESS(
                f"Reminders sent: {result['sent']}; skipped: {result['skipped']}."
            )
        )
