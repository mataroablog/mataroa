"""Isolated SQLite settings; never connect plugin tests to a live database."""

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
OAUTH2_PROVIDER["ALWAYS_RELOAD_OAUTHLIB_CORE"] = True  # noqa: F405
