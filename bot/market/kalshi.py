"""Kalshi client — STRICTLY READ-ONLY (CLAUDE.md rule 1).

Only GET requests are possible through this client; there is no code path that
creates, amends, or cancels orders, and none may ever be added.

Docs read 2026-07-19, portfolio section re-read 2026-08-13 (docs.kalshi.com):
- REST base: https://external-api.kalshi.com/trade-api/v2 (public market data
  needs no auth). Portfolio access is limited to the four READ-ONLY GETs
  permitted by CLAUDE.md rule 1's narrow exception — fills, settlements,
  positions, balance — which exist only to show the owner their own trading
  history. No order endpoint is reachable from this client; the order-placement
  routes are not even in the portfolio section of the API.
- WS: wss://external-api-ws.kalshi.com/trade-api/ws/v2 — auth REQUIRED at
  handshake; public channels: ticker, trade, market_lifecycle_v2
- Auth: KALSHI-ACCESS-KEY / -TIMESTAMP (ms) / -SIGNATURE headers; signature =
  base64(RSA-PSS-SHA256(timestamp + METHOD + path-without-query))
- Prices arrive as dollar strings ("0.5400"); converted to integer cents here.
- Tennis match-winner series: KXATPMATCH, KXATPCHALLENGERMATCH, KXITFMATCH,
  KXITFWMATCH, KXWTAMATCH, KXWTACHALLENGERMATCH (+ legacy KXWTAGAME). Two markets per event (one per
  player, YES = that player wins). Milestones (type tennis_tournament_singles)
  carry the DELAYED score via GET /live_data/milestone/{id}:
  competitor1/2_overall_score = sets won.
"""
from __future__ import annotations

import base64
import time
from datetime import datetime, timezone

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from bot.config import settings
from bot.log import get_logger

log = get_logger("market.kalshi")

TENNIS_SERIES = {
    # series ticker -> tour hint for the player matcher
    "KXATPMATCH": "atp",
    "KXATPCHALLENGERMATCH": "atp",
    "KXITFMATCH": "atp",  # ITF men live under the ATP player universe
    "KXWTAMATCH": "wta",
    "KXWTACHALLENGERMATCH": "wta",  # WTA 125 tour
    "KXWTAGAME": "wta",  # legacy naming: "game" = match
    "KXITFWMATCH": "wta",
}


def dollars_to_cents(v) -> int | None:
    if v in (None, ""):
        return None
    try:
        return int(round(float(v) * 100))
    except (TypeError, ValueError):
        return None


class KalshiAuth:
    def __init__(self, key_id: str, private_key_b64: str):
        self.key_id = key_id
        pem = base64.b64decode(private_key_b64)
        self.private_key = serialization.load_pem_private_key(pem, password=None)

    def headers(self, method: str, path: str) -> dict[str, str]:
        ts = str(int(time.time() * 1000))
        message = f"{ts}{method}{path.split('?')[0]}".encode()
        sig = self.private_key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
        }


