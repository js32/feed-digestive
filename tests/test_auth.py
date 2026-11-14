"""Anmeldung der Status-Seite: Formular, Cookie für Monate, alles unter /status geschützt."""
import threading
from http.server import ThreadingHTTPServer

import httpx
import pytest

from app import auth
from app.__main__ import _make_handler
from app.config import Settings


@pytest.fixture(autouse=True)
def _reset():
    auth._fails.clear()
    yield
    auth._fails.clear()


def test_cookie_roundtrip_and_expiry():
    c = auth.make_cookie("geheim", "tok", now=1000)
    assert auth.valid_cookie(c, "geheim", "tok", now=1000 + auth.DAYS * 86400 - 1)
    assert not auth.valid_cookie(c, "geheim", "tok", now=1000 + auth.DAYS * 86400 + 1)   # abgelaufen
    assert not auth.valid_cookie(c, "anderes", "tok", now=1000)                          # Passwort geändert
    assert not auth.valid_cookie(c, "geheim", "anderer-token", now=1000)
    later = str(int(c.split(".")[0]) + 999999) + "." + c.split(".")[1]                   # Ablauf gefälscht
    assert not auth.valid_cookie(later, "geheim", "tok", now=1000)
    for junk in (None, "", "abc", "1.", ".x", "99999999999"):
        assert not auth.valid_cookie(junk, "geheim", "tok", now=1000)


def test_cookie_header():
    h = auth.set_cookie_header("v", "/tok/status", secure=True)
    assert "HttpOnly" in h and "SameSite=Strict" in h and "Secure" in h and "Path=/tok/status" in h
    assert f"Max-Age={auth.DAYS * 86400}" in h
    assert "Secure" not in auth.set_cookie_header("v", "/tok/status", secure=False)
    assert auth.cookie_from_header("a=1; fd_status=xyz.abc; b=2") == "xyz.abc"
    assert auth.cookie_from_header("a=1") is None


@pytest.fixture
def server(env, monkeypatch):
    env.setenv("STATUS_PASSWORD", "sehr geheim")
    monkeypatch.setattr("app.__main__.time.sleep", lambda s: None)
    s = Settings.from_env()
    (s.data_dir / "feed.xml").write_text("<feed/>")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(s))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    with httpx.Client(base_url=f"http://127.0.0.1:{httpd.server_port}", follow_redirects=False) as c:
        yield c
    httpd.shutdown()


def test_everything_under_status_needs_login(server):
    c = server
    r = c.get("/tok/status")
    assert r.status_code == 200 and 'type="password"' in r.text and "Digest" not in r.text
    for path in ("/tok/status.json", "/tok/status/logs", "/tok/status/health", "/tok/status/report/learn"):
        assert c.get(path).status_code == 401, path
    assert c.get("/tok/feed.xml").status_code == 200  # der Feed braucht keine Anmeldung


def test_login_sets_cookie_and_opens_page(server):
    c = server
    bad = c.post("/tok/status/login", data={"password": "falsch"})
    assert bad.status_code == 401 and "stimmt nicht" in bad.text and "set-cookie" not in bad.headers
    r = c.post("/tok/status/login", data={"username": "feed-digestive", "password": "sehr geheim"})
    assert r.status_code == 303 and r.headers["location"] == "/tok/status"
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert "Path=/tok/;" in cookie  # deckt auch /tok/status.json ab – "Path=/tok/status" täte das nicht
    assert "Secure" in cookie  # PUBLIC_URL ist https
    c.cookies.clear()  # Secure-Cookies schickt httpx nicht über http zurück – deshalb von Hand
    signed_in = {"Cookie": cookie.split(";")[0]}
    assert c.get("/tok/status.json", headers=signed_in).json()["login"] is True
    page = c.get("/tok/status", headers=signed_in).text
    assert "<title>" in page and 'type="password"' not in page
    assert c.get("/tok/status.json", headers={"Cookie": "fd_status=gefaelscht.abc"}).status_code == 401
    out = c.get("/tok/status/logout", headers=signed_in)
    assert out.status_code == 303 and "Max-Age=0" in out.headers["set-cookie"]


def test_lockout_after_many_failures(server):
    c = server
    for _ in range(auth.MAX_FAILS):
        assert c.post("/tok/status/login", data={"password": "x"}).status_code == 401
    r = c.post("/tok/status/login", data={"password": "sehr geheim"})
    assert r.status_code == 429 and "set-cookie" not in r.headers


def test_login_only_at_the_right_place(server):
    c = server
    assert c.post("/tok/feed.xml", data={"password": "sehr geheim"}).status_code == 404
    assert c.post("/status/login", data={"password": "sehr geheim"}).status_code == 404
    assert c.post("/tok/status/login", content=b"x" * 5000).status_code == 400


def test_without_password_nothing_changes(env):
    s = Settings.from_env()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(s))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    with httpx.Client(base_url=f"http://127.0.0.1:{httpd.server_port}") as c:
        assert c.get("/tok/status.json").json()["login"] is False
        assert c.post("/tok/status/login", data={"password": "x"}).status_code == 404
    httpd.shutdown()
