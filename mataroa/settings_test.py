"""Isolated SQLite settings; enable OAuth without requiring PostgreSQL."""

import os

os.environ["MATAROA_CHATGPT_ENABLED"] = "1"

from mataroa.settings import *  # noqa: F403, E402

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
MATAROA_CHATGPT_ENABLED = True
MATAROA_MCP_ISSUER_URL = "https://mataroa.blog"
MATAROA_MCP_RESOURCE_URL = "https://mataroa.blog/mcp"
MATAROA_CHATGPT_CLIENT_IDS = ("test-chatgpt",)
OAUTH2_PROVIDER = {**OAUTH2_PROVIDER}  # noqa: F405
OAUTH2_PROVIDER["ALWAYS_RELOAD_OAUTHLIB_CORE"] = True  # noqa: F405

# Opt into a disposable PostgreSQL server for row-lock concurrency tests.
if os.environ.get("MATAROA_TEST_POSTGRES_DB"):
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
