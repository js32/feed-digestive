"""Atom-Feed erzeugen."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from html import escape
from xml.etree import ElementTree as ET

from .config import Settings

ATOM = "http://www.w3.org/2005/Atom"
ET.register_namespace("", ATOM)


def _el(parent, tag, text=None, **attrs):
    e = ET.SubElement(parent, f"{{{ATOM}}}{tag}", {k: str(v) for k, v in attrs.items()})
    if text is not None:
        e.text = text
    return e


def _host(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).netloc.removeprefix("www.")


def _link(it: dict, s: Settings | None) -> str:
    """Link auf den Artikel – mit Klick-Zähler über den eigenen Server, wenn aktiv."""
    if s is not None and s.track_clicks and it.get("entry_id"):
        from .feedback import tracked_url
        return tracked_url(s.feed_base, it["entry_id"])
    from .feedback import safe_url
    return safe_url(it.get("url"))


def _item_html(it: dict, with_header: bool = True, s: Settings | None = None) -> str:
    title = escape(it.get("digest_title") or it.get("title") or "")
    url = escape(_link(it, s), quote=True)
    meta = " · ".join(x for x in (
        escape(it.get("feed") or _host(it.get("url") or "")),
        f"{it['reading_time']} min" if it.get("reading_time") else "",
        f"Relevanz {it['score']}/10" if it.get("score") is not None else "",
        "aufgefüllt (unter MIN_SCORE)" if it.get("filler") else "",
    ) if x)
    orig = it.get("title") or ""
    orig_html = (f'<br>Original: „{escape(orig)}“'
                 if orig and orig != it.get("digest_title") else "")
    if it.get("similar_to"):
        orig_html += f'<br>Ähnlich zu deinem Stern: „{escape(it["similar_to"])}“'
    if it.get("filler"):
        title = "↓ " + title
    if it.get("explore"):
        title = "🎲 Zufallsfund: " + title
    head = f'<h3><a href="{url}">{title}</a></h3>' if with_header else ""
    # Reihenfolge: Titel → warum lesen → Auszug → Kleingedrucktes → Link
    why = f'<p><strong>Warum lesen:</strong> {escape(it["why"])}</p>' if it.get("why") else ""
    excerpt = f'<p>{escape(it.get("excerpt") or "")}</p>' if it.get("excerpt") else ""
    return (f'{head}{why}{excerpt}'
            f'<p><small>{meta}{orig_html}</small></p>'
            f'<p><a href="{url}">→ Zum Artikel</a></p>{_pixel(it, s)}')


def _pixel(it: dict, s: Settings | None) -> str:
    """Unsichtbares Bild am Ende (OPEN_PIXEL=log) – nur zur Messung, ob der Reader es erst beim Öffnen lädt."""
    if s is None or s.open_pixel != "log" or not it.get("entry_id"):
        return ""
    from .feedback import pixel_url
    # ohne width/height="1": Miniflux entfernt Bilder mit diesen Maßen als Tracking-Pixel.
    # Das GIF selbst ist 1×1 px und transparent, also trotzdem unsichtbar.
    return f'<img src="{escape(pixel_url(s.feed_base, it["entry_id"]), quote=True)}" alt="">'


def _sections(items: list[dict]) -> list[tuple[str, list[dict]]]:
    """Aufeinanderfolgende Artikel mit gleichem Thema als Abschnitt; ohne Themen ein Abschnitt."""
    out: list[tuple[str, list[dict]]] = []
    for it in items:
        topic = it.get("topic") or ""
        if out and out[-1][0] == topic:
            out[-1][1].append(it)
        else:
            out.append((topic, [it]))
    return out


def _digest_html(d: dict, s: Settings | None = None) -> str:
    def toc_item(it):
        return (f'<li><a href="{escape(_link(it, s), quote=True)}">'
                f'{escape(it.get("digest_title") or it["title"])}</a></li>')

    toc, body = [], []
    for topic, items in _sections(d["items"]):
        head = f"<h2>{escape(topic)}</h2>" if topic else ""
        toc.append((f"<li>{escape(topic)}<ul>" if topic else "") + "".join(map(toc_item, items))
                   + ("</ul></li>" if topic else ""))
        body.append(head + "<hr>".join(_item_html(it, s=s) for it in items))
    toc_tag = "ul" if any(topic for topic, _ in _sections(d["items"])) else "ol"
    return (f"<p>{len(d['items'])} von {d.get('considered', '?')} neuen Artikeln ausgewählt.</p>"
            f"<{toc_tag}>{''.join(toc)}</{toc_tag}><hr>{'<hr>'.join(body)}")


def _uid(*parts: str) -> str:
    return "urn:sha1:" + hashlib.sha1("|".join(parts).encode()).hexdigest()


def write_feed(s: Settings, digests: list[dict]) -> None:
    root = ET.Element(f"{{{ATOM}}}feed")
    _el(root, "title", s.feed_title)
    _el(root, "subtitle", "Täglich die interessantesten neuen Artikel aus meinen Feeds")
    _el(root, "id", _uid(s.feed_url))
    _el(root, "link", rel="self", href=s.feed_url, type="application/atom+xml")
    latest = max((d["generated_at"] for d in digests), default=datetime.now(timezone.utc).isoformat())
    _el(root, "updated", latest)
    author = _el(root, "author")
    _el(author, "name", "feed-digestive")
    _el(root, "generator", "feed-digestive")

    for d in sorted(digests, key=lambda d: d["generated_at"], reverse=True):
        if s.feed_mode == "daily":
            e = _el(root, "entry")
            _el(e, "title", f"Digest vom {d['date']} – {len(d['items'])} Artikel")
            _el(e, "id", _uid(s.feed_url, d["id"]))
            _el(e, "updated", d["generated_at"])
            _el(e, "published", d["generated_at"])
            # bewusst kein Artikel-Link: sonst zählt ein Crawler-Abruf als Klick
            _el(e, "link", rel="alternate", href=s.feed_url)
            _el(e, "content", _digest_html(d, s), type="html")
        else:
            for it in d["items"]:
                _item_entry(root, s, d, it, s.feed_url)

    _write(root, s.data_dir / "feed.xml")
    write_topic_feeds(s, digests)


def _item_entry(root, s: Settings, d: dict, it: dict, feed_url: str) -> None:
    """Ein Feed-Eintrag pro Artikel (FEED_MODE=items und Themenfeeds)."""
    e = _el(root, "entry")
    mark = "🎲 Zufallsfund: " if it.get("explore") else "↓ " if it.get("filler") else ""
    _el(e, "title", mark + (it.get("digest_title") or it["title"]))
    _el(e, "id", _uid(feed_url, it["url"]))
    _el(e, "updated", d["generated_at"])
    _el(e, "published", d["generated_at"])
    _el(e, "link", rel="alternate", href=_link(it, s))
    a = _el(e, "author")
    _el(a, "name", it.get("feed") or _host(it["url"]))
    for term in dict.fromkeys(x for x in (it.get("interest_topic"), it.get("topic")) if x):
        _el(e, "category", term=term)
    _el(e, "summary", " ".join(x for x in (
        f"Warum lesen: {it['why']}" if it.get("why") else "", it.get("excerpt") or "") if x))
    _el(e, "content", _item_html(it, with_header=False, s=s), type="html")


def _write(root, out) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    ET.ElementTree(root).write(tmp, encoding="utf-8", xml_declaration=True)
    tmp.replace(out)


# ----------------------------------------------------------------------
# Themenfeeds: ein Feed je Interessen-Thema, ein Eintrag pro Artikel
# ----------------------------------------------------------------------
_FOLD = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "é": "e", "è": "e", "á": "a", "à": "a"})


def topic_slug(topic: str) -> str:
    """"Künstliche Intelligenz" -> "kuenstliche-intelligenz" (für die Feed-Adresse)."""
    return re.sub(r"[^a-z0-9]+", "-", topic.lower().translate(_FOLD)).strip("-")[:64]


def topic_feed_url(s: Settings, slug: str) -> str:
    return f"{s.feed_base}/topic/{slug}.xml"


def write_topic_feeds(s: Settings, digests: list[dict]) -> list[dict]:
    """Schreibt data/topics/<thema>.xml für jedes Interessen-Thema, das in den gespeicherten Digests
    vorkommt, plus index.json für die Status-Seite. Feeds zu Themen ohne Artikel verschwinden wieder."""
    by_slug: dict[str, dict] = {}
    older: dict[str, str] | None = None  # Digests von vor den Themenfeeds: Thema aus dem Protokoll nachschlagen
    for d in sorted(digests, key=lambda d: d["generated_at"], reverse=True):
        for it in d["items"]:
            if "interest_topic" not in it:
                if older is None:
                    from .feedback import read_jsonl
                    older = {str(r.get("entry_id")): r["topic"] for r in read_jsonl(s.data_dir / "history.jsonl")
                             if r.get("topic") and str(r["topic"]).lower() not in ("sonstiges", "other", "-")}
                it = {**it, "interest_topic": older.get(str(it.get("entry_id")))}
            topic = (it.get("interest_topic") or "").strip()
            slug = topic_slug(topic)
            if slug:
                by_slug.setdefault(slug, {"topic": topic, "entries": []})["entries"].append((d, it))
    folder = s.data_dir / "topics"
    folder.mkdir(parents=True, exist_ok=True)
    index = []
    for slug, t in sorted(by_slug.items()):
        url = topic_feed_url(s, slug)
        root = ET.Element(f"{{{ATOM}}}feed")
        _el(root, "title", f"{s.feed_title} – {t['topic']}")
        _el(root, "subtitle", f"Alle Digest-Artikel zum Thema {t['topic']}")
        _el(root, "id", _uid(url))
        _el(root, "link", rel="self", href=url, type="application/atom+xml")
        _el(root, "updated", t["entries"][0][0]["generated_at"])
        _el(_el(root, "author"), "name", "feed-digestive")
        _el(root, "generator", "feed-digestive")
        for d, it in t["entries"]:
            _item_entry(root, s, d, it, url)
        _write(root, folder / f"{slug}.xml")
        index.append({"topic": t["topic"], "slug": slug, "count": len(t["entries"])})
    for old in folder.glob("*.xml"):
        if old.stem not in by_slug:
            old.unlink()
    tmp = folder / "index.tmp"
    tmp.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    tmp.replace(folder / "index.json")
    return index
