"""Einstiegspunkt.

  python -m app serve     Webserver + täglicher Lauf (Standard im Container)
  python -m app run       einmal ausführen und Feed aktualisieren
  python -m app dry-run   einmal ausführen, Ergebnis nur anzeigen (nichts speichern)
  python -m app check     Verbindung zu Feedreader und LLMs testen
  python -m app learn     zeigen, was aus Klicks und Sternen gelernt wurde
  python -m app laya-report  auswerten, welche Laya-Fragen etwas taugen
  python -m app opens-report [Tage]  Pixel-Messung auswerten (OPEN_PIXEL=log)
  python -m app laya-questions  Laya-Fragen aus interests.md erzeugen
  python -m app privacy-report [Tage]  was in den letzten Tagen an LLM-Anbieter ging
  python -m app profile-suggest  Vorschlag für interests.md aus deinen Klicks und Sternen
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys

import httpx
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

from . import audit, auth, feedback, notify, status
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
            digest = run(s)
        notify.digest_ready(s, digest)
        return True
    except status.RunBusy:
        log.warning("Lauf läuft bereits – übersprungen")
    except SystemExit as e:
        log.error("%s", e)
        notify.run_failed(s, e)
    except Exception as e:
        log.exception("Lauf fehlgeschlagen")
        notify.run_failed(s, e)
    finally:
        reset_rate_limits()
    return False


def _poll_stars(s: Settings) -> None:
    """Sterne und Angelesenes regelmäßig übernehmen, nicht nur beim täglichen Lauf – so zeigt die Status-Seite sie bald
    an. Während eines Laufs nicht: der übernimmt sie selbst."""
    while True:
        time.sleep(s.star_poll_minutes * 60)
        if status.run_active(s.data_dir):
            continue
        try:
            src = make_source(s)
            feedback.collect_stars(s.data_dir, src.starred(), quiet=True)
            feedback.collect_reader_reads(s, src, quiet=True)
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


# 1×1 transparentes GIF
# Berichte, die die Status-Seite anzeigen kann: Name in der URL -> Befehl. Feste Liste, keine Eingaben
# aus der Anfrage landen in der Kommandozeile.
REPORTS = {"learn": ["learn"], "laya": ["laya-report"], "privacy": ["privacy-report"], "opens": ["opens-report"],
           "profile": ["profile-suggest"]}
_report_lock = threading.Lock()


def _report_text(name: str) -> tuple[int, str]:
    """Bericht in eigenem Prozess erzeugen – so stört er weder Webserver noch einen laufenden Digest."""
    if not _report_lock.acquire(blocking=False):
        return 429, "Es wird gerade schon ein Bericht erstellt – bitte gleich noch einmal."
    try:
        r = subprocess.run([sys.executable, "-m", "app", *REPORTS[name]], capture_output=True, text=True,
                           timeout=180, cwd=str(Path(__file__).resolve().parent.parent))
        if r.returncode != 0:
            return 500, (r.stdout + "\n" + r.stderr[-2000:]).strip()
        return 200, r.stdout.strip() or "(keine Ausgabe)"
    except subprocess.TimeoutExpired:
        return 504, "Der Bericht hat länger als 3 Minuten gebraucht und wurde abgebrochen."
    finally:
        _report_lock.release()


PIXEL = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\x00\x00\x00!\xf9\x04\x01\x00\x00\x00\x00"
         b",\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;")


def _make_handler(s: Settings):
    feed_file = s.data_dir / "feed.xml"
    prefix = f"/{s.feed_token}" if s.feed_token else ""
    redirect_re = re.compile(rf"^{re.escape(prefix)}/r/([A-Za-z0-9_-]{{1,64}})$")
    status_path = f"{prefix}/status"
    # Cookie-Pfad: der geheime Ordner. "/<token>/status" allein würde /<token>/status.json nicht abdecken –
    # Browser schicken ein Cookie nur für den Pfad selbst und für das, was mit "/" darunter liegt.
    cookie_path = f"{prefix}/"
    vote_re = re.compile(rf"^{re.escape(prefix)}/v/([A-Za-z0-9_-]{{1,64}})/(up|down)$")
    topic_re = re.compile(rf"^{re.escape(prefix)}/topic/([a-z0-9-]{{1,64}})\.xml$")
    pixel_re = re.compile(rf"^{re.escape(prefix)}/p/([A-Za-z0-9_-]{{1,64}})\.gif$")
    last_feed_fetch = [None]  # Zeitpunkt des letzten Feed-Abrufs, für die Pixel-Messung

    class Handler(BaseHTTPRequestHandler):
        server_version = "feed-digestive"

        def do_GET(self):  # noqa: N802
            path, _, query = self.path.partition("?")
            if path == "/healthz":
                return self._send(200, b"ok", "text/plain")
            if path == s.feed_path and feed_file.exists():
                last_feed_fetch[0] = time.monotonic()
                return self._send(200, feed_file.read_bytes(), "application/atom+xml; charset=utf-8")
            m = topic_re.match(path)
            if m:  # Themenfeed – der Name kann nur Kleinbuchstaben, Ziffern und "-" enthalten
                topic_file = s.data_dir / "topics" / f"{m.group(1)}.xml"
                if topic_file.exists():
                    return self._send(200, topic_file.read_bytes(), "application/atom+xml; charset=utf-8")
                return self._send(404, b"unbekanntes Thema", "text/plain; charset=utf-8")
            # Status-Seite liegt wie der Feed hinter dem geheimen Pfad – und mit STATUS_PASSWORD hinter einer Anmeldung
            if path == status_path or path.startswith((status_path + "/", status_path + ".")):
                if path == status_path + "/logout":
                    return self._logout()
                if not self._signed_in():
                    if path in (status_path, status_path + "/", status_path + "/login"):
                        return self._send(200, auth.login_page(status_path + "/login"), "text/html; charset=utf-8",
                                          cache=False)
                    return self._send(401, b"nicht angemeldet", "text/plain; charset=utf-8", cache=False)
                if path == status_path + "/login":
                    return self._see_other(status_path)
            if path in (status_path, status_path + "/"):
                return self._send(200, status.PAGE.read_bytes(), "text/html; charset=utf-8", cache=False)
            if path == status_path + ".json":
                return self._json(status.snapshot(s))
            if path == status_path + "/health":
                return self._json(status.health(s, force="force=1" in query))
            if path.startswith(status_path + "/report/") and self.command == "GET":
                name = path.removeprefix(status_path + "/report/")
                if name in REPORTS:
                    code, text = _report_text(name)
                    return self._send(code, text.encode(), "text/plain; charset=utf-8", cache=False)
            if path == status_path + "/logs":
                q = dict(re.findall(r"(?:^|&)(after|gen)=(\d+)", query))
                return self._json(status.read_log(s.data_dir, int(q.get("after", 0)), int(q.get("gen", 0))))
            if vote_re.match(path):  # Links aus alten Digests, die der Reader noch gespeichert hat
                return self._send(410, "👍/👎 gibt es nicht mehr. Was zählt: „Zum Artikel“ antippen "
                                       "oder im Reader einen Stern setzen.".encode(), "text/plain; charset=utf-8")
            m = pixel_re.match(path)
            if m:
                if self.command == "GET" and s.open_pixel == "log" and feedback.lookup_url(s.data_dir, m.group(1)):
                    since = time.monotonic() - last_feed_fetch[0] if last_feed_fetch[0] else None
                    feedback.record_open(s.data_dir, m.group(1), self.headers.get("User-Agent"), since)
                return self._send(200, PIXEL, "image/gif", cache=False)
            m = redirect_re.match(path)
            if m:
                entry_id = m.group(1)
                target = feedback.lookup_url(s.data_dir, entry_id)
                if not target:
                    return self._send(404, b"unbekannter Artikel", "text/plain; charset=utf-8")
                if self.command == "GET" and feedback.is_human(self.headers.get("User-Agent"), self.headers):
                    feedback.record_click(s.data_dir, entry_id, day_only=s.privacy == "strict")
                return self._redirect(target)
            return self._send(404, b"not found", "text/plain")

        do_HEAD = do_GET

        def do_POST(self):  # noqa: N802 – nur die Anmeldung
            path = self.path.partition("?")[0]
            if not s.status_password or path != status_path + "/login":
                return self._send(404, b"not found", "text/plain")
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if not 0 < length <= 4096:
                return self._send(400, b"bad request", "text/plain")
            form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"), keep_blank_values=True)
            if auth.locked():
                return self._send(429, auth.login_page(status_path + "/login",
                                  "Zu viele Fehlversuche. Bitte in zehn Minuten noch einmal."),
                                  "text/html; charset=utf-8", cache=False)
            if not auth.check_password((form.get("password") or [""])[0], s.status_password):
                time.sleep(1)  # bremst Durchprobieren
                log.warning("Status-Seite: Anmeldung mit falschem Passwort")
                return self._send(401, auth.login_page(status_path + "/login", "Das Passwort stimmt nicht."),
                                  "text/html; charset=utf-8", cache=False)
            cookie = auth.set_cookie_header(auth.make_cookie(s.status_password, s.feed_token), cookie_path,
                                            secure=s.public_url.startswith("https://"))
            return self._see_other(status_path, cookie)

        def _signed_in(self) -> bool:
            return not s.status_password or auth.valid_cookie(
                auth.cookie_from_header(self.headers.get("Cookie")), s.status_password, s.feed_token)

        def _logout(self):
            return self._see_other(status_path, auth.set_cookie_header(
                "", cookie_path, secure=s.public_url.startswith("https://"), max_age=0))

        def _see_other(self, target, cookie=None):
            self.send_response(303)
            self.send_header("Location", target)
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()

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
    if s.preset:
        log.info("Profil PRESET=%s: Vorfilter %s, Embeddings %s", s.preset, s.prefilter, "an" if s.embeddings else "aus")
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
    if s.preset:
        print(f"Profil PRESET={s.preset}: Vorfilter {s.prefilter}, Embeddings {'an' if s.embeddings else 'aus'}")
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
        try:
            flt.score([probe])
        except Exception as e:  # sonst endet check ohne Erklärung
            raise SystemExit(f"✗ Laya ({s.laya_model}): {type(e).__name__}: {e}")
        print(f"✓ Laya ({s.laya_model}): Testartikel bekommt {probe.get('prefilter')}/10 – "
              f"{probe.get('prefilter_why', '')}", flush=True)
        # Laya-Modell (1–2,5 GB) freigeben, bevor Embeddings und LLM-Test laufen – sonst droht auf
        # kleinen Servern der OOM-Killer, und check bricht kommentarlos ab
        del flt
        import gc
        gc.collect()
    if s.prefilter == "embeddings":
        from .embed_filter import EmbedFilter
        flt = EmbedFilter(s)
        probe = {"id": "check-probe", "feed": "Test",
                 "title": "New open-weight language model beats GPT on coding benchmarks",
                 "text": "Researchers released a new open-weight LLM that outperforms larger models on coding tasks."}
        flt.score([probe])
        print(f"✓ Embedding-Vorfilter ({len(flt.spec['topics'])} Themen, {len(flt.positives)} Favoriten): "
              f"Testartikel bekommt {probe['prefilter']}/10 – {probe['prefilter_why']}")
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
            # großzügig, sonst verbraucht ein "denkendes" Modell alles vorher und die Antwort bleibt leer
            answer = llm.complete("Antworte knapp.", "Sag nur: ok", max_tokens=400, purpose="Test")
        except LLMError as e:
            raise SystemExit(f"✗ {stage}: {e}")
        if not answer.strip():
            print(f"⚠ {stage}: {llm} antwortet leer – vermutlich ein Modell, das vor der Antwort nachdenkt und den "
                  "Denkmodus nicht abschalten lässt. Das kostet Ausgabe-Token und kann Antworten abschneiden.")
        else:
            print(f"✓ {stage}: {llm} antwortet: {answer.strip()[:40]!r}")
    for name, ok in notify.send(s, "feed-digestive: Test", "Die Benachrichtigung funktioniert.").items():
        print(f"✓ {name}: Testnachricht gesendet" if ok
              else f"✗ {name}: Testnachricht nicht zugestellt (siehe Meldung oben)")
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
        if s.prefilter == "embeddings":
            from .embed_filter import parse_interests
            imd = s.config_dir / "interests.md"
            topics, _ = parse_interests(imd.read_text(encoding="utf-8") if imd.exists() else "")
            labels = {f"topic_{k}": "Thema " + v["label"] for k, v in topics.items()}
            labels.update({"like": "Ähnlich zu deinen Favoriten", "dislike": "Ähnlich zu Abgelehntem"})
        print(describe(s.data_dir, s.feedback_days, labels, s.signal_half_life_days))
        from .learn import describe_llm
        print()
        print(describe_llm(s.data_dir, labels))
    elif cmd == "laya-questions":
        from .laya_questions import run as make_questions
        make_questions(s)
    elif cmd == "opens-report":
        days = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 14
        print(feedback.opens_report(s.data_dir, days))
    elif cmd == "privacy-report":
        days = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 30
        print(audit.report(s.data_dir, days))
    elif cmd == "profile-suggest":
        from .profile_suggest import suggest
        print(suggest(s))
    elif cmd == "laya-report":
        from .laya_report import report
        print(report(s.data_dir, s.config_dir))
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
