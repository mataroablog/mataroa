"""Run ORM/OAuth tests against a migrated, isolated Django test database."""

import os
import sys
from pathlib import Path

import pytest

# This package lives in the Mataroa source tree at plugins/chatgpt.
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.settings")


@pytest.fixture(scope="session", autouse=True)
def django_environment():
    import django
    from django.test.utils import (
        setup_databases,
        setup_test_environment,
        teardown_databases,
        teardown_test_environment,
    )

    django.setup()
    setup_test_environment()
    databases = setup_databases(verbosity=0, interactive=False)
    yield
    teardown_databases(databases, verbosity=0)
    teardown_test_environment()


# Django models are imported during test collection, before session fixtures run.
import django  # noqa: E402

django.setup()
