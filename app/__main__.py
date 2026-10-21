"""Einstiegspunkt.

  python -m app serve     Webserver + täglicher Lauf (Standard im Container)
  python -m app run       einmal ausführen und Feed aktualisieren
  python -m app dry-run   einmal ausführen, Ergebnis nur anzeigen (nichts speichern)
  python -m app check     Verbindung zu Feedreader und LLMs testen
  python -m app learn     zeigen, was aus Klicks und Sternen gelernt wurde
  python -m app laya-report  auswerten, welche Laya-Fragen etwas taugen
  python -m app privacy-report [Tage]  was in den letzten Tagen an LLM-Anbieter ging
"""
from __future__ import annotations

import json
import logging
import re
import sys

import httpx
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

from . import audit, feedback, status
from .config import Settings
from .digest import run
from .feed import write_feed
from .llm import LLM, LLMError, reset_rate_limits
from .sources import make_source

log = logging.getLogger("feed-digestive")


def _run_safely(s: Settings, source: str = "Zeitplan") -> bool:
    """Ein Lauf mit Sperre und Eintrag für die Status-Seite – auch für `run` aus der Shell."""
    try:
        with status.tracked(s, source):
            run(s)
        return True
    except status.RunBusy:
        log.warning("Lauf läuft bereits – übersprungen")
    except SystemExit as e:
        log.error("%s", e)
    except Exception:
        log.exception("Lauf fehlgeschlagen")
    finally:
        reset_rate_limits()
    return False


def _poll_stars(s: Settings) -> None:
    """Sterne regelmäßig übernehmen, nicht nur beim täglichen Lauf – so zeigt die Status-Seite sie bald
    an. Während eines Laufs nicht: der übernimmt sie selbst."""
    while True:
        time.sleep(s.star_poll_minutes * 60)
        if status.run_active(s.data_dir):
            continue
        try:
            feedback.collect_stars(s.data_dir, make_source(s).starred(), quiet=True)
        except Exception as e:
            log.warning("Sterne-Abgleich fehlgeschlagen: %s", e)


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
    prefix = f"/{s.feed_token}" if s.feed_token else ""
    redirect_re = re.compile(rf"^{re.escape(prefix)}/r/([A-Za-z0-9_-]{{1,64}})$")
    status_path = f"{prefix}/status"

    class Handler(BaseHTTPRequestHandler):
        server_version = "feed-digestive"

        def do_GET(self):  # noqa: N802
            path, _, query = self.path.partition("?")
            if path == "/healthz":
                return self._send(200, b"ok", "text/plain")
            if path == s.feed_path and feed_file.exists():
                return self._send(200, feed_file.read_bytes(), "application/atom+xml; charset=utf-8")
            # Status-Seite liegt wie der Feed hinter dem geheimen Pfad
            if path in (status_path, status_path + "/"):
                return self._send(200, status.PAGE.read_bytes(), "text/html; charset=utf-8", cache=False)
            if path == status_path + ".json":
                return self._json(status.snapshot(s))
            if path == status_path + "/health":
                return self._json(status.health(s, force="force=1" in query))
            if path == status_path + "/logs":
                q = dict(re.findall(r"(?:^|&)(after|gen)=(\d+)", query))
                return self._json(status.read_log(s.data_dir, int(q.get("after", 0)), int(q.get("gen", 0))))
            m = redirect_re.match(path)
            if m:
                entry_id = m.group(1)
                target = feedback.lookup_url(s.data_dir, entry_id)
                if not target:
                    return self._send(404, b"unbekannter Artikel", "text/plain; charset=utf-8")
                if self.command == "GET" and feedback.is_human(self.headers.get("User-Agent")):
                    feedback.record_click(s.data_dir, entry_id, day_only=s.privacy == "strict")
                return self._redirect(target)
            return self._send(404, b"not found", "text/plain")

        do_HEAD = do_GET

        def _redirect(self, target):
            self.send_response(302)
            self.send_header("Location", target)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")  # Ziel sieht den geheimen Pfad nicht
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _json(self, data):
            body = json.dumps(data, ensure_ascii=False).encode()
            return self._send(200, body, "application/json; charset=utf-8", cache=False)

        def _send(self, code, body, ctype, cache=True):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=300" if cache else "no-store")
            if not cache:
                self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def log_message(self, fmt, *args):
            log.debug("http %s", fmt % args)

    return Handler


