"""The /kalshi page shows the owner's real balance, positions and P&L.

The Kalshi credentials are global env vars, so there is exactly ONE account —
the owner's — while the app supports several invited users. The admin gate is
the only thing standing between an invited account and the owner's book, so it
is tested rather than assumed."""
import asyncio

import pytest

from bot.web import kalshi_history, kalshi_sync, kalshi_tag


class Req:
    headers, query = {}, {}

    def __init__(self, user):
        self._u = user

    def get(self, k, default=None):
        return {"user": self._u, "session_cookie": ""}.get(k, default)

    async def post(self):
        return {}


ADMIN = {"id": 1, "username": "owner", "is_admin": True}
PLAIN = {"id": 2, "username": "guest", "is_admin": False}


@pytest.mark.parametrize("handler", [kalshi_history, kalshi_sync, kalshi_tag])
@pytest.mark.parametrize("user", [None, PLAIN], ids=["logged-out", "non-admin"])
def test_every_kalshi_route_is_admin_only(handler, user):
    r = asyncio.run(handler(Req(user)))
    assert r.status == 403
    assert "admin" in r.text.lower()


def test_the_gate_runs_before_any_kalshi_api_call(monkeypatch):
    """A non-admin must be refused without the account ever being read — the
    gate cannot sit behind the data fetch."""
    called = []
    monkeypatch.setattr("bot.web._account_summary",
                        lambda *a, **k: called.append(1) or ({}, ""))
    asyncio.run(kalshi_history(Req(PLAIN)))
    assert called == []
