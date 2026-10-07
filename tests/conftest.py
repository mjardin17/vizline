"""Shared pytest fixtures. Tests never touch the network."""
from __future__ import annotations

import pytest

from vigline.db import get_connection, run_migrations


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.db"


@pytest.fixture
def conn(db_path):
    c = get_connection(db_path)
    run_migrations(c)
    yield c
    c.close()
