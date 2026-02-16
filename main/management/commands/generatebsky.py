import json

from authlib.jose import JsonWebKey
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Generate an ES256 keypair for Bluesky AT Protocol OAuth client assertion."

    def handle(self, *args, **options):
        key = JsonWebKey.generate_key("EC", "P-256", is_private=True)
        key_dict = key.as_dict(is_private=True)
        key_dict["kid"] = "mataroa-client-key"
        key_dict["use"] = "sig"
        key_dict["alg"] = "ES256"

        key_json = json.dumps(key_dict)

        self.stdout.write(
            self.style.SUCCESS("Generated ES256 keypair for Bluesky OAuth.")
        )
        self.stdout.write("")
        self.stdout.write(
            "Set this as your BLUESKY_CLIENT_SECRET_JWK environment variable:"
        )
        self.stdout.write("")
        self.stdout.write(key_json)
        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING(
                "Keep this secret! It is your server's private key for AT Protocol OAuth."
            )
        )
