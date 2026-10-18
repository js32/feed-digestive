"""Quellen: woher die Artikel kommen.

Jede Quelle liefert Einträge in einem einheitlichen Format:

    {"id": str, "title": str, "url": str, "content": html, "feed_title": str,
     "feed_url": str, "feed_id": str, "author": str, "published_at": ISO-Zeit,
     "reading_time": Minuten}

Unterstützt:
  miniflux  – Miniflux-REST-API (API-Token)
  greader   – Google-Reader-API: FreshRSS, Inoreader, The Old Reader, BazQux,
              Miniflux, Tiny Tiny RSS (Plugin), Nextcloud News (Plugin) u. a.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx

from .miniflux import Miniflux, html_to_text

log = logging.getLogger(__name__)

SEEN_KEEP = 5000  # so viele zuletzt gesehene IDs merken (gegen Doppelte bei Zeitfenstern)


def _reading_time(html: str) -> int:
    words = len(html_to_text(html).split())
    return max(1, round(words / 220)) if words > 80 else 0


class Source:
    name = "?"

    def check(self) -> str:
        """Login testen, gibt den Benutzernamen zurück."""
        raise NotImplementedError

    def fetch_new(self, state: dict, window_start: datetime) -> tuple[list[dict], dict]:
        """Neue Einträge seit dem letzten Lauf + aktualisierter Quellen-Zustand."""
        raise NotImplementedError

    def starred(self, limit: int = 300) -> list[dict]:
        """Mit Stern markierte Einträge (normalisiert)."""
        raise NotImplementedError

    def fetch_unread(self, limit: int) -> list[dict]:
        """Alle ungelesenen Einträge, neueste zuerst, höchstens limit."""
        raise NotImplementedError


# ----------------------------------------------------------------------
# Miniflux (native API)
# ----------------------------------------------------------------------
class MinifluxSource(Source):
    name = "Miniflux"

    def __init__(self, s):
        self.s = s
        self.mf = Miniflux(s.miniflux_url, s.miniflux_token)

    @staticmethod
    def _norm(e: dict) -> dict:
        feed = e.get("feed") or {}
        return {
            "id": str(e["id"]), "title": e.get("title") or "", "url": e.get("url") or "",
            "content": e.get("content") or "", "feed_title": feed.get("title") or "",
            "feed_url": feed.get("feed_url") or "", "feed_id": str(e.get("feed_id") or feed.get("id") or ""),
            "author": e.get("author") or "", "published_at": e.get("published_at") or e.get("created_at"),
            "reading_time": e.get("reading_time") or 0,
        }

    def check(self) -> str:
        return self.mf.me().get("username", "?")

    def fetch_new(self, state, window_start):
        cats = [int(x) for x in self.s.category_ids if str(x).isdigit()]
        raw = self.mf.entries_since(
            after_entry_id=state.get("last_entry_id"),
            after_ts=int(window_start.timestamp()),
            max_entries=self.s.max_entries,
            only_unread=self.s.only_unread,
            category_ids=cats,
        )
        last = max([e["id"] for e in raw], default=state.get("last_entry_id") or 0)
        return [self._norm(e) for e in raw], {"last_entry_id": last}

    def fetch_unread(self, limit):
        out = []
        cats = [int(x) for x in self.s.category_ids if str(x).isdigit()] or [None]
        for cat in cats:
            offset = 0
            while len(out) < limit:
                data = self.mf._get("/entries", status="unread", order="published_at", direction="desc",
                                    limit=min(100, limit - len(out)), offset=offset, category_id=cat)
                batch = data.get("entries") or []
                out.extend(batch)
                offset += len(batch)
                if len(batch) < 100:
                    break
        return [self._norm(e) for e in out[:limit]]

    def starred(self, limit=300):
        try:
            data = self.mf._get("/entries", starred="true", order="id", direction="desc", limit=limit)
        except httpx.HTTPError as e:
            log.warning("Stern-Einträge nicht abrufbar: %s", e)
            return []
        return [self._norm(e) for e in data.get("entries") or []]


# ----------------------------------------------------------------------
# Google Reader API
# ----------------------------------------------------------------------
READING_LIST = "user/-/state/com.google/reading-list"
STARRED = "user/-/state/com.google/starred"
READ = "user/-/state/com.google/read"


def short_id(item_id: str) -> str:
    """'tag:google.com,2005:reader/item/000000000000001f' -> '000000000000001f'.

    Dezimale Kurzform (manche Server) wird in dieselbe 16-stellige Hex-Form gebracht,
    damit Lese-Liste und Stern-Liste dieselbe ID liefern.
    """
    item_id = (item_id or "").strip()
    if "item/" in item_id:                       # Langform: Hex nach "item/"
        return item_id.rsplit("item/", 1)[1].lower()
    if re.fullmatch(r"-?\d+", item_id):          # Kurzform: vorzeichenbehaftete Dezimalzahl
        return f"{int(item_id) & 0xFFFFFFFFFFFFFFFF:016x}"
    return re.sub(r"[^A-Za-z0-9_-]", "_", item_id)[-64:]


class GReaderSource(Source):
    name = "Google Reader API"

    def __init__(self, s):
        self.s = s
        self.base = s.greader_url.rstrip("/")
        headers = {"User-Agent": "feed-digestive/1.0"}
        # Inoreader verlangt zusätzlich App-ID/Key (Entwickler-Konto)
        if s.greader_app_id:
            headers["AppId"] = s.greader_app_id
            headers["AppKey"] = s.greader_app_key
        self.http = httpx.Client(timeout=60, headers=headers, follow_redirects=True)
        self._auth = None

    # --- Login --------------------------------------------------------
    def _login(self) -> None:
        r = self.http.post(f"{self.base}/accounts/ClientLogin",
                           data={"Email": self.s.greader_user, "Passwd": self.s.greader_password})
        if r.status_code in (401, 403):
            raise SystemExit("Google-Reader-API: Login abgelehnt – Benutzer/API-Passwort prüfen "
                             "(FreshRSS: eigenes API-Passwort im Profil setzen)")
        r.raise_for_status()
        token = next((l.split("=", 1)[1] for l in r.text.splitlines() if l.startswith("Auth=")), None)
        if not token:
            raise SystemExit(f"Google-Reader-API: kein Auth-Token in der Antwort von {self.base}")
        self._auth = token
        self.http.headers["Authorization"] = f"GoogleLogin auth={token}"

    def _get(self, path: str, **params):
        if not self._auth:
            self._login()
        params = {k: v for k, v in params.items() if v is not None}
        r = self.http.get(f"{self.base}/reader/api/0/{path}", params=params)
        if r.status_code == 401:  # Token abgelaufen -> einmal neu anmelden
            self._login()
            r = self.http.get(f"{self.base}/reader/api/0/{path}", params=params)
        r.raise_for_status()
        return r.json()

    # --- Einträge -----------------------------------------------------
    @staticmethod
    def _norm(it: dict) -> dict:
        link = next((l.get("href") for l in it.get("canonical") or [] if l.get("href")), None) \
            or next((l.get("href") for l in it.get("alternate") or [] if l.get("href")), "")
        content = (it.get("content") or {}).get("content") or (it.get("summary") or {}).get("content") or ""
        origin = it.get("origin") or {}
        ts = it.get("published") or (int(it.get("crawlTimeMsec", 0)) // 1000) or None
        pub = datetime.fromtimestamp(int(ts), timezone.utc).isoformat() if ts else None
        return {
            "id": short_id(str(it.get("id", ""))), "title": it.get("title") or "", "url": link or "",
            "content": content, "feed_title": origin.get("title") or "",
            "feed_url": (origin.get("streamId") or "").removeprefix("feed/"),
            "feed_id": origin.get("streamId") or "", "author": it.get("author") or "",
            "published_at": pub, "reading_time": _reading_time(content),
        }

    def _stream(self, stream: str, *, since: datetime | None, limit: int,
                exclude_read: bool = False, oldest_first: bool = True) -> list[dict]:
        out, cont = [], None
        while len(out) < limit:
            data = self._get(
                f"stream/contents/{quote(stream, safe='/-')}", output="json",
                n=min(250, limit - len(out)), c=cont,
                ot=int(since.timestamp()) if since else None,
                r="o" if oldest_first else None,
                xt=READ if exclude_read else None,
            )
            items = data.get("items") or []
            out.extend(items)
            cont = data.get("continuation")
            if not items or not cont:
                break
        return out

    def check(self) -> str:
        info = self._get("user-info", output="json")
        return info.get("userName") or info.get("userEmail") or info.get("userId") or "?"

    def fetch_new(self, state, window_start):
        last_run = state.get("greader_since")
        since = datetime.fromisoformat(last_run) - timedelta(hours=1) if last_run else window_start
        streams = [f"user/-/label/{l}" for l in self.s.greader_labels] or [READING_LIST]
        seen = set(state.get("greader_seen", []))
        items, fresh_ids = [], []
        for stream in streams:
            for raw in self._stream(stream, since=since, limit=self.s.max_entries,
                                    exclude_read=self.s.only_unread):
                e = self._norm(raw)
                if e["id"] in seen:
                    continue
                seen.add(e["id"])
                fresh_ids.append(e["id"])
                items.append(e)
        if len(items) > self.s.max_entries:
            log.warning("Mehr als %d neue Einträge – nur die neuesten werden betrachtet", self.s.max_entries)
            items = items[-self.s.max_entries:]
        keep = (state.get("greader_seen", []) + fresh_ids)[-SEEN_KEEP:]
        return items, {"greader_since": datetime.now(timezone.utc).isoformat(), "greader_seen": keep}

    def fetch_unread(self, limit):
        streams = [f"user/-/label/{l}" for l in self.s.greader_labels] or [READING_LIST]
        seen, out = set(), []
        for stream in streams:
            for raw in self._stream(stream, since=None, limit=limit, exclude_read=True, oldest_first=False):
                e = self._norm(raw)
                if e["id"] not in seen:
                    seen.add(e["id"])
                    out.append(e)
        return out[:limit]

    def starred(self, limit=300):
        try:
            return [self._norm(it) for it in self._stream(STARRED, since=None, limit=limit, oldest_first=False)]
        except (httpx.HTTPError, SystemExit) as e:
            log.warning("Stern-Einträge nicht abrufbar: %s", e)
            return []


def make_source(s) -> Source:
    if s.source == "greader":
        return GReaderSource(s)
    return MinifluxSource(s)
