from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import create_app
from app.config import TestingConfig
from scripts.seed import run_seed


@pytest.fixture()
def app(tmp_path):
    db_path = str(tmp_path / "test_store.db")
    upload_dir = str(tmp_path / "uploads")
    log_dir = str(tmp_path / "logs")

    class LocalTestConfig(TestingConfig):
        DATABASE_PATH = db_path
        UPLOAD_FOLDER = upload_dir
        LOG_FOLDER = log_dir

    run_seed(db_path=db_path, upload_dir=upload_dir)
    flask_app = create_app(LocalTestConfig)
    yield flask_app


@pytest.fixture()
def client(app):
    return app.test_client()


def extract_csrf(html_bytes: bytes) -> str:
    text = html_bytes.decode("utf-8", errors="ignore")
    m = re.search(r'name="csrf_token"\s+value="([^"]+)"', text)
    if m:
        return m.group(1)
    m2 = re.search(r'name="csrf-token"\s+content="([^"]+)"', text)
    return m2.group(1) if m2 else ""
