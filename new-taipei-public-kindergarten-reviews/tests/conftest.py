"""
pytest fixtures for the kindergarten pipeline test suite.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

# Ensure project root is importable
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Point to in-memory SQLite for tests
os.environ["DATABASE_URL"] = "sqlite:///:memory:"


from database.db import build_engine, init_db
from database.models import Base


@pytest.fixture(scope="function")
def engine():
    """Create a fresh in-memory SQLite engine per test."""
    eng = build_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture(scope="function")
def session(engine):
    """Provide a Session that rolls back after each test."""
    with Session(engine) as s:
        yield s
        s.rollback()
