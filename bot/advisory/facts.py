"""Stage 1 of advisory generation: the deterministic fact block.

The stats engine produces the top 4-6 facts ranked by salience
(stat magnitude × recency × relevance to the current match state; deciding-set
facts boosted when the match is one set from a decider or in one), plus model
prob, implied prob, edge, executable price, volume, inferred state + confidence.

Every number that may appear in prose is collected into `allowed_numbers` — the
numeric validator rejects any rendered number not in this set (CLAUDE.md rule 3).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from bot.prob.model import MatchState
from bot.stats.fallback import Stat
from bot.stats.profile import PlayerProfile, compute_matchup

MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


@dataclass
class Fact:
    key: str
    salience: float
    data: dict  # numbers + preformatted strings the renderer may use
    sentence_hint: str  # deterministic phrasing used by the template fallback


@dataclass
class FactBlock:
    market_ticker: str
    recommended_name: str
    opponent_name: str
    recommended_side: str  # 'yes' | 'no'
    facts: list[Fact]
    model_prob: float
    model_confidence: float
    implied_prob: float
    edge: float
    executable_price_cents: int
    volume: int | None
    state_key: str
    state_confidence: float
    state_confirmed: bool
    best_of: int
    probation: bool
    allowed_numbers: set = field(default_factory=set)

    def to_json(self) -> dict:
        return {
            "market_ticker": self.market_ticker,
            "recommended": self.recommended_name, "opponent": self.opponent_name,
            "side": self.recommended_side,
            "facts": [{"key": f.key, "salience": round(f.salience, 3), "data": f.data,
                       "hint": f.sentence_hint} for f in self.facts],
            "model_prob": self.model_prob, "model_confidence": self.model_confidence,
            "implied_prob": self.implied_prob, "edge": self.edge,
            "price_cents": self.executable_price_cents, "volume": self.volume,
            "state": self.state_key, "state_confidence": self.state_confidence,
            "state_confirmed": self.state_confirmed, "best_of": self.best_of,
            "probation": self.probation,
        }


def _add_numbers(allowed: set, *values) -> None:
    for v in values:
        if v is None:
            continue
        if isinstance(v, float):
            allowed.add(f"{v:.1f}".rstrip("0").rstrip("."))
            allowed.add(str(int(round(v))))
        else:
            allowed.add(str(int(v)))


def _pct(stat: Stat) -> int | None:
    return int(round(stat.value * 100)) if stat.value is not None else None


def _decider_label(best_of: int) -> str:
    return f"set {best_of}"


def _window_phrase(window: str) -> str:
    """Digit-free description of a stat window (digits would trip the validator)."""
    return {"last365": "past year", "last180": "past six months",
            "last60": "past two months", "career": "career",
            "prior": "prior", "h2h": "head-to-head"}.get(window, "recent")


def build_facts(profile_a: PlayerProfile, profile_b: PlayerProfile,
                history_a: list, history_b: list,
                state: MatchState, as_of: date, max_facts: int = 6) -> list[Fact]:
    """Candidate facts scored by salience. Player A is the candidate side."""
    facts: list[Fact] = []
    a, b = profile_a, profile_b
    name_a, name_b = a.player_name.split()[-1], b.player_name.split()[-1]
    dec_label = _decider_label(state.best_of)

    # relevance: how close are we to (or in) a decider?
    sets_played = state.sets_a + state.sets_b
    need = state.best_of // 2 + 1
    in_decider = state.sets_a == need - 1 and state.sets_b == need - 1
    decider_boost = 2.5 if in_decider else (1.6 if sets_played >= 1 else 1.0)

    def add(key, salience, data, hint):
        facts.append(Fact(key=key, salience=salience, data=data, sentence_hint=hint))

    # --- deciding-set facts (the core differentiator) ---
    for who, prof, nm in (("a", a, name_a), ("b", b, name_b)):
        d = prof.deciding
        if not d.best.is_omitted and d.best.n >= 3:
            mag = abs(d.best.value - 0.5) * 2
            wp = _window_phrase(d.best.window)
            add(f"decider_record_{who}", (0.5 + mag) * decider_boost,
                {"wins": d.best.wins, "losses": d.best.losses,
                 "pct": _pct(d.best), "window": wp},
                f"{nm} is {d.best.wins}-{d.best.losses} in {dec_label}s ({wp})")
        if abs(d.streak) >= 3:
            add(f"decider_streak_{who}", 0.8 * decider_boost,
                {"streak": abs(d.streak), "direction": "won" if d.streak > 0 else "lost"},
                f"{nm} has {'won' if d.streak > 0 else 'lost'} {abs(d.streak)} straight {dec_label}s")
        if d.days_since_decider_win is not None and d.days_since_decider_win > 90 \
                and d.streak < 0:
            last_win_month = MONTHS[(as_of.month - 1 - (d.days_since_decider_win // 30)) % 12]
            add(f"decider_drought_{who}", 0.7 * decider_boost,
                {"days": d.days_since_decider_win, "month": last_win_month},
                f"{nm} has not won a {dec_label} since {last_win_month}")
        sk = d.skunk_share_of_wins_365
        if not sk.is_omitted and sk.value is not None and sk.value >= 0.8 and sk.n >= 5:
            add(f"skunk_share_{who}", 0.55 * decider_boost,
                {"pct": _pct(sk), "wins": sk.n},
                f"{_pct(sk)}% of {nm}'s last-year wins were straight-sets — thin {dec_label} experience")

    dec_a, dec_b = a.deciding.best, b.deciding.best
    if dec_a.value is not None and dec_b.value is not None:
        diff = dec_a.value - dec_b.value
        if abs(diff) >= 0.10:
            add("decider_differential", (0.6 + abs(diff)) * decider_boost,
                {"diff_pct": int(round(abs(diff) * 100)),
                 "better": name_a if diff > 0 else name_b},
                f"{name_a if diff > 0 else name_b}'s {dec_label} win rate is "
                f"{int(round(abs(diff) * 100))}% higher")

    # --- form ---
    for who, prof, nm in (("a", a, name_a), ("b", b, name_b)):
        f10 = prof.form.last10
        if f10.n >= 8 and f10.value is not None and abs(f10.value - 0.5) >= 0.2:
            add(f"form10_{who}", 0.4 + abs(f10.value - 0.5),
                {"wins": f10.wins, "n": f10.n},
                f"{nm} has won {f10.wins} of their last {f10.n} matches")
        if abs(prof.form.streak) >= 4:
            add(f"streak_{who}", 0.5 + min(0.4, abs(prof.form.streak) * 0.05),
                {"streak": abs(prof.form.streak),
                 "direction": "won" if prof.form.streak > 0 else "lost"},
                f"{nm} has {'won' if prof.form.streak > 0 else 'lost'} "
                f"{abs(prof.form.streak)} matches in a row")
        t = prof.trajectory
        if t.delta is not None and abs(t.delta) >= 0.2 and t.last60.n >= 5:
            add(f"trajectory_{who}", 0.45 + abs(t.delta) / 2,
                {"pct60": _pct(t.last60), "pct180": _pct(t.last180),
                 "direction": "better" if t.delta > 0 else "worse"},
                f"{nm} has been much {'better' if t.delta > 0 else 'worse'} in the "
                f"past two months ({_pct(t.last60)}% vs {_pct(t.last180)}% prior)")

    # --- matchup ---
    mu = compute_matchup(history_a, history_b, profile_b.player_id,
                         profile_a.player_id, as_of, None)
    if mu.h2h.n >= 2:
        add("h2h", 0.6 + abs((mu.h2h.value or 0.5) - 0.5),
            {"wins": mu.h2h.wins, "losses": mu.h2h.losses,
             "sets_a": mu.h2h_sets[0], "sets_b": mu.h2h_sets[1]},
            f"{name_a} leads the head-to-head {mu.h2h.wins}-{mu.h2h.losses}"
            if (mu.h2h.value or 0) > 0.5 else
            f"{name_b} leads the head-to-head {mu.h2h.losses}-{mu.h2h.wins}")
    elif not mu.common_opponents.is_omitted and mu.common_opponent_count >= 3:
        add("common_opponents", 0.5,
            {"wins_a": mu.common_opponents.wins, "losses_a": mu.common_opponents.losses,
             "wins_b": mu.common_opponents_b.wins, "losses_b": mu.common_opponents_b.losses,
             "n_common": mu.common_opponent_count},
            f"Against {mu.common_opponent_count} common opponents: {name_a} "
            f"{mu.common_opponents.wins}-{mu.common_opponents.losses}, {name_b} "
            f"{mu.common_opponents_b.wins}-{mu.common_opponents_b.losses}")

    facts.sort(key=lambda f: f.salience, reverse=True)
    return facts[:max_facts]


def build_fact_block(*, market_ticker: str, name_a: str, name_b: str, side: str,
                     facts: list[Fact], model_prob: float, model_confidence: float,
                     price_cents: int, volume: int | None, state: MatchState,
                     state_confidence: float, state_confirmed: bool,
                     probation: bool) -> FactBlock:
    implied = price_cents / 100.0
    edge = model_prob - implied
    block = FactBlock(
        market_ticker=market_ticker, recommended_name=name_a, opponent_name=name_b,
        recommended_side=side, facts=facts, model_prob=round(model_prob, 3),
        model_confidence=round(model_confidence, 2), implied_prob=round(implied, 3),
        edge=round(edge, 3), executable_price_cents=price_cents, volume=volume,
        state_key=state.key, state_confidence=round(state_confidence, 2),
        state_confirmed=state_confirmed, best_of=state.best_of, probation=probation)

    allowed = block.allowed_numbers
    for f in facts:
        _add_numbers(allowed, *[v for v in f.data.values() if isinstance(v, (int, float))])
    _add_numbers(allowed, price_cents, volume, state.best_of,
                 int(round(model_prob * 100)), int(round(implied * 100)),
                 int(round(state_confidence * 100)))
    allowed.add(f"{abs(edge) * 100:.1f}".rstrip("0").rstrip("."))
    _add_numbers(allowed, int(round(abs(edge) * 100)))
    for part in state.key.split("-"):
        allowed.add(part)
    for n in range(1, state.best_of + 1):
        allowed.add(str(n))  # "set 3", "2-0" style references
    _add_numbers(allowed, 0)
    return block
