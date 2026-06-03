"""Startup behavior: MongoDB is required (no warn-and-continue)."""

import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from main import app


def test_startup_succeeds_and_seeds_when_db_ok():
    with patch("main.init_database", new_callable=AsyncMock) as init, \
         patch("main.seed_runtime_config_if_missing", new_callable=AsyncMock) as seed, \
         patch("main.close_database", new_callable=AsyncMock):
        with TestClient(app):
            pass
        init.assert_awaited()
        seed.assert_awaited()  # runtime config seed/load attempted at startup


def test_startup_fails_clearly_when_mongo_unavailable():
    with patch("main.init_database", new_callable=AsyncMock) as init, \
         patch("main.close_database", new_callable=AsyncMock):
        init.side_effect = RuntimeError("mongo unavailable")
        with pytest.raises(Exception):
            with TestClient(app):
                pass


def test_startup_tolerates_seed_failure_but_still_starts():
    # init_database succeeded (Mongo up); seeding hiccup must not crash startup,
    # but per-call config load still guards outbound separately.
    with patch("main.init_database", new_callable=AsyncMock), \
         patch("main.seed_runtime_config_if_missing", new_callable=AsyncMock) as seed, \
         patch("main.close_database", new_callable=AsyncMock):
        seed.side_effect = RuntimeError("seed hiccup")
        with TestClient(app) as c:
            assert c.get("/config").status_code == 200