def serve(s: Settings) -> None:
    # Feed beim Start aus den gespeicherten Digests neu schreiben – so greifen Änderungen
    # an FEED_MODE, Layout oder Titel sofort, nicht erst nach dem nächsten Lauf
    try:
        digests = json.loads((s.data_dir / "digests.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        digests = []
    write_feed(s, digests)
    status.rotate_log(s.data_dir)
    httpd = ThreadingHTTPServer(("0.0.0.0", s.http_port), _make_handler(s))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    log.info("Feed erreichbar unter %s (intern Port %d)", s.feed_url, s.http_port)
    log.info("Status-Seite: %s/status", s.feed_base)
    if s.star_poll_minutes > 0 and (s.use_starred or s.learn):
        threading.Thread(target=_poll_stars, args=(s,), daemon=True).start()
    if s.run_on_start:
        _run_safely(s)
    while True:
        nxt = _next_run(s)
        status.next_run = nxt
        log.info("Nächster Lauf: %s", nxt.strftime("%d.%m.%Y %H:%M %Z"))
        while (remaining := (nxt - datetime.now(nxt.tzinfo)).total_seconds()) > 0:
            time.sleep(min(remaining, 300))
        status.next_run = None
        _run_safely(s)


def check(s: Settings) -> None:
    src = make_source(s)
    target = s.greader_url if s.source == "greader" else s.miniflux_url
    try:
        print(f"✓ {src.name}: angemeldet als {src.check()}")
    except httpx.ConnectError as e:
        raise SystemExit(f"✗ {src.name}: {target} nicht erreichbar ({e}).\n"
                         "  Stimmt die URL in der .env? Löst der Container den Namen auf (DNS)?")
    except httpx.HTTPError as e:
        raise SystemExit(f"✗ {src.name}: Fehler bei {target}: {e}")
    if s.prefilter == "laya":
        from .laya_filter import LayaFilter
        flt = LayaFilter(s.config_dir / "laya_questions.json", model=s.laya_model,
                         max_len=s.laya_max_len, batch_size=s.laya_batch_size)
        probe = {"feed": "Test", "title": "New open-weight language model beats GPT on coding benchmarks",
                 "text": "Researchers released a new open-weight LLM that outperforms larger models on coding tasks."}
        flt.score([probe])
        print(f"✓ Laya ({s.laya_model}): Testartikel bekommt {probe['prefilter']}/10 – {probe['prefilter_why']}")
    if s.embeddings:
        try:
            from .embed import Embedder, similarity
        except ImportError:
            raise SystemExit("✗ Embeddings: fastembed fehlt – Image mit WITH_EMBEDDINGS=1 bauen")
        emb = Embedder(s.embedding_model, s.data_dir)
        a = {"id": "check-a", "title": "AI error almost triggered a military strike", "text": ""}
        b = {"id": "check-b", "title": "KI-Fehler hätte fast einen Militärschlag ausgelöst", "text": ""}
        emb.embed([a, b])
        print(f"✓ Embeddings ({emb}): englische und deutsche Meldung {similarity(a, b):.2f} ähnlich "
              f"(gleiche Geschichte ab DEDUP_SIMILARITY={s.dedup_similarity})")
    for stage, prov, model in (("Bewertung", s.scoring_provider, s.scoring_model),
                               ("Auszüge", s.summary_provider, s.summary_model)):
        if prov == "none":
            continue
        llm = LLM(prov, model)
        try:
            answer = llm.complete("Antworte knapp.", "Sag nur: ok", max_tokens=20, purpose="Test")
        except LLMError as e:
            raise SystemExit(f"✗ {stage}: {e}")
        print(f"✓ {stage}: {llm} antwortet: {answer.strip()[:40]!r}")
    print(f"Feed-URL: {s.feed_url}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    s = Settings.from_env()
    logging.getLogger().addHandler(status.FileLog(s.data_dir))
    audit.configure(s.data_dir)
    if cmd == "serve":
        serve(s)
    elif cmd == "run":
        if not _run_safely(s, "Befehl"):
            sys.exit(1)
    elif cmd == "dry-run":
        run(s, dry_run=True)
    elif cmd == "check":
        check(s)
    elif cmd == "learn":
        from .learn import describe
        labels = {}
        qfile = s.config_dir / "laya_questions.json"
        if qfile.exists():
            spec = json.loads(qfile.read_text(encoding="utf-8"))
            labels = {f"topic_{k}": "Thema " + v.get("label", k) for k, v in spec.get("topics", {}).items()}
            labels["depth"] = "Tiefe"
            labels.update({f"penalty_{k}": "Malus " + v.get("label", k) for k, v in spec.get("penalties", {}).items()})
        print(describe(s.data_dir, s.feedback_days, labels))
    elif cmd == "privacy-report":
        days = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 30
        print(audit.report(s.data_dir, days))
    elif cmd == "laya-report":
        from .laya_report import report
        print(report(s.data_dir, s.config_dir))
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
