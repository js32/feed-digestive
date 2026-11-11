"""Benachrichtigung aufs Handy, wenn ein Digest fertig ist oder ein Lauf scheitert.

Zwei Wege, einzeln oder zusammen:
  NTFY_URL   Adresse eines ntfy-Themas, z. B. https://ntfy.sh/<langer-zufälliger-name> oder eigener Server
  BARK_URL   Bark (nur iPhone): die Adresse, die die App anzeigt, z. B. https://api.day.app/<schlüssel>,
             oder die eines eigenen bark-server

Beide Dienste sehen den Inhalt der Nachricht. Deshalb steht standardmäßig nur die Zahl der Artikel
drin; NOTIFY_DETAILS=true nimmt Rubriken und Titel mit auf.
"""
from __future__ import annotations

import logging
from urllib.parse import urlsplit

import httpx

from .config import Settings

log = logging.getLogger(__name__)


def _target(url: str, what: str = "NTFY_URL", example: str = "https://ntfy.sh/<thema>") -> tuple[str, str]:
    """"https://ntfy.sh/mein-thema" -> ("https://ntfy.sh", "mein-thema"); bei Bark ist der letzte Teil
    der Geräteschlüssel."""
    parts = urlsplit(url.strip())
    path = parts.path.strip("/")
    if parts.scheme not in ("http", "https") or not parts.netloc or not path:
        raise ValueError(f"{what} muss wie {example} aussehen")
    base, _, last = path.rpartition("/")
    return f"{parts.scheme}://{parts.netloc}" + (f"/{base}" if base else ""), last


def _ntfy(s: Settings, title: str, message: str, urgent: bool) -> None:
    base, topic = _target(s.ntfy_url)
    body = {"topic": topic, "title": title, "message": message,  # JSON statt Kopfzeilen: Umlaute bleiben heil
            "tags": ["warning" if urgent else "newspaper"]}
    if urgent:
        body["priority"] = 4
    headers = {"Authorization": f"Bearer {s.ntfy_token}"} if s.ntfy_token else {}
    httpx.post(base, json=body, headers=headers, timeout=10).raise_for_status()


def _bark(s: Settings, title: str, message: str, urgent: bool) -> None:
    base, key = _target(s.bark_url, "BARK_URL", "https://api.day.app/<schlüssel>")
    body = {"device_key": key, "title": title, "body": message, "group": "feed-digestive",
            "level": "timeSensitive" if urgent else "active"}
    r = httpx.post(f"{base}/push", json=body, timeout=10)
    r.raise_for_status()
    try:
        answer = r.json()
    except ValueError:
        answer = {}
    if isinstance(answer, dict) and answer.get("code") not in (None, 200):  # Bark meldet Fehler auch mit HTTP 200
        raise ValueError(f"Bark antwortet: {answer.get('message') or answer}")


CHANNELS = {"ntfy": ("ntfy_url", _ntfy), "Bark": ("bark_url", _bark)}


def channels(s: Settings) -> list[str]:
    return [name for name, (attr, _) in CHANNELS.items() if getattr(s, attr)]


def send(s: Settings, title: str, message: str, urgent: bool = False) -> dict[str, bool]:
    """Schickt die Nachricht über alle eingerichteten Wege; Ergebnis je Weg. Fehler werden nur
    protokolliert – eine Benachrichtigung darf nie einen Lauf scheitern lassen."""
    result = {}
    for name in channels(s):
        try:
            CHANNELS[name][1](s, title, message, urgent)
            result[name] = True
        except (httpx.HTTPError, ValueError) as e:
            log.warning("%s: Nachricht nicht zugestellt: %s", name, e)
            result[name] = False
    return result


def digest_message(s: Settings, digest: dict) -> tuple[str, str]:
    items = digest.get("items") or []
    title = f"Digest vom {digest.get('date', '')}: {len(items)} Artikel".replace("  ", " ")
    if not s.notify_details:
        return title, "Der neue Digest liegt im Feed."
    lines, group = [], None
    for it in items:
        topic = it.get("topic") or ""
        if topic and topic != group:
            lines.append(f"\n{topic}")
        group = topic
        lines.append("• " + ("🎲 " if it.get("explore") else "") + (it.get("digest_title") or it.get("title") or ""))
    return title, "\n".join(lines).strip()[:3000]  # ntfy kürzt bei 4096 Bytes, Apple bei etwa 4 KB


def digest_ready(s: Settings, digest: dict | None) -> None:
    if digest and digest.get("items"):
        send(s, *digest_message(s, digest))


def run_failed(s: Settings, error: BaseException) -> None:
    detail = f"{type(error).__name__}: {error}"[:300] if s.notify_details else "Details stehen auf der Status-Seite."
    send(s, "Digest-Lauf fehlgeschlagen", detail, urgent=True)
