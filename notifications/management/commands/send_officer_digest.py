from django.core.management.base import BaseCommand

from notifications.digest import send_officer_digest


class Command(BaseCommand):
    help = "Send the pending/upcoming officer digest to subscribed officers."

    def handle(self, *args, **options):
        result = send_officer_digest()
        self.stdout.write(
            self.style.SUCCESS(
                f"Digests sent: {result['sent']}; skipped: {result['skipped']}."
            )
        )
