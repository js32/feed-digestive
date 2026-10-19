"""Status-Seite: Logs und Laufzustand als Dateien in data/status/ – so sieht die Seite auch
Läufe, die per `docker compose exec … run` in einem eigenen Prozess gestartet wurden."""
from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Settings

LOG_MAX_BYTES = 2_000_000  # danach wird log.jsonl zu log.1.jsonl
TAIL_LINES = 3000          # so viele Zeilen bekommt die Seite beim ersten Abruf
HISTORY_RUNS = 30          # so viele Läufe merkt sich run.json für den Verlauf
SCORE_DAYS = 7             # Zeitraum für die Score-Verteilung
NEAR_MISS_DAYS = 2         # "Knapp nicht drin" aus so vielen Tagen
PAGE = Path(__file__).with_name("status.html")

_mu = threading.Lock()
next_run: datetime | None = None  # setzt der Server-Prozess


class RunBusy(Exception):
    """Ein anderer Prozess führt gerade einen Lauf aus."""


def _dir(data_dir: Path) -> Path:
    d = data_dir / "status"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def _save(path: Path, data) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# ----------------------------------------------------------------------
# Logs: eine JSON-Zeile pro Eintrag, alle Prozesse hängen an dieselbe Datei an
# ----------------------------------------------------------------------
class FileLog(logging.Handler):
    def __init__(self, data_dir: Path):
        super().__init__(level=logging.INFO)
        self.path = _dir(data_dir) / "log.jsonl"

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
            if record.exc_info:
                msg += "\n" + logging.Formatter().formatException(record.exc_info)
            line = json.dumps({"t": record.created, "level": record.levelname, "msg": msg,
                               "pid": os.getpid()}, ensure_ascii=False) + "\n"
            with _mu, open(self.path, "a", encoding="utf-8") as f:  # bei jedem Eintrag neu öffnen,
                f.write(line)                                      # damit Umbenennen nichts verliert
        except Exception:
            self.handleError(record)


def _log_path(data_dir: Path) -> Path:
    return _dir(data_dir) / "log.jsonl"


def _log_pos(data_dir: Path) -> tuple[int, int]:
    """(Generation, Größe) der Log-Datei – die Generation ändert sich beim Umbenennen."""
    try:
        st = _log_path(data_dir).stat()
        return st.st_ino, st.st_size
    except FileNotFoundError:
        return 0, 0


def rotate_log(data_dir: Path) -> None:
    path = _log_path(data_dir)
    with _mu:
        if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
            path.replace(path.with_name("log.1.jsonl"))


def _read_lines(data_dir: Path, after: int) -> tuple[list[dict], int]:
    """Zeilen ab Byte-Position after, jede mit ihrer Position als fortlaufender Nummer n."""
    with open(_log_path(data_dir), "rb") as f:
        f.seek(after)
        data = f.read()
    data = data[:data.rfind(b"\n") + 1]  # halb geschriebene letzte Zeile weglassen
    lines, pos = [], after
    for raw in data.splitlines(keepends=True):
        try:
            lines.append({**json.loads(raw), "n": pos})
        except ValueError:
            pass
        pos += len(raw)
    return lines, after + len(data)


def read_log(data_dir: Path, after: int, gen: int) -> dict:
    cur_gen, size = _log_pos(data_dir)
    if not cur_gen:
        return {"lines": [], "seq": 0, "gen": 0}
    if gen != cur_gen or after > size:
        after = 0
    lines, seq = _read_lines(data_dir, after)
    return {"lines": lines[-TAIL_LINES:], "seq": seq, "gen": cur_gen}


# ----------------------------------------------------------------------
# Fortschritt eines Laufs aus seinen Log-Zeilen
# ----------------------------------------------------------------------
# Jede Stufe beginnt mit der Log-Zeile, die digest.run an dieser Stelle schreibt
STAGE_STARTS = [None,
                re.compile(r"Einträge aus "),
                re.compile(r"^Laya|^Bewerte \d+ Artikel|^Bewertung = Laya"),
                re.compile(r"^Embeddings mit |Artikel ausgewählt"),
                re.compile(r"Artikel ausgewählt"),
                re.compile(r"nach Themen gruppiert|Keine Themengruppen|Themengruppen fehlgeschlagen")]
