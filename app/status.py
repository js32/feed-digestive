"""Status-Seite: Logs und Laufzustand als Dateien in data/status/ – so sieht die Seite auch
Läufe, die per `docker compose exec … run` in einem eigenen Prozess gestartet wurden."""
from __future__ import annotations

import fcntl
import json
import logging
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings

LOG_MAX_BYTES = 2_000_000  # danach wird log.jsonl zu log.1.jsonl
TAIL_LINES = 3000          # so viele Zeilen bekommt die Seite beim ersten Abruf
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


def read_log(data_dir: Path, after: int, gen: int) -> dict:
    """Zeilen ab Byte-Position after. Jede Zeile bekommt ihre Position als fortlaufende Nummer n."""
    cur_gen, size = _log_pos(data_dir)
    if not cur_gen:
        return {"lines": [], "seq": 0, "gen": 0}
    if gen != cur_gen or after > size:
        after = 0
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
    return {"lines": lines[-TAIL_LINES:], "seq": after + len(data), "gen": cur_gen}


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
        _save(d / "run.json", {"current": None, "last": {
            **current, "ended": ended, "ok": error is None, "error": error, "seconds": round(seconds)}})
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


def _run_state(d: Path) -> tuple[dict | None, dict | None]:
    state = _load(d / "run.json", {})
    current, last = state.get("current"), state.get("last")
    if current and not _run_active(d):
        # Prozess ist weg, ohne aufzuräumen (z. B. Container gestoppt)
        last = {**current, "ended": None, "ok": False, "seconds": None,
                "error": "Lauf wurde abgebrochen (Prozess beendet, z. B. durch Neustart)"}
        current = None
    return current, last


def snapshot(s: Settings) -> dict:
    """Alles, was die Status-Seite anzeigt – ohne Schlüssel oder Tokens."""
    state = _load(s.data_dir / "state.json", {})
    digests = _load(s.data_dir / "digests.json", [])
    latest = max(digests, key=lambda d: d.get("generated_at", ""), default=None)
    feed_file = s.data_dir / "feed.xml"
    current, last = _run_state(_dir(s.data_dir))
    return {
        "now": _now(),
        "running": current,
        "last_run": last,
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
            "items": [{k: it.get(k) for k in ("digest_title", "title", "url", "feed", "score",
                                                "why", "topic", "explore", "filler", "similar_to")}
                      for it in latest.get("items", [])],
        },
    }
