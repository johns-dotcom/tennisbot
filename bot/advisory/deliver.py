"""Advisory delivery: persistent audit row (written by the engine) + a
structured stdout log line. No external delivery channel — by user decision
(2026-07-19) advisories live in the database and the logs only.
"""
from __future__ import annotations

from bot.advisory.facts import FactBlock
from bot.log import get_logger

log = get_logger("advisory.deliver")

PROBATION_PREFIX = "[PROBATION — UNVERIFIED STATE] "


def deliver_advisory(block: FactBlock, prose: str) -> bool:
    prefix = PROBATION_PREFIX if block.probation and not block.state_confirmed else ""
    log.info("ADVISORY",
             advisory_text=prefix + prose,
             market=block.market_ticker,
             recommended=block.recommended_name,
             side=block.recommended_side,
             price_cents=block.executable_price_cents,
             model_prob=block.model_prob,
             implied_prob=block.implied_prob,
             edge=block.edge,
             volume=block.volume,
             state=block.state_key,
             state_confidence=block.state_confidence,
             state_confirmed=block.state_confirmed,
             probation=block.probation and not block.state_confirmed)
    return True
