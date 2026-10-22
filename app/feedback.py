"""Rückmeldung sammeln: Bewertungs-Protokoll, Klicks im Digest, Sterne im Feedreader.

Alles liegt lokal in data/:
  history.jsonl   ein Eintrag pro bewertetem Artikel und Lauf (Merkmale, Werte, gezeigt ja/nein)
  feedback.jsonl  ein Eintrag pro Signal: "read" (im Reader angelesen), "click" (Zum Artikel), "star"
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
TRACK_RE = re.compile(r"/r/([A-Za-z0-9_-]+)(?:$|[?#])")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
HISTORY_DAYS = 180

# Wie stark ein Signal für Interesse spricht (Gewicht beim Lernen). Zählt pro Artikel das stärkste.
SIGNAL_WEIGHTS = {"read": 0.3, "click": 1.0, "star": 1.5, "up": 2.0}
# 👎 "weniger davon": deutliches Negativbeispiel, schlägt alle anderen Signale
DOWN_WEIGHT = 2.0
# "Alle als gelesen markieren": so viele Einträge innerhalb weniger Sekunden zählen nicht als angelesen
BULK_MIN, BULK_SECONDS = 4, 15


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
        "run": run_id, "ts": now, "entry_id": str(it["id"]), "url": it["url"], "title": it["title"],
        "feed": it["feed"], "features": it.get("laya_features"), "prefilter": it.get("prefilter"),
        "score": it.get("score"), "shown": str(it["id"]) in {str(x) for x in shown_ids},
    } for it in items]
    _append(data_dir / "history.jsonl", rows)


def load_cache(data_dir: Path) -> dict[str, dict]:
    """Bisheriges Wissen je Artikel: zuletzt bekannte Laya-Merkmale, LLM-Wert, schon gezeigt?"""
    cache: dict[str, dict] = {}
    for r in read_jsonl(data_dir / "history.jsonl"):
        c = cache.setdefault(str(r.get("entry_id")), {"features": None, "score": None, "shown": False})
        if r.get("features"):
            c["features"] = r["features"]
        if r.get("score") is not None:
            c["score"] = r["score"]
        c["shown"] = c["shown"] or bool(r.get("shown"))
    try:
        digests = json.loads((data_dir / "digests.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        digests = []
    for d in digests:
        for it in d.get("items", []):
            if it.get("entry_id") is not None:
                cache.setdefault(str(it["entry_id"]), {"features": None, "score": None, "shown": False})["shown"] = True
    return cache


def prune(data_dir: Path) -> None:
    """Alte Protokollzeilen entfernen, damit die Dateien nicht endlos wachsen."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)).isoformat()
    for name in ("history.jsonl", "feedback.jsonl", "opens.jsonl"):
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
def tracked_url(feed_base: str, entry_id) -> str:
    return f"{feed_base}/r/{entry_id}"


def vote_url(feed_base: str, entry_id, kind: str) -> str:
    return f"{feed_base}/v/{entry_id}/{kind}"


def pixel_url(feed_base: str, entry_id) -> str:
    return f"{feed_base}/p/{entry_id}.gif"


def is_human(user_agent: str | None) -> bool:
    return bool(user_agent) and not BOT_RE.search(user_agent)


def record_click(data_dir: Path, entry_id: str, day_only: bool = False) -> None:
    """day_only (PRIVACY=strict): nur das Datum speichern, nicht die Uhrzeit des Klicks."""
    now = datetime.now(timezone.utc)
    if day_only:
        now = now.replace(hour=0, minute=0, second=0, microsecond=0)
    _append(data_dir / "feedback.jsonl", [{"ts": now.isoformat(), "entry_id": entry_id, "kind": "click"}])


