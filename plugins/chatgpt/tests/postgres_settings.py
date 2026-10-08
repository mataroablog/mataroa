"""Opt-in PostgreSQL test settings for a dedicated disposable server."""

import os

from .settings import *  # noqa: F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["MATAROA_TEST_POSTGRES_DB"],
        "HOST": os.environ["MATAROA_TEST_POSTGRES_HOST"],
        "PORT": os.environ.get("MATAROA_TEST_POSTGRES_PORT", "5432"),
        "USER": os.environ["MATAROA_TEST_POSTGRES_USER"],
        "PASSWORD": os.environ.get("MATAROA_TEST_POSTGRES_PASSWORD", ""),
        "CONN_MAX_AGE": 0,
    }
}
