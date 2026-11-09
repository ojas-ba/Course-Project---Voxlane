"""Startup uses files; failed configuration cannot be silently replaced."""

from pathlib import Path
from shutil import copyfile
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from main import app


def test_startup_loads_files_and_runs_local_warmup():
    with patch("main.run_local_warmup", new_callable=AsyncMock) as warm:
        with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000)) as client:
            assert client.get("/").status_code == 200
            assert client.get("/telephony/webhook").status_code == 404
        warm.assert_awaited_once()


def test_startup_rejects_corrupt_config():
    from app import storage
    storage.DATA_DIR.mkdir()
    path = storage.DATA_DIR / "runtime_config.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError):
        with TestClient(app):
            pass
    assert path.read_text(encoding="utf-8") == "{broken"


def test_startup_rejects_unwritable_storage():
    with patch("app.storage.initialize_storage", new=AsyncMock(side_effect=OSError("denied"))):
        with pytest.raises(OSError, match="denied"):
            with TestClient(app):
                pass


def test_startup_rejects_missing_build(tmp_path):
    with patch("main.STATIC_DIR", tmp_path):
        with pytest.raises(RuntimeError, match="assets missing"):
            with TestClient(app):
                pass


@pytest.mark.parametrize("missing", ["candidate-view.js", "base.css", "studio.css", "talk.css"])
def test_startup_rejects_incomplete_ui_assets(tmp_path: Path, missing: str) -> None:
    """Reject a partial UI installation before creating or migrating local data."""
    from main import REQUIRED_BROWSER_ASSETS, STATIC_DIR

    for asset in REQUIRED_BROWSER_ASSETS:
        if asset != missing:
            copyfile(STATIC_DIR / asset, tmp_path / asset)
    with (
        patch("main.STATIC_DIR", tmp_path),
        patch("main.seed_runtime_config_if_missing", new_callable=AsyncMock) as seed,
        pytest.raises(RuntimeError, match=missing.replace(".", r"\.")),
    ):
        with TestClient(app):
            pass
    seed.assert_not_awaited()
