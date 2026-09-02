"""There is no login page any more — access is a shared key.

These tests exist because the failure mode is severe and silent: /kalshi renders
a real Kalshi cash balance, deposits, withdrawals and P&L, and the app is
deployed on a public URL. If the gate ever fails OPEN, that is world-readable."""
import asyncio
import contextlib

import pytest

import bot.web as W
from bot import webauth


class FakeUser:
    id, username, is_admin, is_active = 1, "owner", True, True


class FakeDB:
    def __init__(self, user=None):
        self.user = user

    def commit(self):
        pass


class Req:
    def __init__(self, path="/live", query=None, cookies=None):
        self.path = path
        self.path_qs = path
        self.query = query or {}
        self.cookies = cookies or {}
        self.headers = {}
        self.secure = False      # aiohttp Request attribute _secure_cookie reads
        self._d = {}

    def get(self, k, default=None):
        return self._d.get(k, default)

    def __setitem__(self, k, v):
        self._d[k] = v

    def __getitem__(self, k):
        return self._d[k]


class Resp:
    status = 200

    def __init__(self):
        self.cookies_set = {}

    def set_cookie(self, k, v, **kw):
        self.cookies_set[k] = v


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    """No real DB, no real session; owner resolves to a fixed fake."""
    monkeypatch.setattr(W, "db_session", lambda: contextlib.nullcontext(FakeDB()))
    monkeypatch.setattr(webauth, "current_user", lambda r, db: None)
    monkeypatch.setattr(webauth, "owner_user", lambda db: FakeUser())
    monkeypatch.setattr(webauth, "make_session_token", lambda uid, **k: "TOK")
    monkeypatch.delenv("ACCESS_KEY", raising=False)
    monkeypatch.delenv("WEB_TOKEN", raising=False)


async def _run(req):
    async def handler(r):
        return Resp()
    return await W.auth_guard(req, handler)


def test_no_key_configured_serves_nothing(monkeypatch):
    """An UNSET env var must fail closed. This is the one that would silently
    publish a real trading account if it went the other way."""
    r = asyncio.run(_run(Req("/kalshi")))
    assert r.status == 404


def test_wrong_key_is_refused(monkeypatch):
    monkeypatch.setenv("ACCESS_KEY", "correct-horse")
    r = asyncio.run(_run(Req("/kalshi", query={"k": "wrong"})))
    assert r.status == 404


def test_the_right_key_admits_the_owner_and_sets_cookies(monkeypatch):
    monkeypatch.setenv("ACCESS_KEY", "correct-horse")
    req = Req("/kalshi", query={"k": "correct-horse"})
    r = asyncio.run(_run(req))
    assert r.status == 200
    assert req["user"] == {"id": 1, "username": "owner", "is_admin": True}
    # both cookies, so the next request needs no ?k=
    assert webauth.SESSION_COOKIE in r.cookies_set
    assert r.cookies_set[webauth.ACCESS_COOKIE] == "correct-horse"


def test_the_key_cookie_alone_is_enough_on_later_requests(monkeypatch):
    monkeypatch.setenv("ACCESS_KEY", "correct-horse")
    req = Req("/mybets", cookies={webauth.ACCESS_COOKIE: "correct-horse"})
    assert asyncio.run(_run(req)).status == 200


def test_web_token_still_works_as_the_key(monkeypatch):
    """Deploys already setting WEB_TOKEN keep working without a new variable."""
    monkeypatch.setenv("WEB_TOKEN", "legacy")
    assert asyncio.run(_run(Req("/live", query={"k": "legacy"}))).status == 200


def test_healthz_stays_open(monkeypatch):
    # Railway's health check must not need the key
    assert asyncio.run(_run(Req("/healthz"))).status == 200


def test_api_paths_are_refused_too(monkeypatch):
    r = asyncio.run(_run(Req("/api/events")))
    assert r.status == 404


def test_a_redirecting_handler_still_gets_its_cookies(monkeypatch):
    """Most POST handlers signal success by RAISING HTTPFound. Without cookie
    stamping on the raised response the redirect target would 404."""
    monkeypatch.setenv("ACCESS_KEY", "correct-horse")
    req = Req("/bet", query={"k": "correct-horse"})

    async def raiser(r):
        raise W.web.HTTPFound("/mybets")

    with pytest.raises(W.web.HTTPException) as ei:
        asyncio.run(W.auth_guard(req, raiser))
    assert webauth.SESSION_COOKIE in ei.value.cookies


# --- the shell no longer offers a way to sign out ------------------------

def test_nav_has_no_sign_out_and_no_logout_link():
    """There is no session to end — access is a key held in the browser, so a
    'Sign out' link would be a dead end pointing at a route that no longer
    exists."""
    for user in ({"id": 1, "username": "owner", "is_admin": True},
                 {"id": 2, "username": "guest", "is_admin": False},
                 None):
        html = W.page("T", "live", "<p>body</p>", user=user)
        assert "Sign out" not in html
        assert "/logout" not in html


def test_only_admins_see_the_users_and_kalshi_links():
    admin = W.page("T", "live", "<p>b</p>",
                   user={"id": 1, "username": "owner", "is_admin": True})
    plain = W.page("T", "live", "<p>b</p>",
                   user={"id": 2, "username": "guest", "is_admin": False})
    assert "/admin/users" in admin and "/kalshi" in admin
    assert "/admin/users" not in plain and "/kalshi" not in plain


def test_an_unset_key_refuses_even_a_plausible_value(monkeypatch):
    """Guards the exact inversion that would publish a real trading account:
    'no key configured' must mean 'nobody gets in', never 'everybody does'."""
    monkeypatch.delenv("ACCESS_KEY", raising=False)
    monkeypatch.delenv("WEB_TOKEN", raising=False)
    assert webauth.access_key() == ""
    assert webauth.access_key_ok("anything") is False
    assert webauth.access_key_ok("") is False
    assert webauth.access_key_ok(None) is False


# --- lockout diagnostics ------------------------------------------------

def test_healthz_reports_whether_a_key_is_configured(monkeypatch):
    """Every other route 404s silently, which is right for a stranger but left
    the owner unable to tell "the env var never reached this service" from "I
    typed it wrong". This reports only WHETHER, never the value, and offers no
    way to test a candidate key."""
    import asyncio
    import json

    monkeypatch.delenv("ACCESS_KEY", raising=False)
    monkeypatch.delenv("WEB_TOKEN", raising=False)
    r = asyncio.run(W.healthz(Req("/healthz")))
    assert json.loads(r.text) == {"ok": True, "auth": "unconfigured"}

    monkeypatch.setenv("ACCESS_KEY", "correct-horse")
    r = asyncio.run(W.healthz(Req("/healthz")))
    body = json.loads(r.text)
    assert body["auth"] == "configured"
    # the key itself must never appear
    assert "correct-horse" not in r.text


def test_healthz_never_becomes_a_key_oracle(monkeypatch):
    """It must not accept a candidate key and say whether it matched — that
    would turn liveness into a brute-force endpoint."""
    import asyncio
    import json

    monkeypatch.setenv("ACCESS_KEY", "correct-horse")
    right = json.loads(asyncio.run(W.healthz(Req("/healthz", query={"k": "correct-horse"}))).text)
    wrong = json.loads(asyncio.run(W.healthz(Req("/healthz", query={"k": "nope"}))).text)
    assert right == wrong          # indistinguishable
