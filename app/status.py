"""Status-Seite: Log-Puffer im Speicher, Laufzustand und Daten für /status."""
from __future__ import annotations

import json
import logging
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings

LOG_LINES = 3000  # so viele Zeilen hält der Puffer, ältere fallen raus
PAGE = Path(__file__).with_name("status.html")


class LogBuffer(logging.Handler):
    """Hält die letzten Log-Zeilen dieses Prozesses mit fortlaufender Nummer."""

    def __init__(self, maxlen: int = LOG_LINES):
        super().__init__(level=logging.INFO)
        self.lines: deque[dict] = deque(maxlen=maxlen)
        self.seq = 0
        self._mu = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
            if record.exc_info:
                msg += "\n" + logging.Formatter().formatException(record.exc_info)
        except Exception:
            msg = str(record.msg)
        with self._mu:
            self.seq += 1
            self.lines.append({"n": self.seq, "t": record.created, "level": record.levelname, "msg": msg})

    def since(self, after: int) -> list[dict]:
        with self._mu:
            return [ln for ln in self.lines if ln["n"] > after]


class RunTracker:
    """Merkt sich, ob gerade ein Lauf läuft und wie der letzte ausging."""

    def __init__(self, logs: LogBuffer):
        self.logs = logs
        self.current: dict | None = None
        self.last: dict | None = None
        self.next_run: datetime | None = None

    def start(self) -> None:
        self.current = {"started": _now(), "first_log": self.logs.seq + 1}

    def finish(self, error: str | None = None) -> None:
        run, self.current = self.current or {"started": _now(), "first_log": 0}, None
        ended = _now()
        self.last = {**run, "ended": ended, "ok": error is None, "error": error,
                     "seconds": round((datetime.fromisoformat(ended)
                                       - datetime.fromisoformat(run["started"])).total_seconds())}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def snapshot(s: Settings, runs: RunTracker) -> dict:
    """Alles, was die Status-Seite anzeigt – ohne Schlüssel oder Tokens."""
    state = _load(s.data_dir / "state.json", {})
    digests = _load(s.data_dir / "digests.json", [])
    latest = max(digests, key=lambda d: d.get("generated_at", ""), default=None)
    feed_file = s.data_dir / "feed.xml"
    return {
        "now": _now(),
        "running": runs.current,
        "last_run": runs.last,
        "last_run_saved": state.get("last_run"),  # auch Läufe vor dem letzten Neustart
        "next_run": runs.next_run.isoformat() if runs.next_run else None,
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
