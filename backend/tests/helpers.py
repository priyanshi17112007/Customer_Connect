"""Shared test fixtures.

Each test module gets its own temporary SQLite database so runs are repeatable
and the developer's real database is never modified. ``DATABASE_URL`` must be
set before ``app.database.session`` is first imported, so this module performs
that setup at import time and then builds the application.
"""

import os
import pathlib
import sys
import tempfile

BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# A throwaway database, isolated per test session.
_TMP_DIR = tempfile.mkdtemp(prefix="resolveai-tests-")
TEST_DB_PATH = pathlib.Path(_TMP_DIR) / "test_support.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH.as_posix()}"
os.environ.setdefault("APP_API_KEY", "test-key-not-a-real-secret")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("SECURITY_LOG_LEVEL", "ERROR")

from app.database.init_db import init_db, seed_data  # noqa: E402
from app.security import middleware as mw  # noqa: E402

_seeded = False


def ensure_seeded() -> None:
    """Create and seed the temporary database exactly once per session."""
    global _seeded
    if _seeded:
        return
    init_db()
    seed_data()
    _seeded = True


def reset_counters() -> None:
    """Clear the in-memory rate-limit and auth-failure stores.

    The limiter is process-wide and shared by every test in the session, so it
    is reset between tests to keep them independent and order-independent.
    """
    mw._counters.clear()
    mw._auth_failures.clear()


def get_client():
    """Return a FastAPI TestClient for the real application."""
    from fastapi.testclient import TestClient

    ensure_seeded()
    reset_counters()
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


def auth_headers(key: str | None = None) -> dict:
    """Valid auth header, or a deliberately wrong one for negative tests."""
    return {"X-API-Key": key or os.environ["APP_API_KEY"]}