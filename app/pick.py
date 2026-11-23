"""Einen Artikel, der knapp nicht in den Digest kam, von Hand nachholen.

Die Status-Seite zeigt die besten verpassten Artikel mit einem Knopf „In den Digest“. Der Artikel
bekommt wie die anderen einen Titel und einen Auszug, kommt in einem eigenen Abschnitt „Von dir
nachgeholt“ ans Ende des neuesten Digests, der Feed wird neu geschrieben – und das Ganze zählt
als stärkstes Lernsignal (2,0): Genau hier lag die Auswahl daneben.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from . import feedback, status
from .config import Settings
from .digest import _load, _save, build_profile, explain, prepare, summarize
from .feed import write_feed
from .llm import LLM
from .sources import make_source

log = logging.getLogger(__name__)

SECTION = "Von dir nachgeholt"


_lock = threading.Lock()  # zwei schnelle Klicks dürfen den Artikel nicht doppelt anhängen


class PickError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def pick(s: Settings, entry_id: str) -> dict:
    """Holt den Artikel in den neuesten Digest und gibt den neuen Eintrag zurück. PickError mit
    HTTP-Code, wenn das nicht geht."""
    if not _lock.acquire(blocking=False):
        raise PickError(409, "Es wird gerade schon ein Artikel nachgeholt – bitte gleich noch einmal.")
    try:
        return _pick(s, str(entry_id))
    finally:
        _lock.release()


def _pick(s: Settings, entry_id: str) -> dict:
    if status.run_active(s.data_dir):
        raise PickError(409, "Gerade läuft ein Digest – bitte gleich noch einmal.")
    rows = [r for r in feedback.read_jsonl(s.data_dir / "history.jsonl") if str(r.get("entry_id")) == entry_id]
    if not rows:
        raise PickError(404, "Diesen Artikel kenne ich nicht.")
    known = rows[-1]
    path = s.data_dir / "digests.json"
    digests = _load(path, [])
    if not digests:
        raise PickError(409, "Es gibt noch keinen Digest, in den der Artikel kommen könnte.")
    if any(r.get("shown") for r in rows) or any(str(it.get("entry_id")) == entry_id
                                                 for d in digests for it in d.get("items", [])):
        raise PickError(409, "Der Artikel steht schon in einem Digest.")

    # vollen Text aus dem Reader holen; ist der Eintrag dort weg, reicht der Titel
    raw = None
    try:
        raw = make_source(s).entry(entry_id)
    except Exception as e:  # noqa: BLE001 – Quelle nicht erreichbar: trotzdem nachholen
        log.warning("Nachholen: Eintrag %s nicht abrufbar (%s), nur mit Titel", entry_id, e)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    items = prepare([raw], s, epoch) if raw else []
    it = items[0] if items else {"id": entry_id, "title": known.get("title") or "(ohne Titel)",
                                 "url": known.get("url") or "", "feed": known.get("feed") or "", "text": ""}
    it.update(score=known.get("score"), score_topic=known.get("topic"), laya_features=known.get("features"),
              laya_v=known.get("laya_v"), prefilter=known.get("prefilter"), picked=True)

    llm = LLM(s.summary_provider, s.summary_model)
    profile = build_profile(s, [], full=s.summary_profile == "full", local=llm.is_local)
    it = summarize(it, llm, profile, s.output_language)

    topic = known.get("topic") if (known.get("topic") or "").lower() not in ("", "sonstiges", "other", "-") else None
    reasons = [r for r in explain(it, s, topic) if r["label"] != "Auswahl"]
    reasons.insert(0, {"label": "Auswahl", "text": "Von dir nachgeholt – die Auswahl hatte den Artikel "
                                                   "nicht drin, du wolltest ihn trotzdem"})
    item = {"entry_id": it["id"], **{k: it.get(k) for k in (
        "digest_title", "title", "url", "feed", "author", "published", "score", "excerpt", "why",
        "reading_time")}, "topic": SECTION, "interest_topic": topic, "picked": True, "reasons": reasons}

    latest = max(digests, key=lambda d: d.get("generated_at", ""))
    latest["items"].append(item)
    _save(path, digests)
    write_feed(s, digests)
    feedback.log_history(s.data_dir, "pick", [{**it, "laya_features": known.get("features")}], {it["id"]})
    feedback._append(s.data_dir / "feedback.jsonl", [{"ts": datetime.now(timezone.utc).isoformat(),
                                                     "entry_id": entry_id, "kind": "pick"}])
    log.info("Nachgeholt in den Digest vom %s: %s", latest.get("date"), item["digest_title"] or item["title"])
    return item
