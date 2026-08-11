"""Structural enforcement of CLAUDE.md rule 2: market price NEVER reaches the
probability engine.

Checks, over every module in bot/prob/:
  1. no import of bot.market (or any market/kalshi module)
  2. no identifier or attribute name mentioning price/odds/implied/orderbook/
     bid/ask/tick/kalshi/market
  3. WinProbabilityModel.predict's signature accepts exactly the allowed inputs

Do not weaken these assertions to make a change pass — restructure the change.
"""
import ast
import inspect
from pathlib import Path

import pytest

from bot.prob.model import WinProbabilityModel

PROB_DIR = Path(__file__).parent.parent / "bot" / "prob"
FORBIDDEN_IMPORT_PREFIXES = ("bot.market", "bot.advisory", "bot.engine")
FORBIDDEN_NAME_PARTS = ("price", "odds", "implied", "orderbook", "bid", "ask",
                        "tick", "kalshi", "market_")


def prob_modules():
    return sorted(PROB_DIR.glob("*.py"))


@pytest.mark.parametrize("path", prob_modules(), ids=lambda p: p.name)
def test_no_market_imports(path):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        for name in names:
            assert not name.startswith(FORBIDDEN_IMPORT_PREFIXES), \
                f"{path.name} imports {name} — market data must not reach bot/prob"


@pytest.mark.parametrize("path", prob_modules(), ids=lambda p: p.name)
def test_no_market_identifiers(path):
    tree = ast.parse(path.read_text())
    offenders = []
    for node in ast.walk(tree):
        name = None
        if isinstance(node, ast.Name):
            name = node.id
        elif isinstance(node, ast.Attribute):
            name = node.attr
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = node.name
        elif isinstance(node, ast.arg):
            name = node.arg
        if name and any(part in name.lower() for part in FORBIDDEN_NAME_PARTS):
            offenders.append(name)
    assert not offenders, f"{path.name} contains market-shaped identifiers: {offenders}"


def test_predict_signature_is_closed():
    params = list(inspect.signature(WinProbabilityModel.predict).parameters)
    # as_of is a DATE (prediction date, for recency-decayed confidence) — not a
    # market input. Any price/odds/quote param would still (correctly) fail here.
    assert params == ["self", "player_a", "player_b", "surface", "tier",
                      "match_state", "as_of"], \
        "predict() signature changed — price/market inputs must never be added"


def test_match_state_carries_only_sets():
    from bot.prob.model import MatchState

    fields = set(MatchState.__dataclass_fields__)
    assert fields == {"sets_a", "sets_b", "best_of"}, \
        "MatchState must stay a pure sets-won state — no market payload"
