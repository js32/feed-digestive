"""Ein ganzer Lauf mit nachgebauter Quelle und nachgebautem LLM (ohne lokale Modelle, ohne Netz):
Digest, Gründe, Themenfeeds und Benachrichtigung greifen ineinander."""
import json
from datetime import datetime, timezone

import pytest

from app import digest as D
from app.config import Settings

TOPIC_OF = {"KI": "Künstliche Intelligenz", "Phil": "Philosophie"}


class FakeSource:
    name = "Test"

    def fetch_unread(self, limit):
        now = datetime.now(timezone.utc).isoformat()
        return [{"id": i, "title": f"{kind} Artikel {i}", "url": f"https://example.org/{i}", "feed_id": i,
                 "feed_title": f"Feed {i}", "published_at": now, "content": "<p>" + "Text " * 80 + "</p>"}
                for i, kind in enumerate(["KI", "KI", "KI", "KI", "Phil", "Phil", "Rest"], 1)]

    def starred(self, limit=300):
        return []

    def digest_reads(self, feed_path, limit=300):
        return None


class FakeLLM:
    is_local = False

    def __init__(self, provider, model):
        self.model = model

    def __repr__(self):
        return f"fake:{self.model}"

    def complete_json(self, system, user, max_tokens=0, purpose=""):
        if purpose == "Bewertung":
            out = []
            for block in user.split("\n\n")[1:]:
                n = int(block.split("\n")[0].removeprefix("id: "))
                kind = block.split("titel: ")[1].split(" ")[0]
                out.append({"id": n, "score": {"KI": 8, "Phil": 7}.get(kind, 2), "why": f"Grund {kind}",
                            "topic": TOPIC_OF.get(kind, "Sonstiges")})
            return {"scores": out}
        if purpose == "Auszug":
            title = user.split("Originaltitel: ")[1].split("\n")[0]
            return {"title": "DE: " + title, "excerpt": "Du erfährst etwas.", "why": "Weil."}
        return {"topics": []}  # keine Rubriken


@pytest.fixture
def run_env(env, monkeypatch):
    env.setenv("SELECT_FROM", "unread")
    env.setenv("ARTICLES", "4-6")
    env.setenv("EXPLORE_SLOTS", "0")
    s = Settings.from_env()
    (s.config_dir / "interests.md").write_text(
        "## Hohe Priorität\n- Künstliche Intelligenz: Modelle\n- Philosophie\n")
    monkeypatch.setattr(D, "make_source", lambda s: FakeSource())
    monkeypatch.setattr(D, "LLM", FakeLLM)
    return s


def test_full_run(run_env):
    s = run_env
    digest = D.run(s)
    items = digest["items"]
    topics = [it["interest_topic"] for it in items]
    # MAX_PER_TOPIC=3: erst drei KI, dann das andere Thema; der vierte KI-Artikel füllt nur einen freien Platz
    ki, phil = "Künstliche Intelligenz", "Philosophie"
    assert topics == [ki, ki, ki, phil, phil, ki]
    first = items[0]
    assert first["digest_title"].startswith("DE: ")
    reasons = {r["label"]: r["text"] for r in first["reasons"]}
    assert reasons["Auswahl"].startswith("Regulär") and reasons["Note"] == "8/10 – Grund KI"
    assert reasons["Thema"] == "Künstliche Intelligenz"

    saved = json.loads((s.data_dir / "digests.json").read_text())
    assert saved[0]["items"][0]["reasons"] == first["reasons"]
    assert (s.data_dir / "feed.xml").exists()
    assert sorted(p.name for p in (s.data_dir / "topics").glob("*.xml")) == [
        "kuenstliche-intelligenz.xml", "philosophie.xml"]

    from app import status
    snap = status.snapshot(s)
    assert snap["latest"]["items"][0]["reasons"] == first["reasons"]
    assert {t["topic"]: t["url"] for t in snap["topic_feeds"]}["Philosophie"] == \
        "https://digest.example.org/tok/topic/philosophie.xml"


def test_second_run_shows_nothing_twice(run_env):
    s = run_env
    first = {it["entry_id"] for it in D.run(s)["items"]}
    second = D.run(s)
    assert second is None or not first & {it["entry_id"] for it in second["items"]}


def test_run_notifies(run_env, env, monkeypatch):
    env.setenv("NTFY_URL", "https://ntfy.sh/thema")
    s = Settings.from_env()
    sent = []
    from app import __main__ as M
    from app import notify
    monkeypatch.setattr(notify, "send", lambda s, title, message, **k: sent.append((title, message)) or {})
    assert M._run_safely(s, "Test") is True
    assert sent and sent[0][0].endswith("6 Artikel")
    monkeypatch.setattr(M, "run", lambda s: (_ for _ in ()).throw(RuntimeError("kaputt")))
    assert M._run_safely(s, "Test") is False
    assert sent[-1][0] == "Digest-Lauf fehlgeschlagen"
