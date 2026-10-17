#!/usr/bin/env python3
"""Wandelt einen Read-Later-Export (JSON-Liste mit link/tags/date/starred)
in config/examples.txt um. Die Beispiele zeigen dem LLM, was du bisher
gespeichert hast.

Aufruf:  python3 tools/import_readlater.py ReadLater.json config/examples.txt [anzahl]
"""
import json
import re
import sys
import urllib.parse


def slug_to_text(url: str) -> str:
    p = urllib.parse.urlparse(url)
    host = p.netloc.removeprefix("www.")
    path = urllib.parse.unquote(p.path).strip("/")
    # letzten "sprechenden" Pfadteil nehmen
    parts = [x for x in path.split("/") if x and not re.fullmatch(r"[\d.]+|p|article|articles|post|posts|blog|news|features|en|de", x)]
    words = parts[-1] if parts else ""
    words = re.sub(r"\.(html?|php)$", "", words)
    words = re.sub(r"[-_+]+", " ", words)
    words = re.sub(r"\b\d{5,}\b", "", words).strip()
    return f"{host}: {words[:110]}" if words else host


def main() -> None:
    src, dst = sys.argv[1], sys.argv[2]
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else 200
    data = json.load(open(src, encoding="utf-8"))
    data = [x for x in data if x.get("tags")]
    data.sort(key=lambda x: -x.get("date", 0))
    lines = []
    for x in data:
        if len(lines) >= limit:
            break
        text = slug_to_text(x["link"])
        # nur Einträge, deren URL genug über den Inhalt verrät
        if len(re.findall(r"[a-zA-ZäöüÄÖÜß]{3,}", text.split(":", 1)[-1])) < 3:
            continue
        tags = ", ".join(t.strip() for t in x["tags"])
        lines.append(f"[{tags}] {text}")
    with open(dst, "w", encoding="utf-8") as f:
        f.write("# Zuletzt gespeicherte Artikel (neueste zuerst). Format: [Tags] Quelle: Titel aus URL\n")
        f.write("\n".join(lines) + "\n")
    print(f"{len(lines)} Beispiele nach {dst} geschrieben")


if __name__ == "__main__":
    main()
