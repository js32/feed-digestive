"""Anmeldung für die Status-Seite: einmal Passwort eingeben, dann merkt sich der Browser das
für Monate (STATUS_PASSWORD).

Warum nicht das Passwortfenster des Webservers (Basic Auth)? Safari auf dem iPhone vergisst es
ständig und fragt bei jedem Öffnen neu. Hier gibt es stattdessen ein Anmeldeformular und ein
signiertes Cookie. Der Server speichert nichts: Das Cookie enthält sein Ablaufdatum und eine
Unterschrift, die nur mit dem Passwort zu erzeugen ist. Ein neues Passwort meldet alle Geräte ab.
"""
from __future__ import annotations

import hashlib
import hmac
import threading
import time
from html import escape

COOKIE = "fd_status"
DAYS = 180
MAX_FAILS, FAIL_WINDOW = 8, 600  # mehr als 8 falsche Versuche in 10 Minuten: Pause für alle

_fails: list[float] = []
_mu = threading.Lock()


def _key(password: str, token: str) -> bytes:
    return hashlib.sha256(f"feed-digestive status|{token}|{password}".encode()).digest()


def make_cookie(password: str, token: str, now: float | None = None) -> str:
    expires = int((now or time.time()) + DAYS * 86400)
    sig = hmac.new(_key(password, token), str(expires).encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{sig}"


def valid_cookie(value: str | None, password: str, token: str, now: float | None = None) -> bool:
    expires, _, sig = (value or "").partition(".")
    if not expires.isdigit() or int(expires) < (now or time.time()):
        return False
    good = hmac.new(_key(password, token), expires.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(good, sig)


def cookie_from_header(header: str | None) -> str | None:
    for part in (header or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE:
            return value
    return None


def set_cookie_header(value: str, path: str, secure: bool, max_age: int = DAYS * 86400) -> str:
    return (f"{COOKIE}={value}; Path={path}; Max-Age={max_age}; HttpOnly; SameSite=Strict"
            + ("; Secure" if secure else ""))


def locked(now: float | None = None) -> bool:
    now = now or time.time()
    with _mu:
        _fails[:] = [t for t in _fails if now - t < FAIL_WINDOW]
        return len(_fails) >= MAX_FAILS


def check_password(given: str, password: str, now: float | None = None) -> bool:
    """Vergleicht in konstanter Zeit und zählt Fehlversuche."""
    ok = hmac.compare_digest(given.encode(), password.encode())
    if not ok:
        with _mu:
            _fails.append(now or time.time())
    return ok


def login_page(action: str, message: str = "") -> bytes:
    note = f'<p class="err" role="alert">{escape(message)}</p>' if message else ""
    return f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex"><title>Anmelden – feed-digestive</title>
<style>
:root {{ --bg: #f6f7f9; --card: #fff; --text: #111827; --muted: #6b7280; --border: #d1d5db; --brand: #4f46e5; --on-brand: #fff; --err: #b91c1c; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg: #0f172a; --card: #1e293b; --text: #f1f5f9; --muted: #94a3b8; --border: #475569; --brand: #a5b4fc; --on-brand: #0f172a; --err: #fca5a5; }}
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 16px; background: var(--bg);
  color: var(--text); font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }}
form {{ width: 100%; max-width: 360px; padding: 28px 24px; background: var(--card); border: 1px solid var(--border);
  border-radius: 12px; }}
h1 {{ margin: 0 0 4px; font-size: 20px; }}
p {{ margin: 0 0 20px; color: var(--muted); font-size: 14px; }}
.err {{ color: var(--err); }}
label {{ display: block; margin-bottom: 6px; font-size: 14px; }}
input, button {{ width: 100%; padding: 11px 12px; font: inherit; border-radius: 8px; }}
input {{ border: 1px solid var(--border); background: transparent; color: inherit; }}
button {{ margin-top: 16px; border: 0; background: var(--brand); color: var(--on-brand); font-weight: 600; cursor: pointer; }}
input:focus-visible, button:focus-visible {{ outline: 2px solid var(--brand); outline-offset: 2px; }}
</style></head><body>
<form method="post" action="{escape(action, quote=True)}">
  <h1>Status-Seite</h1>
  <p>Einmal anmelden – dieses Gerät bleibt dann {DAYS // 30} Monate angemeldet.</p>
  {note}
  <input type="text" name="username" value="feed-digestive" autocomplete="username" hidden>
  <label for="pw">Passwort</label>
  <input id="pw" type="password" name="password" autocomplete="current-password" required autofocus>
  <button type="submit">Anmelden</button>
</form></body></html>""".encode()
