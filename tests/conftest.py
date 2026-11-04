"""Gemeinsame Hilfen für die Tests. Sie brauchen nur requirements.txt (+ pytest) –
kein Laya, keine Embeddings, kein Netz."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE_ENV = {
    "SOURCE": "miniflux", "MINIFLUX_URL": "http://127.0.0.1:9", "MINIFLUX_API_TOKEN": "x",
    "SCORING_PROVIDER": "mistral", "SCORING_MODEL": "small", "SUMMARY_PROVIDER": "mistral",
    "SUMMARY_MODEL": "large", "MISTRAL_API_KEY": "test-key", "FEED_TOKEN": "tok",
    "PUBLIC_URL": "https://digest.example.org",
}


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Saubere Umgebung: nur BASE_ENV plus Datenordner im Temp-Verzeichnis."""
    import os
    for k in list(os.environ):
        if k.isupper() and k not in ("PATH", "HOME", "LANG", "TMPDIR", "PYTHONPATH"):
            monkeypatch.delenv(k, raising=False)
    for k, v in BASE_ENV.items():
        monkeypatch.setenv(k, v)
    (tmp_path / "data").mkdir()
    (tmp_path / "config").mkdir()
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path / "config"))
    return monkeypatch


@pytest.fixture
def settings(env):
    from app.config import Settings
    return Settings.from_env()