STATS = [  # Muster -> Kennzahlen
    (re.compile(r"^(\d+) (?:ungelesene|neue) Einträge aus "), ("fetched",)),
    (re.compile(r"^(\d+) nach Filter/Deduplizierung \((\d+) waren"), ("kept", "already_shown")),
    (re.compile(r"^Laya-Vorfilter: (\d+) von \d+ Artikeln behalten"), ("prefilter_kept",)),
    (re.compile(r"^Bewerte (\d+) Artikel .*\((\d+) aus früheren"), ("scored_new", "scored_cached")),
    (re.compile(r"^(\d+) Artikel ausgewählt \(davon (\d+) Zufallsfund\)"), ("chosen", "explore")),
]
WAIT_RE = re.compile(r"^(\w+): warte (\d+)s \(LLM_MIN_INTERVAL\)$")


def progress(data_dir: Path, run: dict) -> dict | None:
    """Stufe, Kennzahlen und letzte Meldung eines Laufs – None, wenn seine Zeilen nicht mehr im Log sind."""
    gen, _ = _log_pos(data_dir)
    if not run or run.get("gen") != gen:
        return None
    lines = [ln for ln in _read_lines(data_dir, run["first_log"])[0] if ln.get("pid") == run.get("pid")]
    stage, stats, detail, wait = 0, {}, "", None
    for ln in lines:
        msg = ln["msg"]
        for i, rx in enumerate(STAGE_STARTS):
            if rx and i > stage and rx.search(msg):
                stage = i
        for rx, keys in STATS:
            m = rx.search(msg)
            if m:
                stats.update({k: int(v) for k, v in zip(keys, m.groups())})
        if msg.startswith("Digest nach Themen gruppiert: "):
            stats["topics"] = msg.count("), ") + 1
        elif msg.startswith(("Keine Themengruppen", "Themengruppen fehlgeschlagen")):
            stats["topics"] = 0
        elif msg.startswith("Feed aktualisiert"):
            stats["feed_at"] = ln["t"]
        m = WAIT_RE.match(msg)
        wait = {"provider": m.group(1), "seconds": int(m.group(2)), "t": ln["t"]} if m else None
        if not m:
            detail = msg.split("\n", 1)[0]
    return {"stage": stage, "stats": stats, "detail": detail, "wait": wait}


