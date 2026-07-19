"""Independent verification of play-script stats straight from raw Sackmann CSVs.

Deliberately shares NO code with bot/ — simple counting only.
"""
import csv
import io
import re
import urllib.request
from datetime import date, timedelta
from pathlib import Path

CACHE = Path(__file__).parent / "csv_cache"
CACHE.mkdir(exist_ok=True)
AS_OF = date(2026, 7, 20)

REPOS = {
    "atp": ("Kadantte/tennis_atp",
            ["atp_matches_{y}.csv", "atp_matches_qual_chall_{y}.csv", "atp_matches_futures_{y}.csv"]),
    "wta": ("VictorSquidWei/tennis_wta",
            ["wta_matches_{y}.csv", "wta_matches_qual_itf_{y}.csv"]),
}
YEARS = range(2022, 2027)
SET_TOK = re.compile(r"^(\d{1,2})-(\d{1,2})(?:\(\d+\))?$")
MTB_TOK = re.compile(r"^\[(\d+)-(\d+)\]$")


def fetch(repo, fname):
    p = CACHE / fname
    if not p.exists():
        urllib.request.urlretrieve(f"https://raw.githubusercontent.com/{repo}/master/{fname}", p)
    return list(csv.DictReader(io.StringIO(p.read_text())))


def parse_sets(score):
    """(winner_sets, loser_sets, reached_decider_and_completed, decider_won_by_winner, is_wo, played)"""
    score = (score or "").strip()
    up = score.upper()
    if not score:
        return None
    tokens = score.split()
    is_wo = any(t.upper().strip(".").replace("/", "") in ("WO", "WALKOVER") for t in tokens)
    has_ret = any(t.upper().strip(".") in ("RET", "RETIRED", "DEF", "DEFAULT", "ABN", "ABD") for t in tokens)
    sets = []
    for t in tokens:
        m = SET_TOK.match(t) or MTB_TOK.match(t)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a <= 40 and b <= 40:
                sets.append((a, b))
    if is_wo and not sets:
        return "wo"
    if not sets:
        return None
    # last set incomplete on ret/def unless it looks finished
    completed = list(sets)
    if has_ret:
        a, b = sets[-1]
        hi, lo = max(a, b), min(a, b)
        finished = (hi >= 6 and hi - lo >= 2) or hi == 7 or hi >= 10
        if not finished:
            completed = sets[:-1]
    w = sum(1 for a, b in completed if a > b)
    l = sum(1 for a, b in completed if b > a)
    return w, l, len(completed), has_ret


def player_rows(tour, pid):
    repo, patterns = REPOS[tour]
    out = []
    for y in YEARS:
        for pat in patterns:
            for r in fetch(repo, pat.format(y=y)):
                if r.get("tourney_level") in ("E", "J"):
                    continue
                if r.get("winner_id") == pid or r.get("loser_id") == pid:
                    out.append(r)
    return out


def find_pid(tour, name):
    repo, _ = REPOS[tour]
    for r in fetch(repo, f"{tour}_players.csv"):
        if f"{r['name_first']} {r['name_last']}".strip() == name:
            return r["player_id"]
    raise SystemExit(f"not found: {name}")


def verify(tour, name):
    pid = find_pid(tour, name)
    rows = player_rows(tour, pid)
    cw = cl = w365 = l365 = dw = dl = skunk = wins_completed_scored = 0
    for r in rows:
        d = r["tourney_date"]
        mdate = date(int(d[:4]), int(d[4:6]), int(d[6:8]))
        if mdate >= AS_OF:
            continue
        parsed = parse_sets(r["score"])
        if parsed == "wo" or parsed is None:
            continue  # walkover or unparseable: not a played match
        won = r["winner_id"] == pid
        cw += won
        cl += (not won)
        if mdate >= AS_OF - timedelta(days=365):
            w365 += won
            l365 += (not won)
        wsets, lsets, ncompleted, has_ret = parsed
        best_of = int(r.get("best_of") or 3)
        # decider: total completed sets == best_of means set #best_of completed
        if ncompleted == best_of and (wsets + lsets) == best_of:
            decider_won_by_match_winner = wsets > lsets  # winner won more sets incl. last
            # the match winner won the decider iff they won the last completed set;
            # winner-first notation: last completed set token
            # recompute directly:
            toks = [t for t in r["score"].split()
                    if SET_TOK.match(t) or MTB_TOK.match(t)]
            a, b = (SET_TOK.match(toks[best_of - 1]) or MTB_TOK.match(toks[best_of - 1])).group(1, 2)
            dec_by_winner = int(a) > int(b)
            if won:
                dw += dec_by_winner
                dl += (not dec_by_winner)
            else:
                dw += (not dec_by_winner)
                dl += dec_by_winner
        if won:
            if not has_ret:
                wins_completed_scored += 1
                if lsets == 0 and wsets >= 2:
                    skunk += 1
    print(f"{name} ({tour}): career {cw}-{cl}  365d {w365}-{l365}  "
          f"deciders {dw}-{dl}  skunks {skunk} of {cw} wins ({skunk / cw:.0%})")


verify("atp", "Jannik Sinner")
verify("wta", "Aryna Sabalenka")
verify("wta", "Katarina Kuzmova")