def lookup_title(data_dir: Path, entry_id: str) -> str | None:
    try:
        digests = json.loads((data_dir / "digests.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    for d in digests:
        for it in d.get("items", []):
            if str(it.get("entry_id")) == entry_id:
                return it.get("digest_title") or it.get("title")
    return None


def lookup_url(data_dir: Path, entry_id: str) -> str | None:
    """Nur IDs, die die App selbst ausgeliefert hat – kein offener Redirect."""
    try:
        digests = json.loads((data_dir / "digests.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        digests = []
    for d in digests:
        for it in d.get("items", []):
            if str(it.get("entry_id")) == entry_id:
                return it.get("url")
    for r in reversed(read_jsonl(data_dir / "history.jsonl")):
        if str(r.get("entry_id")) == entry_id and r.get("shown"):
            return r.get("url")
    return None


# ----------------------------------------------------------------------
# Sterne aus dem Feedreader
# ----------------------------------------------------------------------
def collect_stars(data_dir: Path, starred_entries: list[dict], quiet: bool = False) -> int:
    """Neue Sterne in feedback.jsonl übernehmen.

    Gezählt werden Sterne auf Original-Artikeln (gleiche Quell-ID wie im Protokoll) und
    auf Einträgen des Digest-Feeds selbst (ID steckt im /r/<id>-Link).
    """
    known = {str(r["entry_id"]) for r in read_jsonl(data_dir / "feedback.jsonl") if r.get("kind") == "star"}
    now = datetime.now(timezone.utc).isoformat()
    new = []
    for e in starred_entries:
        ids = {str(e.get("id") or "")}
        m = TRACK_RE.search(e.get("url") or "")
        if m:
            ids = {m.group(1)}
        for eid in ids:
            if eid and eid not in known:
                known.add(eid)
                new.append({"ts": now, "entry_id": eid, "kind": "star"})
    _append(data_dir / "feedback.jsonl", new)
    if new or not quiet:  # beim Lauf immer, beim regelmäßigen Abgleich nur wenn sich etwas tut
        log.info("Sterne: %d im Feedreader, %d neu übernommen", len(starred_entries), len(new))
    return len(new)


# ----------------------------------------------------------------------
# Im Reader angelesen (Digest-Einträge, die der Reader als gelesen meldet)
# ----------------------------------------------------------------------
def votes(rows: list[dict]) -> dict[str, str]:
    """entry_id -> "up" | "down", die jeweils letzte Stimme zählt (umentscheiden geht)."""
    out: dict[str, str] = {}
    for r in rows:
        if r.get("kind") in ("up", "down"):
            out[str(r.get("entry_id"))] = r["kind"]
    return out


def signals(rows: list[dict]) -> dict[str, dict[str, float]]:
    """entry_id -> {kind: Gewicht} der positiven Signale aus feedback.jsonl (ohne 👎-Artikel)."""
    vote = votes(rows)
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        kind = r.get("kind") or "click"
        eid = str(r.get("entry_id"))
        if kind == "up" and vote.get(eid) != "up":
            continue  # später zurückgenommen
        if kind in SIGNAL_WEIGHTS and vote.get(eid) != "down":
            out.setdefault(eid, {})[kind] = SIGNAL_WEIGHTS[kind]
    return out


def record_vote(data_dir: Path, entry_id: str, kind: str, day_only: bool = False) -> None:
    now = datetime.now(timezone.utc)
    if day_only:
        now = now.replace(hour=0, minute=0, second=0, microsecond=0)
    _append(data_dir / "feedback.jsonl", [{"ts": now.isoformat(), "entry_id": entry_id, "kind": kind}])


def record_open(data_dir: Path, entry_id: str, user_agent: str | None, since_feed: float | None) -> None:
    """Messung fürs unsichtbare Bild (OPEN_PIXEL=log) – getrennt von feedback.jsonl, zählt nicht fürs Lernen."""
    _append(data_dir / "opens.jsonl", [{
        "ts": datetime.now(timezone.utc).isoformat(), "entry_id": entry_id,
        "ua": (user_agent or "")[:120], "since_feed": None if since_feed is None else round(since_feed, 1)}])


def _bulk(times: list[float]) -> set[int]:
    """Indizes von Einträgen, die in einem Rutsch als gelesen markiert wurden."""
    order = sorted(range(len(times)), key=lambda i: times[i])
    bulk, start = set(), 0
    for end in range(1, len(order) + 1):
        if end == len(order) or times[order[end]] - times[order[end - 1]] > BULK_SECONDS:
            group = order[start:end]
            if len(group) >= BULK_MIN:
                bulk.update(group)
            start = end
    return bulk


def collect_reads(data_dir: Path, read_entries: list[dict], day_only: bool = False, quiet: bool = False) -> int:
    """Gelesene Digest-Einträge aus dem Reader als schwaches Signal "read" übernehmen.

    read_entries: Einträge des Digest-Feeds im Reader mit Status gelesen, je {"url", "changed_at"}.
    Die Artikel-ID steckt im /r/<id>-Link; ohne Klick-Tracking wird über die URL zugeordnet.
    Massen-Markierungen ("alle gelesen") werden ignoriert.
    """
    if not read_entries:
        return 0
    try:
        digests = json.loads((data_dir / "digests.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        digests = []
    by_url = {it.get("url"): str(it.get("entry_id")) for d in digests for it in d.get("items", [])}
    shown = set(by_url.values())
    known = {str(r["entry_id"]) for r in read_jsonl(data_dir / "feedback.jsonl") if r.get("kind") == "read"}

    times = []
    for e in read_entries:
        t = e.get("changed_at")
        try:
            times.append(datetime.fromisoformat(str(t).replace("Z", "+00:00")).timestamp())
        except ValueError:
            times.append(0.0)
    bulk = _bulk(times)
    new, skipped = [], 0
    for i, e in enumerate(read_entries):
        m = TRACK_RE.search(e.get("url") or "")
        eid = m.group(1) if m else by_url.get(e.get("url"))
        if not eid or eid not in shown or eid in known:
            continue
        if i in bulk:
            skipped += 1
            continue
        when = datetime.fromtimestamp(times[i], timezone.utc) if times[i] else datetime.now(timezone.utc)
        if day_only:
            when = when.replace(hour=0, minute=0, second=0, microsecond=0)
        known.add(eid)
        new.append({"ts": when.isoformat(), "entry_id": eid, "kind": "read"})
    _append(data_dir / "feedback.jsonl", new)
    if new or not quiet:
        log.info("Angelesen: %d Digest-Einträge gelesen, %d neu übernommen%s", len(read_entries), len(new),
                 f", {skipped} ignoriert (alle auf einmal als gelesen markiert)" if skipped else "")
    return len(new)


def collect_reader_reads(s, src, quiet: bool = False) -> int:
    """Angelesen-Signal aus dem Reader holen – nur sinnvoll mit FEED_MODE=items (ein Eintrag je Artikel)."""
    if not s.read_signal or s.feed_mode != "items" or not s.learn:
        return 0
    try:
        entries = src.digest_reads(s.feed_path)
    except Exception as e:  # Reader kurz weg: nächstes Mal wieder
        log.warning("Angelesen-Abgleich fehlgeschlagen: %s", e)
        return 0
    if entries is None:
        if not quiet:
            log.info("Angelesen: mit dieser Quelle (noch) nicht unterstützt – nur Klicks und Sterne")
        return 0
    return collect_reads(s.data_dir, entries, day_only=s.privacy == "strict", quiet=quiet)


def opens_report(data_dir: Path, days: int = 14) -> str:
    """Auswertung der Pixel-Messung: lädt der Reader das Bild nur beim Öffnen oder alles vorab?"""
    rows = read_jsonl(data_dir / "opens.jsonl")
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = [r for r in rows if r.get("ts", "") >= since]
    try:
        digests = json.loads((data_dir / "digests.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        digests = []
    shown = {str(it.get("entry_id")): (it.get("digest_title") or it.get("title") or "")
             for d in digests if d.get("generated_at", "") >= since for it in d.get("items", [])}
    if not rows:
        return (f"Keine Pixel-Abrufe in den letzten {days} Tagen. OPEN_PIXEL=log gesetzt, Container neu "
                "gestartet und der Reader hat den Feed seitdem geladen?")
    first: dict[str, dict] = {}
    for r in rows:
        first.setdefault(str(r["entry_id"]), r)
    times = [datetime.fromisoformat(r["ts"]).timestamp() for r in first.values()]
    bulk = _bulk(times)
    quick = [r for r in first.values() if r.get("since_feed") is not None and r["since_feed"] < 120]
    lines = [f"Pixel-Messung, letzte {days} Tage",
             f"Gezeigte Artikel: {len(shown)}   mit Pixel-Abruf: {len(first)} "
             f"({len(first) / max(1, len(shown)):.0%})",
             f"davon in Gruppen (≥{BULK_MIN} innerhalb {BULK_SECONDS} s – sieht nach Vorladen aus): {len(bulk)}",
             f"davon bis 2 min nach einem Feed-Abruf: {len(quick)}",
             "Kennungen: " + ", ".join(sorted({(r.get('ua') or '?')[:40] for r in rows}))[:400], ""]
    if len(first) >= 0.8 * max(1, len(shown)) and len(bulk) >= 0.5 * len(first):
        lines.append("→ Der Reader lädt das Bild offenbar für fast alles vorab. Als Signal untauglich.")
    elif len(first) < 0.8 * max(1, len(shown)) and len(bulk) < 0.3 * max(1, len(first)):
        lines.append("→ Sieht nach echten Öffnungen aus: nur ein Teil, einzeln über den Tag verteilt.")
    else:
        lines.append("→ Noch nicht eindeutig – ein paar Tage weiter messen.")
    lines.append("\nErste Abrufe (neueste zuerst):")
    for i, r in sorted(enumerate(first.values()), key=lambda t: t[1]["ts"], reverse=True)[:25]:
        mark = " [Gruppe]" if i in bulk else ""
        lines.append(f"  {r['ts'][5:16].replace('T', ' ')}{mark}  {shown.get(str(r['entry_id']), r['entry_id'])[:60]}")
    return "\n".join(lines)
