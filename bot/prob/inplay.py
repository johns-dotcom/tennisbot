"""Point-level in-play win probability — the hierarchical tennis model.

Where the proven edge lives: markets on lower-tier events reprice slowly during a
match, so a model that computes P(win | exact live score) from serve-point
probabilities and updates every point can diverge from a laggy line.

Structure (standard Barnett–Clarke / O'Malley recursions):
    point → game → set → match
Inputs are each player's probability of winning a point ON THEIR OWN SERVE
(spwA, spwB). We ANCHOR those to the base model: at 0-0 the hierarchical match
prob is solved to equal the Elo prematch number, so this model agrees with the
baseline before a ball is struck and only *moves* as the score does. Serve stats
can later refine the split; the anchor keeps it honest in the meantime.

Pure functions, memoized. No market data (CLAUDE.md rule 2) — score state only.
"""
from __future__ import annotations

from functools import lru_cache

# tour-average probability of winning a point on serve (ATP ~0.64, WTA ~0.56);
# 0.62 is a neutral default used only as the anchoring base — the level cancels
# in the difference that reproduces the prematch prob.
BASE_SPW = 0.62


def game_win_prob(p: float) -> float:
    """P(server wins a service game) given p = P(win a point on serve)."""
    q = 1.0 - p
    win = p ** 4 * (1 + 4 * q + 10 * q * q)      # to 0, 15, 30
    deuce = 20 * p ** 3 * q ** 3                  # reach 40-40
    win += deuce * (p * p / (p * p + q * q))      # win from deuce
    return win


@lru_cache(maxsize=200_000)
def _game_from_points(p: float, s: int, r: int) -> float:
    """P(server wins THIS game) from an in-progress point score (s server points,
    r returner points, each 0..3 with the deuce region handled)."""
    if s >= 4 and s - r >= 2:
        return 1.0
    if r >= 4 and r - s >= 2:
        return 0.0
    if s >= 3 and r >= 3:
        q = 1 - p
        dprob = p * p / (p * p + q * q)           # win from deuce
        if s == r:
            return dprob
        if s > r:                                  # advantage server
            return p + q * dprob
        return p * dprob                           # advantage returner
    return p * _game_from_points(p, s + 1, r) + (1 - p) * _game_from_points(p, s, r + 1)


@lru_cache(maxsize=500_000)
def _tiebreak(pa: float, pb: float, sa: int, sb: int) -> float:
    """P(A wins the tiebreak) from points (sa, sb), A serving the first point.
    Serve follows the 1-2-2 rotation: A serves point 0, then serve switches every
    two points — so A serves point t iff ((t+1)//2) is even."""
    if sa >= 7 and sa - sb >= 2:
        return 1.0
    if sb >= 7 and sb - sa >= 2:
        return 0.0
    if sa >= 6 and sb >= 6 and sa == sb:           # deuce: next two points, one each
        aw = pa * (1 - pb)                         # A holds then breaks
        bw = (1 - pa) * pb
        return aw / (aw + bw)
    t = sa + sb
    a_serves = ((t + 1) // 2) % 2 == 0
    p_pt = pa if a_serves else (1 - pb)            # P(A wins this point)
    return (p_pt * _tiebreak(pa, pb, sa + 1, sb)
            + (1 - p_pt) * _tiebreak(pa, pb, sa, sb + 1))


@lru_cache(maxsize=500_000)
def set_win_prob(pa: float, pb: float, ga: int, gb: int, a_serving: bool) -> float:
    """P(A wins the set) from the START of a game at games (ga, gb), A serving?"""
    if ga >= 6 and ga - gb >= 2:
        return 1.0
    if gb >= 6 and gb - ga >= 2:
        return 0.0
    if ga == 7:                                    # 7-5
        return 1.0
    if gb == 7:
        return 0.0
    if ga == 6 and gb == 6:                        # tiebreak (A serves first pt)
        return _tiebreak(pa, pb, 0, 0)
    hold = game_win_prob(pa if a_serving else pb)  # server wins this game
    a_wins_game = hold if a_serving else (1 - hold)
    return (a_wins_game * set_win_prob(pa, pb, ga + 1, gb, not a_serving)
            + (1 - a_wins_game) * set_win_prob(pa, pb, ga, gb + 1, not a_serving))


def _sets(na: int, nb: int, s: float) -> float:
    """P(A wins the match) needing na more sets vs nb, each set won w.p. s."""
    if na <= 0:
        return 1.0
    if nb <= 0:
        return 0.0
    return s * _sets(na - 1, nb, s) + (1 - s) * _sets(na, nb - 1, s)


def match_win_prob(pa: float, pb: float, *, best_of: int,
                   sets_a: int, sets_b: int, games_a: int, games_b: int,
                   pts_a: int, pts_b: int, a_serving: bool) -> float:
    """P(A wins the match) from the full live score, given serve-point probs."""
    # resolve the current (possibly in-progress) set
    if pts_a or pts_b:
        sp = pa if a_serving else pb
        s_pts, r_pts = (pts_a, pts_b) if a_serving else (pts_b, pts_a)
        server_holds = _game_from_points(sp, s_pts, r_pts)
        a_wins_game = server_holds if a_serving else (1 - server_holds)
        cur = (a_wins_game * set_win_prob(pa, pb, games_a + 1, games_b, not a_serving)
               + (1 - a_wins_game) * set_win_prob(pa, pb, games_a, games_b + 1, not a_serving))
    else:
        cur = set_win_prob(pa, pb, games_a, games_b, a_serving)
    need = best_of // 2 + 1
    na, nb = need - sets_a, need - sets_b
    fresh = set_win_prob(pa, pb, 0, 0, True)       # a fresh set (server edge ~cancels)
    return cur * _sets(na - 1, nb, fresh) + (1 - cur) * _sets(na, nb - 1, fresh)


def anchor_serve_gap(prematch_a: float, best_of: int, base: float = BASE_SPW,
                      iters: int = 44) -> float:
    """Serve-point-prob gap d ≥ 0 such that the STRONGER server (spw = base+d)
    beats the weaker (base-d) with probability max(prematch_a, 1-prematch_a) at
    0-0. Monotone in d → bisection. Sign/side is assigned by inplay_win_prob."""
    p_strong = max(prematch_a, 1 - prematch_a)
    if p_strong <= 0.5 + 1e-9:
        return 0.0
    lo, hi = 0.0, min(base, 1 - base) - 1e-3
    for _ in range(iters):
        d = (lo + hi) / 2
        p = match_win_prob(base + d, base - d, best_of=best_of, sets_a=0, sets_b=0,
                           games_a=0, games_b=0, pts_a=0, pts_b=0, a_serving=True)
        if p < p_strong:
            lo = d
        else:
            hi = d
    return (lo + hi) / 2


def inplay_win_prob(prematch_a: float, *, best_of: int, sets_a: int, sets_b: int,
                    games_a: int, games_b: int, pts_a: int = 0, pts_b: int = 0,
                    a_serving: bool = True, base: float = BASE_SPW) -> float:
    """Live P(A wins the match) from the current score, anchored so that at 0-0
    it equals `prematch_a` (the Elo number) and then evolves with the score."""
    d = anchor_serve_gap(prematch_a, best_of, base)
    pa, pb = (base + d, base - d) if prematch_a >= 0.5 else (base - d, base + d)
    return match_win_prob(pa, pb, best_of=best_of, sets_a=sets_a, sets_b=sets_b,
                          games_a=games_a, games_b=games_b, pts_a=pts_a,
                          pts_b=pts_b, a_serving=a_serving)
