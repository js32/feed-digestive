"""Einstiegspunkt.

  python -m app serve     Webserver + täglicher Lauf (Standard im Container)
  python -m app run       einmal ausführen und Feed aktualisieren
  python -m app dry-run   einmal ausführen, Ergebnis nur anzeigen (nichts speichern)
  python -m app check     Verbindung zu Miniflux und LLMs testen
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

from .config import Settings
from .digest import run
from .feed import write_feed
from .llm import LLM
from .miniflux import Miniflux

log = logging.getLogger("rss-digest")
_lock = threading.Lock()


def _run_safely(s: Settings) -> None:
    if not _lock.acquire(blocking=False):
        log.warning("Lauf läuft bereits – übersprungen")
        return
    try:
        run(s)
    except SystemExit as e:
        log.error("%s", e)
    except Exception:
        log.exception("Lauf fehlgeschlagen")
    finally:
        _lock.release()


def _next_run(s: Settings) -> datetime:
    tz = ZoneInfo(s.tz)
    now = datetime.now(tz)
    hh, mm = (int(x) for x in s.digest_time.split(":"))
    nxt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if nxt <= now:
        nxt = (nxt + timedelta(days=1)).replace(hour=hh, minute=mm)
    return nxt


def _make_handler(s: Settings):
    feed_file = s.data_dir / "feed.xml"

    class Handler(BaseHTTPRequestHandler):
        server_version = "rss-digest"

        def do_GET(self):  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path == "/healthz":
                return self._send(200, b"ok", "text/plain")
            if path == s.feed_path and feed_file.exists():
                return self._send(200, feed_file.read_bytes(), "application/atom+xml; charset=utf-8")
            return self._send(404, b"not found", "text/plain")

        do_HEAD = do_GET

        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=300")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def log_message(self, fmt, *args):
            log.debug("http %s", fmt % args)

    return Handler


def serve(s: Settings) -> None:
    if not (s.data_dir / "feed.xml").exists():
        write_feed(s, [])  # leerer, aber gültiger Feed zum Abonnieren
    httpd = ThreadingHTTPServer(("0.0.0.0", s.http_port), _make_handler(s))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    log.info("Feed erreichbar unter %s (intern Port %d)", s.feed_url, s.http_port)
    if s.run_on_start:
        _run_safely(s)
    while True:
        nxt = _next_run(s)
        log.info("Nächster Lauf: %s", nxt.strftime("%d.%m.%Y %H:%M %Z"))
        while (remaining := (nxt - datetime.now(nxt.tzinfo)).total_seconds()) > 0:
            time.sleep(min(remaining, 300))
        _run_safely(s)


def check(s: Settings) -> None:
    me = Miniflux(s.miniflux_url, s.miniflux_token).me()
    print(f"✓ Miniflux: angemeldet als {me.get('username')}")
    for stage, prov, model in (("Bewertung", s.scoring_provider, s.scoring_model),
                               ("Auszüge", s.summary_provider, s.summary_model)):
        llm = LLM(prov, model)
        answer = llm.complete("Antworte knapp.", "Sag nur: ok", max_tokens=20)
        print(f"✓ {stage}: {llm} antwortet: {answer.strip()[:40]!r}")
    print(f"Feed-URL: {s.feed_url}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    s = Settings.from_env()
    if cmd == "serve":
        serve(s)
    elif cmd == "run":
        run(s)
    elif cmd == "dry-run":
        run(s, dry_run=True)
    elif cmd == "check":
        check(s)
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