# ----------------------------------------------------------------------
# Laufzustand: Dateisperre + run.json, gemeinsam für Server und `run`
# ----------------------------------------------------------------------
@contextmanager
def tracked(s: Settings, source: str):
    """Hält die Sperre für einen Lauf und protokolliert Beginn und Ende in run.json.
    RunBusy, wenn schon ein anderer Lauf aktiv ist."""
    d = _dir(s.data_dir)
    lock = open(d / "run.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise RunBusy
    rotate_log(s.data_dir)
    gen, pos = _log_pos(s.data_dir)
    current = {"source": source, "pid": os.getpid(), "started": _now(), "first_log": pos, "gen": gen}
    state = _load(d / "run.json", {})
    _save(d / "run.json", {**state, "current": current})
    error = None
    try:
        yield
    except BaseException as e:
        error = str(e) if isinstance(e, SystemExit) else f"{type(e).__name__}: {e}"
        raise
    finally:
        ended = _now()
        seconds = (datetime.fromisoformat(ended) - datetime.fromisoformat(current["started"])).total_seconds()
        prog = progress(s.data_dir, current) or {}
        last = {**current, "ended": ended, "ok": error is None, "error": error, "seconds": round(seconds),
                "stage": prog.get("stage"), "stats": prog.get("stats", {})}
        history = (_load(d / "run.json", {}).get("history") or [])[-(HISTORY_RUNS - 1):] + [last]
        _save(d / "run.json", {"current": None, "last": last, "history": history})
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def _run_active(d: Path) -> bool:
    try:
        with open(d / "run.lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(lock, fcntl.LOCK_UN)
            return False
    except BlockingIOError:
        return True


def _run_state(d: Path) -> tuple[dict | None, dict | None, list[dict]]:
    state = _load(d / "run.json", {})
    current, last, history = state.get("current"), state.get("last"), state.get("history") or []
    if current and not _run_active(d):
        # Prozess ist weg, ohne aufzuräumen (z. B. Container gestoppt)
        last = {**current, "ended": None, "ok": False, "seconds": None,
                "error": "Lauf wurde abgebrochen (Prozess beendet, z. B. durch Neustart)"}
        history = history + [last]
        current = None
    return current, last, history


def _with_progress(data_dir: Path, run: dict | None) -> dict | None:
    """Laufdaten plus Fortschritt aus dem Log; sonst die beim Ende gespeicherten Kennzahlen."""
    if not run:
        return None
    prog = progress(data_dir, run)
    if prog:
        return {**run, **prog}
    return {**run, "stats": run.get("stats") or {}, "detail": "", "wait": None}


_cache: dict[str, tuple[float, object]] = {}


def _jsonl_cached(path: Path, fn):
    """Ergebnis von fn(Zeilen) – neu berechnet nur, wenn sich die Datei geändert hat."""
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        return fn([])
    hit = _cache.get(str(path))
    if hit and hit[0] == mtime:
        return hit[1]
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    result = fn(rows)
    _cache[str(path)] = (mtime, result)
    return result


def _scores(rows: list[dict]) -> dict:
    """Score-Verteilung der letzten Tage (je Artikel der letzte Wert) und knapp Verpasste."""
    now = datetime.now(timezone.utc)
    since, near_since = (now - timedelta(days=SCORE_DAYS)).isoformat(), (now - timedelta(days=NEAR_MISS_DAYS)).isoformat()
    latest: dict[str, dict] = {}
    shown: set[str] = set()
    for r in rows:
        eid = str(r.get("entry_id"))
        if r.get("shown"):
            shown.add(eid)
        if r.get("ts", "") >= since and isinstance(r.get("score"), (int, float)):
            latest[eid] = r
    buckets = [{"score": n, "total": 0, "shown": 0} for n in range(11)]
    for eid, r in latest.items():
        b = buckets[max(0, min(10, round(r["score"])))]
        b["total"] += 1
        b["shown"] += eid in shown
    near = sorted((r for eid, r in latest.items() if eid not in shown and r["ts"] >= near_since),
                  key=lambda r: (-r["score"], r["ts"]))[:5]
    return {"buckets": buckets, "count": len(latest),
            "near_misses": [{k: r.get(k) for k in ("title", "url", "feed", "score")} for r in near]}


def _reactions(rows: list[dict]) -> dict:
    out: dict[str, set] = {"click": set(), "star": set()}
    for r in rows:
        if r.get("kind") in out:
            out[r["kind"]].add(str(r.get("entry_id")))
    return out


def snapshot(s: Settings) -> dict:
    """Alles, was die Status-Seite anzeigt – ohne Schlüssel oder Tokens."""
    state = _load(s.data_dir / "state.json", {})
    digests = _load(s.data_dir / "digests.json", [])
    latest = max(digests, key=lambda d: d.get("generated_at", ""), default=None)
    feed_file = s.data_dir / "feed.xml"
    current, last, history = _run_state(_dir(s.data_dir))
    scores = _jsonl_cached(s.data_dir / "history.jsonl", _scores)
    reactions = _jsonl_cached(s.data_dir / "feedback.jsonl", _reactions)
    return {
        "now": _now(),
        "running": _with_progress(s.data_dir, current),
        "last_run": _with_progress(s.data_dir, last),
        "history": [{k: r.get(k) for k in ("started", "ended", "seconds", "ok", "error", "source", "stats")}
                    for r in history[-14:]],
        "scores": {**scores, "days": SCORE_DAYS, "near_days": NEAR_MISS_DAYS,
                   "min_score": s.min_score, "floor_score": s.floor_score},
        "last_run_saved": state.get("last_run"),
        "next_run": next_run.isoformat() if next_run else None,
        "feed_updated": (datetime.fromtimestamp(feed_file.stat().st_mtime, timezone.utc).isoformat()
                         if feed_file.exists() else None),
        "digest_count": len(digests),
        "models": {"scoring": f"{s.scoring_provider}:{s.scoring_model}",
                   "summary": f"{s.summary_provider}:{s.summary_model}",
                   "prefilter": s.prefilter},
        "latest": latest and {
            "date": latest.get("date"),
            "generated_at": latest.get("generated_at"),
            "considered": latest.get("considered"),
            "items": [{**{k: it.get(k) for k in ("digest_title", "title", "url", "feed", "score",
                                                   "why", "topic", "explore", "filler", "similar_to")},
                       "clicked": str(it.get("entry_id")) in reactions["click"],
                       "starred": str(it.get("entry_id")) in reactions["star"]}
                      for it in latest.get("items", [])],
        },
    }


# ----------------------------------------------------------------------
# Dienste prüfen (auf Klick in der Status-Seite)
# ----------------------------------------------------------------------
HEALTH_CACHE_SECONDS = 30
_health: tuple[float, dict] | None = None


def _check_source(s: Settings) -> dict:
    import httpx
    from .sources import make_source
    src = make_source(s)
    url = s.greader_url if s.source == "greader" else s.miniflux_url
    try:
        return {"name": src.name, "state": "ok", "detail": f"angemeldet als {src.check()}"}
    except httpx.ConnectError:
        return {"name": src.name, "state": "err", "detail": f"{url} nicht erreichbar (URL, DNS oder Netz prüfen)"}
    except httpx.TimeoutException:
        return {"name": src.name, "state": "err", "detail": f"{url} antwortet nicht (Zeitüberschreitung)"}
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        why = "Zugangsdaten abgelehnt" if code in (401, 403) else "Fehler"
        return {"name": src.name, "state": "err", "detail": f"{why} (HTTP {code})"}


def _check_llm(stage: str, provider: str, model: str) -> dict:
    """Erreichbarkeit über die Modell-Liste – kostet keine Token und zählt nicht gegen Gratis-Kontingente
    wie eine echte Anfrage."""
    import httpx
    from .llm import LLM
    name = f"LLM {stage}"
    if provider == "none":
        return {"name": name, "state": "off", "detail": "nicht benutzt (Bewertung = Laya-Wert)"}
    try:
        llm = LLM(provider, model, timeout=8)
    except SystemExit as e:
        return {"name": name, "state": "err", "detail": str(e)}
    if provider == "anthropic":
        r = llm.http.get(f"{llm.base_url}/v1/models", params={"limit": 1000},
                         headers={"x-api-key": llm.api_key, "anthropic-version": "2023-06-01"})
    else:
        headers = {"Authorization": f"Bearer {llm.api_key}"} if llm.api_key else {}
        r = llm.http.get(f"{llm.base_url}/models", headers=headers)
    if r.status_code in (401, 403):
        return {"name": name, "state": "err", "detail": f"{llm}: Schlüssel abgelehnt (HTTP {r.status_code})"}
    if r.status_code == 429:
        return {"name": name, "state": "warn", "detail": f"{llm}: erreichbar, aber gerade Rate-Limit (HTTP 429)"}
    try:
        r.raise_for_status()
        ids = {m.get("id") for m in r.json().get("data", []) if isinstance(m, dict)}
    except (httpx.HTTPError, ValueError) as e:
        return {"name": name, "state": "err", "detail": f"{llm}: {e}"}
    if ids and model not in ids and model.removeprefix("models/") not in ids:
        return {"name": name, "state": "warn", "detail": f"{llm}: erreichbar, Modell aber nicht in der Liste des Anbieters"}
    return {"name": name, "state": "ok", "detail": f"{llm}: erreichbar, Modell vorhanden"}


def _check_local(s: Settings) -> list[dict]:
    from importlib.util import find_spec
    out = []
    if s.prefilter == "laya":
        ok = find_spec("transformers") is not None and (s.config_dir / "laya_questions.json").exists()
        out.append({"name": "Laya-Vorfilter", "state": "ok" if ok else "err",
                    "detail": f"installiert, Modell {s.laya_model}" if ok
                    else "fehlt (Image mit WITH_LAYA=1 bauen, laya_questions.json im config-Ordner?)"})
    else:
        out.append({"name": "Laya-Vorfilter", "state": "off", "detail": "ausgeschaltet (PREFILTER)"})
    if s.embeddings:
        ok = find_spec("fastembed") is not None
        out.append({"name": "Embeddings", "state": "ok" if ok else "err",
                    "detail": f"installiert, {s.embedding_model.split('/')[-1]}" if ok
                    else "fastembed fehlt (Image mit WITH_EMBEDDINGS=1 bauen)"})
    else:
        out.append({"name": "Embeddings", "state": "off", "detail": "ausgeschaltet (EMBEDDINGS)"})
    return out


def _check_files(s: Settings) -> list[dict]:
    import shutil
    out = []
    feed = s.data_dir / "feed.xml"
    if not feed.exists():
        out.append({"name": "Feed-Datei", "state": "warn", "detail": "noch nicht geschrieben"})
    else:
        age = datetime.now(timezone.utc).timestamp() - feed.stat().st_mtime
        hours = age / 3600
        out.append({"name": "Feed-Datei", "state": "ok" if hours < 26 else "warn",
                    "detail": f"zuletzt vor {hours:.0f} h geschrieben" if hours >= 1 else "in der letzten Stunde geschrieben"})
    free = shutil.disk_usage(s.data_dir).free / 1e9
    out.append({"name": "Speicherplatz", "state": "ok" if free > 0.5 else "warn" if free > 0.1 else "err",
                "detail": f"{free:.1f} GB frei im Datenordner"})
    since = datetime.now(timezone.utc).timestamp() - 86400
    errors = [ln for ln in _read_lines(s.data_dir, 0)[0] if ln.get("level") in ("ERROR", "CRITICAL") and ln["t"] >= since] \
        if _log_pos(s.data_dir)[0] else []
    out.append({"name": "Fehler im Log", "state": "ok" if not errors else "warn",
                "detail": "keine in den letzten 24 h" if not errors
                else f"{len(errors)} in den letzten 24 h, zuletzt: {errors[-1]['msg'].splitlines()[0][:160]}"})
    return out


def _timed(fn, *args) -> list[dict]:
    import time
    t0 = time.monotonic()
    try:
        res = fn(*args)
    except Exception as e:  # Prüfung selbst ist fehlgeschlagen – als Fehler des Dienstes zeigen
        name = {"_check_source": "Feedreader", "_check_local": "Lokale Modelle",
                "_check_files": "Dateien"}.get(fn.__name__, fn.__name__)
        res = {"name": name, "state": "err", "detail": f"{type(e).__name__}: {e}"}
    ms = round((time.monotonic() - t0) * 1000)
    rows = res if isinstance(res, list) else [res]
    if fn in (_check_source, _check_llm):
        for r in rows:
            r["ms"] = ms
    return rows


def health(s: Settings, force: bool = False) -> dict:
    """Zustand aller Dienste. Ergebnis wird kurz zwischengespeichert, damit die Seite nicht bei jedem
    Klick Feedreader und Anbieter abfragt."""
    global _health
    from concurrent.futures import ThreadPoolExecutor
    import time
    if _health and not force and time.monotonic() - _health[0] < HEALTH_CACHE_SECONDS:
        return _health[1]
    running = _run_active(_dir(s.data_dir))
    jobs = [(_check_source, s)]
    llms = [("Bewertung", s.scoring_provider, s.scoring_model), ("Auszüge", s.summary_provider, s.summary_model)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = [pool.submit(_timed, fn, *a) for fn, *a in jobs]
        if not running:
            futs += [pool.submit(_timed, _check_llm, *args) for args in llms]
        futs += [pool.submit(_timed, _check_local, s), pool.submit(_timed, _check_files, s)]
        rows = [r for f in futs for r in f.result()]
    if running:  # während eines Laufs keine Anfragen an den Anbieter – das Rate-Limit gehört dem Lauf
        rows[1:1] = [{"name": f"LLM {st}", "state": "skip", "detail": f"{p}:{m}: nicht geprüft, weil gerade ein Lauf läuft"}
                     for st, p, m in llms]
    rows.insert(0, {"name": "Zeitplan", "state": "ok" if next_run or running else "warn",
                    "detail": f"nächster Lauf {next_run.strftime('%d.%m. %H:%M')}" if next_run
                    else "Lauf aktiv" if running else "kein Lauf geplant"})
    result = {"checked": _now(), "services": rows}
    _health = (time.monotonic(), result)
    return result
