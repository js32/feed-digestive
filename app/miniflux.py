"""Minimaler Miniflux-API-Client."""
from __future__ import annotations

import html
import logging
import re
from datetime import datetime

import httpx

log = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_DROP_RE = re.compile(r"<(script|style|noscript|svg|figure)[^>]*>.*?</\1>", re.S | re.I)


def html_to_text(raw: str | None) -> str:
    if not raw:
        return ""
    raw = _DROP_RE.sub(" ", raw)
    raw = re.sub(r"</(p|div|h\d|li|br|tr)>|<br\s*/?>", "\n", raw, flags=re.I)
    text = html.unescape(_TAG_RE.sub(" ", raw))
    return _WS_RE.sub(" ", text).strip()


class Miniflux:
    def __init__(self, base_url: str, token: str, timeout: float = 30):
        self.client = httpx.Client(
            base_url=base_url.rstrip("/") + "/v1",
            headers={"X-Auth-Token": token, "User-Agent": "rss-digest/1.0"},
            timeout=timeout,
        )

    def _get(self, path: str, **params) -> dict:
        r = self.client.get(path, params={k: v for k, v in params.items() if v is not None})
        if r.status_code == 401:
            raise SystemExit("Miniflux: API-Token ungültig (401)")
        r.raise_for_status()
        return r.json()

    def me(self) -> dict:
        return self._get("/me")

    def entries_since(self, *, after_entry_id: int | None, after_ts: int,
                      max_entries: int, only_unread: bool, category_ids: list[int]) -> list[dict]:
        """Neue Einträge in aufsteigender ID-Reihenfolge."""
        out: list[dict] = []
        cats = category_ids or [None]
        for cat in cats:
            cursor = after_entry_id
            while len(out) < max_entries:
                params = dict(
                    order="id", direction="asc", limit=100,
                    status="unread" if only_unread else None,
                    category_id=cat,
                )
                if cursor:
                    params["after_entry_id"] = cursor
                else:
                    params["after"] = after_ts
                data = self._get("/entries", **params)
                batch = data.get("entries") or []
                if not batch:
                    break
                out.extend(batch)
                cursor = batch[-1]["id"]
                if len(batch) < 100:
                    break
        if len(out) > max_entries:
            log.warning("Mehr als %d neue Einträge – nur die neuesten werden betrachtet", max_entries)
            out = sorted(out, key=lambda e: e["id"])[-max_entries:]
        return out

    def starred_titles(self, limit: int = 30) -> list[str]:
        try:
            data = self._get("/entries", starred="true", order="published_at",
                             direction="desc", limit=limit)
        except httpx.HTTPError as e:  # optional, darf fehlschlagen
            log.warning("Stern-Einträge nicht abrufbar: %s", e)
            return []
        return [f"{e['feed']['title']}: {e['title']}" for e in data.get("entries") or []]


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
