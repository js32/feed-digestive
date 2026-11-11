"""Benachrichtigung per ntfy, wenn ein Digest fertig ist oder ein Lauf scheitert.

NTFY_URL ist die Adresse des Themas, z. B. https://ntfy.sh/<langer-zufälliger-name> oder die eines
eigenen ntfy-Servers. Wer das Thema kennt, kann mitlesen – deshalb steht in der Nachricht
standardmäßig nur die Zahl der Artikel. NTFY_DETAILS=true nimmt Rubriken und Titel mit auf
(sinnvoll mit eigenem Server oder NTFY_TOKEN).
"""
from __future__ import annotations

import logging
from urllib.parse import urlsplit

import httpx

from .config import Settings

log = logging.getLogger(__name__)


def _target(url: str) -> tuple[str, str]:
    """"https://ntfy.sh/mein-thema" -> ("https://ntfy.sh", "mein-thema")"""
    parts = urlsplit(url.strip())
    path = parts.path.strip("/")
    if parts.scheme not in ("http", "https") or not parts.netloc or not path:
        raise ValueError("NTFY_URL muss wie https://ntfy.sh/<thema> aussehen")
    base, _, topic = path.rpartition("/")
    return f"{parts.scheme}://{parts.netloc}" + (f"/{base}" if base else ""), topic


def send(s: Settings, title: str, message: str, tags: list[str] | None = None, priority: int | None = None) -> bool:
    """Schickt eine Nachricht. Fehler werden nur protokolliert – eine Benachrichtigung darf nie
    einen Lauf scheitern lassen."""
    if not s.ntfy_url:
        return False
    try:
        base, topic = _target(s.ntfy_url)
        body = {"topic": topic, "title": title, "message": message}  # JSON statt Kopfzeilen: Umlaute bleiben heil
        if tags:
            body["tags"] = tags
        if priority:
            body["priority"] = priority
        headers = {"Authorization": f"Bearer {s.ntfy_token}"} if s.ntfy_token else {}
        r = httpx.post(base, json=body, headers=headers, timeout=10)
        r.raise_for_status()
        return True
    except (httpx.HTTPError, ValueError) as e:
        log.warning("ntfy: Nachricht nicht zugestellt: %s", e)
        return False


def digest_message(s: Settings, digest: dict) -> tuple[str, str]:
    items = digest.get("items") or []
    title = f"Digest vom {digest.get('date', '')}: {len(items)} Artikel".replace("  ", " ")
    if not s.ntfy_details:
        return title, "Der neue Digest liegt im Feed."
    lines, group = [], None
    for it in items:
        topic = it.get("topic") or ""
        if topic and topic != group:
            lines.append(f"\n{topic}")
        group = topic
        lines.append("• " + ("🎲 " if it.get("explore") else "") + (it.get("digest_title") or it.get("title") or ""))
    return title, "\n".join(lines).strip()[:3500]  # ntfy kürzt bei 4096 Bytes


def digest_ready(s: Settings, digest: dict | None) -> None:
    if s.ntfy_url and digest and digest.get("items"):
        send(s, *digest_message(s, digest), tags=["newspaper"])


def run_failed(s: Settings, error: BaseException) -> None:
    if s.ntfy_url:
        detail = f"{type(error).__name__}: {error}"[:300] if s.ntfy_details else "Details stehen auf der Status-Seite."
        send(s, "Digest-Lauf fehlgeschlagen", detail, tags=["warning"], priority=4)
