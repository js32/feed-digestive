"""Laya-Version und vega: ohne echtes Laya getestet – ein Platzhalter-Modul nimmt die Aufrufe entgegen."""
import json
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

from app import laya_try as T

QUESTIONS = {"topics": {"phil": {"label": "Philosophie", "question": "Geht es um Philosophie?"}},
             "depth": {"question": "Wie tief?", "levels": ["flach", "mittel", "tief"]},
             "penalties": {"promo": {"label": "PR", "question": "Ist es Werbung?"}}}


@pytest.fixture
def fake_laya(monkeypatch):
    calls = {"router": [], "requests": []}

    class Router:
        def __init__(self, **kw):
            calls["router"].append(kw)

        def predict_batch(self, requests, batch_size=None, sort_by_length=False):
            calls["requests"] += requests
            out = []
            for r in requests:
                p = 0.9 if "Philosophie" in r["state"] else 0.1  # "versteht" Philosophie
                out.append({"answers": {"topic_phil": {"noul": p}, "depth": {"score": 2},
                                        "penalty_promo": {"noul": 0.0}},
                            "routing": {"model": r.get("model") or "multilingual"}})
            return out

    laya = types.ModuleType("laya")
    laya.Router = Router
    torch = types.ModuleType("torch")
    torch.set_num_threads = lambda n: None
    monkeypatch.setitem(sys.modules, "laya", laya)
    monkeypatch.setitem(sys.modules, "torch", torch)
    version = {"laya": "0.5.3"}
    import importlib.metadata as md
    monkeypatch.setattr(md, "version", lambda name: version[name])
    return calls, version


@pytest.fixture
def qfile(tmp_path):
    p = tmp_path / "laya_questions.json"
    p.write_text(json.dumps(QUESTIONS))
    return p


def test_classic_models_do_not_turn_on_vega(fake_laya, qfile):
    from app.laya_filter import LayaFilter
    calls, _ = fake_laya
    LayaFilter(qfile, model="auto")
    LayaFilter(qfile, model="multilingual")
    assert all("vega" not in kw for kw in calls["router"])


def test_vega_needs_laya_05(fake_laya, qfile):
    from app.laya_filter import LayaFilter
    calls, version = fake_laya
    version["laya"] = "0.4.11"
    with pytest.raises(SystemExit, match="0.5"):
        LayaFilter(qfile, model="vega")


def test_vega_routes_every_request_to_vega(fake_laya, qfile, monkeypatch):
    from app.laya_filter import LayaFilter
    calls, _ = fake_laya
    flt = LayaFilter(qfile, model="vega")
    assert calls["router"][-1]["vega"] is True
    monkeypatch.setenv("LAYA_VEGA_READER", "4b")
    LayaFilter(qfile, model="vega")
    assert calls["router"][-1]["vega"] == "4b"
    items = [{"feed": "B", "title": "Philosophie heute", "text": "Philosophie " * 20}]
    flt._run_laya(items)
    assert calls["requests"][-1]["model"] == "vega" and items[0]["laya_features"]["topic_phil"] == 0.9


def test_spearman_and_overlap():
    assert T.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert T.spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)
    assert T.spearman([1, 1, 1], [1, 2, 3]) is None
    assert T.ranks([5, 1, 5]) == [2.5, 1.0, 2.5]
    assert T.top_overlap([9, 8, 1, 0, 0, 0], [9, 1, 8, 0, 0, 0]) == (1, 2)


def test_laya_try_end_to_end(fake_laya, settings, monkeypatch):
    s = settings
    (s.config_dir / "laya_questions.json").write_text(json.dumps(QUESTIONS))
    now = datetime.now(timezone.utc)
    rows = [{"entry_id": str(i), "ts": (now - timedelta(hours=i)).isoformat(), "score": sc, "title": t}
            for i, (sc, t) in enumerate([(9, "Philosophie der Zeit"), (8, "Philosophie und Technik"),
                                         (3, "Neues Handy"), (2, "Börsenkurse"), (7, "Philosophie im Alltag"),
                                         (1, "Rabattaktion")])]
    (s.data_dir / "history.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    class Src:
        def entry(self, eid):
            r = rows[int(eid)]
            return {"id": eid, "title": r["title"], "url": f"https://ex.org/{eid}", "feed_title": "Blog",
                    "feed_id": "1", "published_at": now.isoformat(), "content": "<p>" + (r["title"] + " ") * 30 + "</p>"}

    monkeypatch.setattr(T, "make_source", lambda s: Src())
    out = T.run(s, "vega", 6)
    assert "Laya 0.5.3, Modell vega, 6 Artikel" in out
    assert "Rangkorrelation +0.8" in out or "Rangkorrelation +0.9" in out
    assert "Von den 2 besten laut Mistral landen 2" in out
    assert "laya-try auto 6" in out
    assert T.run(s, "turbo") .startswith("Unbekanntes Modell")
