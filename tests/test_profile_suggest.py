"""Profilvorschlag: was ans Modell geht, Mindestmenge, PRIVACY=strict, lokaler Abschnitt bleibt lokal."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app import profile_suggest as ps
from app.config import Settings

NOW = datetime.now(timezone.utc)


def _setup(s, clicks=6):
    hist, fb = [], []
    for i in range(clicks):
        hist.append({"entry_id": f"c{i}", "ts": (NOW - timedelta(days=20)).isoformat(), "title": f"Geklickt {i}",
                     "feed": "Blog", "shown": True})
        fb.append({"entry_id": f"c{i}", "kind": "click", "ts": (NOW - timedelta(days=i)).isoformat()})
    for i in range(3):
        hist.append({"entry_id": f"n{i}", "ts": (NOW - timedelta(days=20)).isoformat(), "title": f"Liegen {i}",
                     "feed": "News", "shown": True})
    hist.append({"entry_id": "frisch", "ts": NOW.isoformat(), "title": "Gerade erst gezeigt", "feed": "News",
                 "shown": True})
    hist.append({"entry_id": "nie", "ts": (NOW - timedelta(days=20)).isoformat(), "title": "Nie gezeigt",
                 "feed": "News", "shown": False})
    (s.data_dir / "history.jsonl").write_text("".join(json.dumps(r) + "\n" for r in hist))
    (s.data_dir / "feedback.jsonl").write_text("".join(json.dumps(r) + "\n" for r in fb))
    (s.config_dir / "interests.md").write_text(
        "## Hohe Priorität\n- Philosophie\n<!-- local-only -->\n- Privates Thema\n<!-- /local-only -->\n")


class FakeLLM:
    is_local = False
    calls: list = []

    def __init__(self, provider, model):
        pass

    def complete(self, system, user, max_tokens=0, purpose=""):
        FakeLLM.calls.append((system, user, purpose))
        return "## Ergänzen\n- Retro-Computing (Beleg: Geklickt 1)"


@pytest.fixture
def fake_llm(monkeypatch):
    FakeLLM.calls, FakeLLM.is_local = [], False
    monkeypatch.setattr(ps, "LLM", FakeLLM)
    return FakeLLM


def test_collect_separates_liked_and_ignored(settings):
    _setup(settings)
    liked, ignored = ps.collect(settings)
    assert len(liked) == 6 and liked[0][1] == "Blog: Geklickt 0"       # frischestes Signal zuerst
    assert liked[0][0] > liked[-1][0]
    assert sorted(ignored) == ["News: Liegen 0", "News: Liegen 1", "News: Liegen 2"]
    assert not any("Gerade erst" in t or "Nie gezeigt" in t for t in ignored)


def test_too_few_signals_makes_no_call(settings, fake_llm):
    _setup(settings, clicks=2)
    assert "zu wenig" in ps.suggest(settings)
    assert fake_llm.calls == []


def test_suggestion_is_saved_but_profile_untouched(settings, fake_llm):
    _setup(settings)
    before = (settings.config_dir / "interests.md").read_text()
    out = ps.suggest(settings)
    assert "Retro-Computing" in out and "Nichts davon gilt automatisch" in out
    assert (settings.data_dir / "interests.suggested.md").read_text().strip() == out.strip()
    assert (settings.config_dir / "interests.md").read_text() == before
    system, user, purpose = fake_llm.calls[0]
    assert purpose == "Profilvorschlag" and "Geklickt 3" in user and "Liegen 1" in user
    assert "Privates Thema" not in user  # local-only bleibt bei Cloud-Anbietern draußen


def test_local_model_gets_private_section(settings, fake_llm):
    _setup(settings)
    fake_llm.is_local = True
    ps.suggest(settings)
    assert "Privates Thema" in fake_llm.calls[0][1]


def test_strict_privacy_refuses_cloud(env, fake_llm):
    env.setenv("PRIVACY", "strict")
    s = Settings.from_env()
    _setup(s)
    assert "PRIVACY=strict" in ps.suggest(s)
    assert fake_llm.calls == []
