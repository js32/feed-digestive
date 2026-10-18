"""Rückmeldung sammeln: Bewertungs-Protokoll, Klicks im Digest, Sterne in Miniflux.

Alles liegt lokal in data/:
  history.jsonl   ein Eintrag pro bewertetem Artikel und Lauf (Merkmale, Werte, gezeigt ja/nein)
  feedback.jsonl  ein Eintrag pro Klick bzw. neuem Stern
"""
from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)
_write_lock = threading.Lock()

# Abrufe, die kein Mensch sind: Miniflux-Crawler, Link-Vorschauen, Skripte
BOT_RE = re.compile(r"miniflux|bot|crawl|spider|preview|curl|wget|python|httpx|feedparser|fetch|okhttp|go-http",
                    re.I)
TRACK_RE = re.compile(r"/r/(\d+)(?:$|[?#])")
HISTORY_DAYS = 180


def _append(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with _write_lock, path.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


# ----------------------------------------------------------------------
# Protokoll
# ----------------------------------------------------------------------
def log_history(data_dir: Path, run_id: str, items: list[dict], shown_ids: set[int]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    rows = [{
        "run": run_id, "ts": now, "entry_id": it["id"], "url": it["url"], "title": it["title"],
        "feed": it["feed"], "features": it.get("laya_features"), "prefilter": it.get("prefilter"),
        "score": it.get("score"), "shown": it["id"] in shown_ids,
    } for it in items]
    _append(data_dir / "history.jsonl", rows)


def prune(data_dir: Path) -> None:
    """Alte Protokollzeilen entfernen, damit die Dateien nicht endlos wachsen."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)).isoformat()
    for name in ("history.jsonl", "feedback.jsonl"):
        path = data_dir / name
        rows = read_jsonl(path)
        kept = [r for r in rows if r.get("ts", "") >= cutoff]
        if len(kept) < len(rows):
            with _write_lock:
                tmp = path.with_suffix(".tmp")
                tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept), encoding="utf-8")
                tmp.replace(path)


# ----------------------------------------------------------------------
# Klicks
# ----------------------------------------------------------------------
def tracked_url(feed_base: str, entry_id: int) -> str:
    return f"{feed_base}/r/{entry_id}"


def is_human(user_agent: str | None) -> bool:
    return bool(user_agent) and not BOT_RE.search(user_agent)


def record_click(data_dir: Path, entry_id: int) -> None:
    _append(data_dir / "feedback.jsonl", [{
        "ts": datetime.now(timezone.utc).isoformat(), "entry_id": entry_id, "kind": "click"}])


def lookup_url(data_dir: Path, entry_id: int) -> str | None:
    """Nur IDs, die die App selbst ausgeliefert hat – kein offener Redirect."""
    try:
        digests = json.loads((data_dir / "digests.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        digests = []
    for d in digests:
        for it in d.get("items", []):
            if it.get("entry_id") == entry_id:
                return it.get("url")
    for r in reversed(read_jsonl(data_dir / "history.jsonl")):
        if r.get("entry_id") == entry_id and r.get("shown"):
            return r.get("url")
    return None


# ----------------------------------------------------------------------
# Sterne aus Miniflux
# ----------------------------------------------------------------------
def collect_stars(data_dir: Path, starred_entries: list[dict]) -> int:
    """Neue Sterne in feedback.jsonl übernehmen.

    Gezählt werden Sterne auf Original-Artikeln (gleiche Miniflux-ID wie im Protokoll) und
    auf Einträgen des Digest-Feeds selbst (ID steckt im /r/<id>-Link).
    """
    known = {r["entry_id"] for r in read_jsonl(data_dir / "feedback.jsonl") if r.get("kind") == "star"}
    now = datetime.now(timezone.utc).isoformat()
    new = []
    for e in starred_entries:
        ids = {e.get("id")}
        m = TRACK_RE.search(e.get("url") or "")
        if m:
            ids = {int(m.group(1))}
        for eid in ids:
            if eid and eid not in known:
                known.add(eid)
                new.append({"ts": now, "entry_id": eid, "kind": "star"})
    _append(data_dir / "feedback.jsonl", new)
    if new:
        log.info("%d neue Sterne aus Miniflux übernommen", len(new))
    return len(new)
