"""Phone alerts via a Discord webhook — advisory notifications only.

Enabled by the DISCORD_WEBHOOK_URL env var. When unset, every call no-ops so
the engine and worker run exactly as before. This sends NOTIFICATIONS only; it
never touches an order (CLAUDE.md rule 1).

The POST is dispatched on a short-lived daemon thread: the engine's on_quote /
on_confirmed_state hooks run directly inside the worker's async event loop, so a
synchronous network call here would stall it. Fire-and-forget keeps the loop
responsive; a failed alert is logged and swallowed, never raised.
"""
from __future__ import annotations

import os
import threading

import httpx

from bot.log import get_logger

log = get_logger("notify")

# embed accent colors (decimal): amber for armed, red for pushed, blurple info
_COLORS = {"armed": 0xE0A800, "pushed": 0xE2483D, "info": 0x5865F2}


def discord_enabled() -> bool:
    return bool(os.environ.get("DISCORD_WEBHOOK_URL"))


def _post(hook: str, payload: dict, kind: str) -> None:
    try:
        r = httpx.post(hook, json=payload, timeout=8.0)
        if r.status_code >= 300:
            log.warning("discord notify non-2xx", status=r.status_code, kind=kind)
    except Exception as e:  # a dead webhook must never crash the worker
        log.warning("discord notify failed", kind=kind, error=str(e))


def _dispatch(hook: str, payloads: list[dict], kind: str) -> None:
    """POST each payload IN ORDER on one short-lived daemon thread. Sequential
    (not one thread per message) so the @everyone headline always lands before
    the no-ping analysis, and the worker's event loop is never blocked."""
    def run() -> None:
        for p in payloads:
            _post(hook, p, kind)

    threading.Thread(target=run, daemon=True).start()


def notify_signal(*, match: str, pick: str, confidence: str, analysis: str,
                  fields: list[tuple[str, str]] | None = None,
                  link: str | None = None, kind: str = "armed") -> bool:
    """Two-message signal alert:
      1. @everyone ping — just the model's PICK and CONFIDENCE (this is what the
         phone push previews, so it stays short and scannable).
      2. a SEPARATE message with NO ping — the analysis and reasoning, so the
         detail doesn't re-ping everyone.
    Returns True if a webhook is configured and the messages were dispatched."""
    hook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not hook:
        return False
    # message 1 — the ping. Short: pick + confidence only.
    ping = {
        "content": (f"@everyone\n**◎ SIGNAL · {match}**\n"
                    f"Model's pick: **{pick}**\n"
                    f"Model's confidence: **{confidence}**").strip()[:1990],
        "allowed_mentions": {"parse": ["everyone"]},
    }
    # message 2 — the analysis. NO mention ping (allowed_mentions empty).
    embed: dict = {
        "title": f"Analysis · {match}"[:256],
        "color": _COLORS.get(kind, _COLORS["info"]),
    }
    if fields:
        embed["fields"] = [{"name": n[:256], "value": (v or "—")[:1024],
                            "inline": True} for n, v in fields[:12]]
    if link:
        embed["url"] = link
    detail = {
        "content": (analysis or "").strip()[:1990],
        "embeds": [embed],
        "allowed_mentions": {"parse": []},  # explicitly ping no one
    }
    _dispatch(hook, [ping, detail], kind)
    return True


def notify(kind: str, title: str, message: str,
           fields: list[tuple[str, str]] | None = None,
           link: str | None = None) -> bool:
    """Single-message alert (no @everyone). General helper; the scenario signal
    uses notify_signal for its two-message ping+analysis format."""
    hook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not hook:
        return False
    embed: dict = {
        "title": title[:256],
        "description": (message or "")[:2048],
        "color": _COLORS.get(kind, _COLORS["info"]),
    }
    if fields:
        embed["fields"] = [{"name": n[:256], "value": (v or "—")[:1024],
                            "inline": True} for n, v in fields[:12]]
    if link:
        embed["url"] = link
    payload = {
        "content": f"**{title}**\n{message or ''}".strip()[:1990],
        "embeds": [embed],
        "allowed_mentions": {"parse": []},
    }
    _dispatch(hook, [payload], kind)
    return True
