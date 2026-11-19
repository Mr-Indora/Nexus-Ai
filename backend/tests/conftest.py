"""Set isolated test configuration before any module imports application settings."""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory


TEST_DATABASE_DIR = TemporaryDirectory(prefix="nexus-backend-tests-")
TEST_DATABASE_PATH = (Path(TEST_DATABASE_DIR.name) / "test_nexus.db").as_posix()
os.environ.update(
    DATABASE_URL=f"sqlite:///{TEST_DATABASE_PATH}",
    DEMO_MODE="true",
    GEMINI_API_KEY="",
    SECRET_KEY="test-secret-key-0123456789abcdef",
    GOOGLE_CLIENT_ID="cid",
    GOOGLE_CLIENT_SECRET="sec",
    FRONTEND_URL="http://localhost:3000",
    LOCAL_LLM_ENABLED="false",
    LOCAL_LLM_MODE="disabled",
    PIDS_ENABLED="false",
)


def pytest_sessionfinish(session, exitstatus):
    TEST_DATABASE_DIR.cleanup()