class KalshiClient:
    """READ-ONLY. The single _get method is the only way to reach the API."""

    def __init__(self) -> None:
        cfg = settings()
        self.base = cfg.kalshi_api_base.rstrip("/")
        self.api_root_path = "/" + "/".join(self.base.split("/")[3:])
        self.auth: KalshiAuth | None = None
        if cfg.kalshi_api_key_id and cfg.kalshi_private_key_b64:
            self.auth = KalshiAuth(cfg.kalshi_api_key_id, cfg.kalshi_private_key_b64)
        self.http = httpx.Client(base_url=self.base, timeout=30)

    def _get(self, path: str, _tries: int = 4, **params) -> dict:
        """Authenticated GET with retry/backoff. Retries transient failures
        (timeouts, transport errors, 429, 5xx) with exponential backoff, honoring
        a 429 Retry-After. Auth headers are re-signed each attempt so the
        timestamp never goes stale across a backoff sleep."""
        for attempt in range(_tries):
            headers = (self.auth.headers("GET", f"{self.api_root_path}{path}")
                       if self.auth else {})
            try:
                r = self.http.get(path, params=params, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                if attempt == _tries - 1:
                    raise
                log.warning("kalshi GET transient error — retrying",
                            path=path, attempt=attempt + 1, error=str(e))
                time.sleep(min(8.0, 0.5 * 2 ** attempt))
                continue
            if (r.status_code == 429 or r.status_code >= 500) and attempt < _tries - 1:
                ra = r.headers.get("Retry-After")
                try:
                    delay = float(ra) if ra else min(8.0, 0.5 * 2 ** attempt)
                except ValueError:
                    delay = min(8.0, 0.5 * 2 ** attempt)
                log.warning("kalshi GET throttled/5xx — backing off", path=path,
                            status=r.status_code, delay=round(delay, 2))
                time.sleep(delay)
                continue
            r.raise_for_status()
            return r.json()

    def ws_headers(self) -> dict[str, str]:
        """Auth headers for the websocket handshake (required by Kalshi)."""
        if not self.auth:
            raise RuntimeError("Kalshi websocket requires KALSHI_API_KEY_ID / "
                               "KALSHI_PRIVATE_KEY_B64 — set them in the environment")
        cfg = settings()
        ws_path = "/" + "/".join(cfg.kalshi_ws_url.split("/")[3:])
        return self.auth.headers("GET", ws_path)

    def verify_auth(self) -> bool:
        """One authenticated GET (CLAUDE.md prerequisite). Uses a harmless
        read-only account endpoint."""
        if not self.auth:
            return False
        me = self._get("/api_keys")
        log.info("kalshi auth verified", keys=len(me.get("api_keys", [])))
        return True

    # ---------- market data (public) ----------

    def markets(self, series_ticker: str, status: str = "open") -> list[dict]:
        out, cursor = [], None
        while True:
            params = {"series_ticker": series_ticker, "status": status, "limit": 200}
            if cursor:
                params["cursor"] = cursor
            d = self._get("/markets", **params)
            out.extend(d.get("markets", []))
            cursor = d.get("cursor")
            if not cursor:
                return out

    def market(self, ticker: str) -> dict:
        return self._get(f"/markets/{ticker}").get("market", {})

    def event(self, event_ticker: str) -> dict:
        return self._get(f"/events/{event_ticker}", with_nested_markets=False).get("event", {})

    def trades(self, ticker: str, since_ts: int | None = None) -> list[dict]:
        params = {"ticker": ticker, "limit": 100}
        if since_ts:
            params["min_ts"] = since_ts
        return self._get("/markets/trades", **params).get("trades", [])

    # ---------- portfolio (READ-ONLY; CLAUDE.md rule 1 narrow exception) ----------
    # These four GETs exist only so the owner can see their own trading history.
    # They place no orders. Do not add anything here that is not a GET.

    def _paged(self, path: str, key: str, limit: int = 1000,
               max_pages: int = 200, **params) -> list[dict]:
        """Cursor-paginate a portfolio GET. `max_pages` is a runaway guard, not a
        cap we expect to hit — the account's full history is ~11 pages at 1000."""
        out, cursor = [], None
        for _ in range(max_pages):
            p = dict(params, limit=limit)
            if cursor:
                p["cursor"] = cursor
            d = self._get(path, **p)
            rows = d.get(key) or []
            out.extend(rows)
            cursor = d.get("cursor")
            if not cursor or not rows:
                break
        return out

    def fills(self, min_ts: int | None = None) -> list[dict]:
        """Every execution on the account, newest first. `min_ts` (unix seconds)
        makes a re-sync incremental instead of refetching all ~10.5k."""
        return self._paged("/portfolio/fills", "fills",
                           **({"min_ts": min_ts} if min_ts else {}))

    def settlements(self, min_ts: int | None = None) -> list[dict]:
        """Settled markets with their outcome and revenue."""
        return self._paged("/portfolio/settlements", "settlements",
                           **({"min_ts": min_ts} if min_ts else {}))

    def positions(self) -> list[dict]:
        """Currently-held positions (market_positions)."""
        return self._paged("/portfolio/positions", "market_positions", limit=1000)

    def balance(self) -> dict:
        return self._get("/portfolio/balance")

    # Funding movements, used ONLY to reconcile lifetime P&L via
    # (balance + portfolio_value) - net funded. Kalshi's fills history does not
    # reach back far enough to derive that from trades.
    # NB: these two reject limit>500 with a 400 (verified), unlike fills.

    def deposits(self) -> list[dict]:
        return self._paged("/portfolio/deposits", "deposits", limit=200)

    def withdrawals(self) -> list[dict]:
        return self._paged("/portfolio/withdrawals", "withdrawals", limit=200)

    # ---------- milestones / delayed score (public) ----------

    def milestones_for_event(self, event_ticker: str) -> list[dict]:
        d = self._get("/milestones", related_event_ticker=event_ticker, limit=10)
        return d.get("milestones", [])

    def tennis_milestone_statuses(self, since_hours: int = 36) -> dict[str, str]:
        """event_ticker -> live status ('live'/'not_started'/'P'…) for tennis
        matches. The authoritative what-is-actually-live signal (scheduled times
        drift, this doesn't). The feed spans every recent AND upcoming tennis
        singles milestone worldwide (ITF alone is hundreds/day), so we MUST
        page through all of it — a low page cap silently drops currently-live
        matches to no-status, hiding them from the live board."""
        from datetime import timedelta

        MAX_PAGES = 40  # 40 * 200 = 8k milestones; a hard backstop, not a target
        start = (utcnow() - timedelta(hours=since_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        out: dict[str, str] = {}
        cursor = None
        pages = 0
        for pages in range(1, MAX_PAGES + 1):
            params = {"type": "tennis_tournament_singles",
                      "minimum_start_date": start, "limit": 200}
            if cursor:
                params["cursor"] = cursor
            d = self._get("/milestones", **params)
            for m in d.get("milestones", []):
                det = m.get("details") or {}
                ev = det.get("main_game_event_ticker")
                status = det.get("status")
                if ev and status:
                    out[ev] = status
            cursor = d.get("cursor")
            if not cursor:
                break
        else:
            log.warning("milestone status feed hit page cap — coverage may be "
                        "truncated (live matches could read as no-status)",
                        pages=MAX_PAGES, events=len(out))
        return out

    def live_data(self, milestone_id: str) -> dict:
        return self._get(f"/live_data/milestone/{milestone_id}").get("live_data", {})

    @staticmethod
    def sets_from_live_data(payload: dict) -> tuple[int, int] | None:
        """(sets_competitor1, sets_competitor2) from a live_data payload."""
        det = payload.get("details") or {}
        a, b = det.get("competitor1_overall_score"), det.get("competitor2_overall_score")
        if a is None or b is None:
            return None
        return int(a), int(b)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
