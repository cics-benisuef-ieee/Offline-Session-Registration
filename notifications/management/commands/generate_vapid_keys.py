import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.core.management.base import BaseCommand


def _base64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class Command(BaseCommand):
    help = "Generate a VAPID public/private key pair for browser Web Push."

    def handle(self, *args, **options):
        private_key = ec.generate_private_key(ec.SECP256R1())
        private_der = private_key.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        public_point = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint,
        )

        self.stdout.write("Add these values to your environment:")
        self.stdout.write("")
        self.stdout.write(f"VAPID_PRIVATE_KEY={_base64url(private_der)}")
        self.stdout.write(f"VAPID_PUBLIC_KEY={_base64url(public_point)}")
        self.stdout.write("VAPID_SUBJECT=mailto:you@example.com")
