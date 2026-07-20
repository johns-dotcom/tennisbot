"""Discord webhook delivery: prose + compact data block embed."""
from __future__ import annotations

import httpx

from bot.advisory.facts import FactBlock
from bot.config import settings
from bot.log import get_logger

log = get_logger("advisory.discord")

PROBATION_PREFIX = "**[PROBATION — UNVERIFIED STATE]**\n"


def push_advisory(block: FactBlock, prose: str) -> bool:
    cfg = settings()
    if not cfg.discord_webhook_url:
        log.warning("DISCORD_WEBHOOK_URL not set — advisory not delivered")
        return False
    content = (PROBATION_PREFIX if block.probation and not block.state_confirmed else "")
    embed = {
        "title": f"{block.recommended_name} vs {block.opponent_name}",
        "description": content + prose,
        "color": 0xE67E22 if block.probation and not block.state_confirmed else 0x2ECC71,
        "fields": [
            {"name": "Model", "value": f"{block.model_prob:.0%}", "inline": True},
            {"name": "Price", "value": f"{block.executable_price_cents}¢ "
                                       f"({block.implied_prob:.0%})", "inline": True},
            {"name": "Edge", "value": f"{block.edge * 100:+.1f}%", "inline": True},
            {"name": "State", "value": f"{block.state_key} "
             f"({'confirmed' if block.state_confirmed else f'{block.state_confidence:.0%} est.'})",
             "inline": True},
            {"name": "Volume", "value": str(block.volume or "—"), "inline": True},
            {"name": "Market", "value": block.market_ticker, "inline": True},
        ],
    }
    try:
        r = httpx.post(cfg.discord_webhook_url, json={"embeds": [embed]}, timeout=15)
        r.raise_for_status()
        return True
    except httpx.HTTPError as e:
        log.error("discord push failed", error=str(e))
        return False
