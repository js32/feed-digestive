"""Atom-Feed erzeugen."""
from __future__ import annotations

import hashlib
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


def _item_html(it: dict, with_header: bool = True) -> str:
    title = escape(it.get("digest_title") or it.get("title") or "")
    url = escape(it.get("url") or "", quote=True)
    meta = " · ".join(x for x in (
        escape(it.get("feed") or _host(it.get("url") or "")),
        f"{it['reading_time']} min" if it.get("reading_time") else "",
        f"Relevanz {it['score']}/10" if it.get("score") is not None else "",
    ) if x)
    orig = it.get("title") or ""
    orig_html = (f'<br><small>Original: „{escape(orig)}“</small>'
                 if orig and orig != it.get("digest_title") else "")
    head = f'<h3><a href="{url}">{title}</a></h3>' if with_header else ""
    why = f'<p><em>Warum lesen: {escape(it["why"])}</em></p>' if it.get("why") else ""
    return (f'{head}<p><small>{meta}</small>{orig_html}</p>'
            f'<p>{escape(it.get("excerpt") or "")}</p>{why}'
            f'<p><a href="{url}">→ Zum Artikel</a></p>')


def _digest_html(d: dict) -> str:
    toc = "".join(
        f'<li><a href="{escape(it["url"], quote=True)}">{escape(it.get("digest_title") or it["title"])}</a></li>'
        for it in d["items"]
    )
    body = "<hr>".join(_item_html(it) for it in d["items"])
    return (f"<p>{len(d['items'])} von {d.get('considered', '?')} neuen Artikeln ausgewählt.</p>"
            f"<ol>{toc}</ol><hr>{body}")


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
    _el(author, "name", "rss-digest")
    _el(root, "generator", "rss-digest")

    for d in sorted(digests, key=lambda d: d["generated_at"], reverse=True):
        if s.feed_mode == "daily":
            e = _el(root, "entry")
            _el(e, "title", f"Digest vom {d['date']} – {len(d['items'])} Artikel")
            _el(e, "id", _uid(s.feed_url, d["id"]))
            _el(e, "updated", d["generated_at"])
            _el(e, "published", d["generated_at"])
            _el(e, "link", rel="alternate", href=d["items"][0]["url"] if d["items"] else s.feed_url)
            _el(e, "content", _digest_html(d), type="html")
        else:
            for it in d["items"]:
                e = _el(root, "entry")
                _el(e, "title", it.get("digest_title") or it["title"])
                _el(e, "id", _uid(s.feed_url, it["url"]))
                _el(e, "updated", d["generated_at"])
                _el(e, "published", d["generated_at"])
                _el(e, "link", rel="alternate", href=it["url"])
                a = _el(e, "author")
                _el(a, "name", it.get("feed") or _host(it["url"]))
                _el(e, "summary", it.get("excerpt") or "")
                _el(e, "content", _item_html(it, with_header=False), type="html")

    out = s.data_dir / "feed.xml"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    ET.ElementTree(root).write(tmp, encoding="utf-8", xml_declaration=True)
    tmp.replace(out)
