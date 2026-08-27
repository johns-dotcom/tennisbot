"""SQLAlchemy models. All timestamps stored UTC."""
from datetime import date, datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Player(Base):
    __tablename__ = "players"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tour: Mapped[str] = mapped_column(String(8))  # 'atp' | 'wta' (ITF men under atp, women under wta)
    sackmann_id: Mapped[int | None] = mapped_column(BigInteger)
    api_tennis_id: Mapped[str | None] = mapped_column(String(32))
    first_name: Mapped[str | None] = mapped_column(String(128))
    last_name: Mapped[str | None] = mapped_column(String(128))
    full_name: Mapped[str] = mapped_column(String(256))
    normalized_name: Mapped[str] = mapped_column(String(256), index=True)
    hand: Mapped[str | None] = mapped_column(String(4))
    dob: Mapped[date | None] = mapped_column(Date)
    ioc: Mapped[str | None] = mapped_column(String(8))
    height_cm: Mapped[int | None] = mapped_column(Integer)
    # current ATP/WTA ranking, refreshed from api-tennis get_standings
    rank: Mapped[int | None] = mapped_column(Integer)
    rank_points: Mapped[int | None] = mapped_column(Integer)
    rank_date: Mapped[date | None] = mapped_column(Date)
    # career surface win/loss splits from api-tennis get_players (singles):
    # {"hard": {"w": int, "l": int}, "clay": {...}, "grass": {...}}
    surface_stats: Mapped[dict | None] = mapped_column(JSONB)
    # last incremental bio/surface fetch (get_players is billed per player, so
    # only active players are refreshed and only when this is stale)
    bio_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("tour", "sackmann_id", name="uq_players_tour_sackmann"),
        Index("ix_players_api_tennis", "tour", "api_tennis_id"),
    )


class PlayerRanking(Base):
    """Weekly ATP/WTA ranking snapshot from api-tennis get_standings. One row
    per player per snapshot date — accumulates ranking history over time (the
    whole table costs two API calls a week). Player.rank holds the latest."""

    __tablename__ = "player_rankings"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), index=True)
    tour: Mapped[str] = mapped_column(String(8))
    as_of: Mapped[date] = mapped_column(Date)
    rank: Mapped[int] = mapped_column(Integer)
    points: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (
        UniqueConstraint("player_id", "as_of", name="uq_player_ranking"),
    )


class Tournament(Base):
    __tablename__ = "tournaments"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tour: Mapped[str] = mapped_column(String(8))
    source: Mapped[str] = mapped_column(String(16))  # 'sackmann' | 'api_tennis'
    source_key: Mapped[str] = mapped_column(String(64))  # sackmann tourney_id / api-tennis tournament_key
    name: Mapped[str] = mapped_column(String(256))
    surface: Mapped[str | None] = mapped_column(String(16))
    level: Mapped[str | None] = mapped_column(String(8))  # G/M/A/C/S/F/D, WTA codes, ITF prize codes
    draw_size: Mapped[int | None] = mapped_column(Integer)
    start_date: Mapped[date | None] = mapped_column(Date)
    __table_args__ = (UniqueConstraint("tour", "source", "source_key", name="uq_tournaments_key"),)


class Match(Base):
    __tablename__ = "matches"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tour: Mapped[str] = mapped_column(String(8))
    source: Mapped[str] = mapped_column(String(16))
    source_key: Mapped[str] = mapped_column(String(96))  # sackmann: tourney_id#match_num
    tournament_id: Mapped[int | None] = mapped_column(ForeignKey("tournaments.id"))
    winner_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    loser_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    match_date: Mapped[date | None] = mapped_column(Date, index=True)  # sackmann: tourney week Monday
    round: Mapped[str | None] = mapped_column(String(8))
    best_of: Mapped[int | None] = mapped_column(Integer)
    score_raw: Mapped[str | None] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(16), default="completed")
    # 'completed' | 'ret' | 'wo' | 'def' | 'abandoned' | 'scheduled' | 'unknown'
    sets_won_winner: Mapped[int | None] = mapped_column(Integer)
    sets_won_loser: Mapped[int | None] = mapped_column(Integer)
    minutes: Mapped[int | None] = mapped_column(Integer)
    surface: Mapped[str | None] = mapped_column(String(16))  # denormalized for stat queries
    tourney_level: Mapped[str | None] = mapped_column(String(8))
    stats: Mapped[dict | None] = mapped_column(JSONB)  # serve stats (w_ace, l_ace, ...)
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False)  # cross-source dedup loser
    scheduled_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # upcoming only
    __table_args__ = (
        UniqueConstraint("source", "source_key", name="uq_matches_source_key"),
        Index("ix_matches_winner_date", "winner_id", "match_date"),
        Index("ix_matches_loser_date", "loser_id", "match_date"),
    )


class MatchSet(Base):
    __tablename__ = "match_sets"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), index=True)
    set_number: Mapped[int] = mapped_column(Integer)
    winner_games: Mapped[int] = mapped_column(Integer)  # games won in this set by the MATCH winner
    loser_games: Mapped[int] = mapped_column(Integer)
    set_won_by_match_winner: Mapped[bool] = mapped_column(Boolean)
    tiebreak: Mapped[bool] = mapped_column(Boolean, default=False)
    tiebreak_loser_points: Mapped[int | None] = mapped_column(Integer)
    is_match_tiebreak: Mapped[bool] = mapped_column(Boolean, default=False)  # [10-7] super TB
    completed: Mapped[bool] = mapped_column(Boolean, default=True)  # False: abandoned mid-set (RET)
    __table_args__ = (UniqueConstraint("match_id", "set_number", name="uq_match_sets"),)


class PlayerStatsCache(Base):
    __tablename__ = "player_stats_cache"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"))
    as_of: Mapped[date] = mapped_column(Date)
    payload: Mapped[dict] = mapped_column(JSONB)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("player_id", "as_of", name="uq_stats_cache"),)


class KalshiMarket(Base):
    __tablename__ = "kalshi_markets"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(96), unique=True)
    event_ticker: Mapped[str | None] = mapped_column(String(96))
    title: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(24))
    close_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    player_a_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))  # YES side
    player_b_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    match_id: Mapped[int | None] = mapped_column(ForeignKey("matches.id"))
    match_confidence: Mapped[float | None] = mapped_column(Float)
    matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict | None] = mapped_column(JSONB)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[str | None] = mapped_column(String(8))  # 'yes' | 'no' | 'void'
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # closing line: YES-side mid (cents) of the last quote at/before match start —
    # the reference for closing-line value (CLV) on our picks
    close_yes_cents: Mapped[int | None] = mapped_column(Integer)


class DerivativeMarket(Base):
    """A Kalshi tennis market on a match that is NOT the match winner — set
    winner, exact match score, total games and friends.

    Deliberately its own table rather than more rows in kalshi_markets. The
    probability engine models match winner only, and every part of the bot
    (discovery, the advisory engine, the paper bots, scenarios, the watch loop)
    reads kalshi_markets — so putting these there would feed the pipeline markets
    it cannot price. Nothing in the bot reads this table; it exists purely so a
    user can log a personal bet on one of these and have it settle. Read-only
    market data, and still no orders anywhere (CLAUDE.md rule 1)."""

    __tablename__ = "derivative_markets"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(96), unique=True)
    event_ticker: Mapped[str] = mapped_column(String(96), index=True)
    series_ticker: Mapped[str] = mapped_column(String(64))
    # 'set_winner' | 'exact_score' | 'total_games' | 'total_sets' | ...
    kind: Mapped[str] = mapped_column(String(24), index=True)
    # the kalshi_markets event this hangs off, e.g. 'KXATPMATCH-26AUG10DARNAK'.
    # A plain string, NOT a foreign key — kalshi_markets rows can be re-discovered
    # and this must never cascade into the bot's own tables.
    match_event_ticker: Mapped[str] = mapped_column(String(96), index=True)
    set_no: Mapped[int | None] = mapped_column(Integer)   # set_winner only
    label: Mapped[str | None] = mapped_column(String(160))  # Kalshi's yes_sub_title
    match_label: Mapped[str | None] = mapped_column(String(160))  # 'A vs B'
    title: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(24))
    close_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # last seen quote/trade, in cents (YES side)
    yes_bid_cents: Mapped[int | None] = mapped_column(Integer)
    yes_ask_cents: Mapped[int | None] = mapped_column(Integer)
    last_price_cents: Mapped[int | None] = mapped_column(Integer)
    result: Mapped[str | None] = mapped_column(String(8))  # 'yes' | 'no' | 'void'
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict | None] = mapped_column(JSONB)


class EloSnapshot(Base):
    """The fitted Elo ratings, persisted so they need only be computed once.

    fit_from_db replays ~875k matches and materialises every row plus a dict of
    per-match set results — ~330 MB peak, measured. Recomputing that inside the
    web process to render two spotlight bands is what made the web service
    expensive on a memory-billed host: CPython rarely returns a freed heap to
    the OS, so one fit permanently raises the process floor.

    The daily ingest already performs this fit (via generate_scenarios), so it
    writes the result here and the web service just loads it. Ratings only
    change on that ingest, so a snapshot is never stale in a way that matters.

    One row per fit; the newest wins. Older rows are kept as a short audit trail
    and pruned on write."""

    __tablename__ = "elo_snapshots"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    fitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
    trained_through: Mapped[date | None] = mapped_column(Date)
    n_matches: Mapped[int | None] = mapped_column(Integer)
    n_players: Mapped[int | None] = mapped_column(Integer)
    # {player_id: [overall, sets_seen, recent, last_day|null, {surface: rating}]}
    # — a compact list per player rather than named keys; at tens of thousands
    # of players the key repetition would dominate the payload.
    ratings: Mapped[dict] = mapped_column(JSONB)


class KalshiFill(Base):
    """One execution on the owner's real Kalshi account.

    Read-only mirror of GET /portfolio/fills (CLAUDE.md rule 1's narrow
    exception). Immutable: rows are only ever inserted, keyed on Kalshi's own
    `fill_id`, which is what makes a re-sync idempotent — note `user_bets` has no
    unique constraint at all and therefore no such guarantee.

    Distinct from UserBet, which is a hand-typed ledger that hardcodes the YES
    side. 21% of real fills are NO-side, so that shape cannot represent them."""

    __tablename__ = "kalshi_fills"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    fill_id: Mapped[str] = mapped_column(String(64), unique=True)
    trade_id: Mapped[str | None] = mapped_column(String(64))
    order_id: Mapped[str | None] = mapped_column(String(64))
    ticker: Mapped[str] = mapped_column(String(96), index=True)
    event_ticker: Mapped[str | None] = mapped_column(String(96), index=True)
    action: Mapped[str] = mapped_column(String(8))        # 'buy' | 'sell'
    outcome_side: Mapped[str] = mapped_column(String(4))  # 'yes' | 'no'
    book_side: Mapped[str | None] = mapped_column(String(8))
    # Kalshi supports FRACTIONAL contracts (count_fp arrives as e.g. "5.57"),
    # so this must not be an integer.
    count: Mapped[float] = mapped_column(Float)
    yes_price_cents: Mapped[int | None] = mapped_column(Integer)
    no_price_cents: Mapped[int | None] = mapped_column(Integer)
    # float, not int: a single fee is often sub-cent ("0.017400" = 1.74¢) and
    # rounding each of ~10k fills would visibly skew the total
    fee_cents: Mapped[float | None] = mapped_column(Float)
    is_taker: Mapped[bool | None] = mapped_column(Boolean)
    ts: Mapped[int | None] = mapped_column(BigInteger, index=True)  # unix seconds
    created_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # deliberately NO raw payload. Every field Kalshi returns is columnised
    # above except market_ticker (always == ticker), side (always ==
    # outcome_side), subaccount_number (always 0) and exchange_index. Keeping
    # the JSONB cost ~2.3 kB per fill — ~24 MB of Python objects across this
    # account's 10.5k fills, on every load. Railway bills memory.


class KalshiSettlement(Base):
    """A settled market on the owner's account — read-only mirror of
    GET /portfolio/settlements. `revenue` arrives as an INT IN CENTS while
    `fee_cost` arrives as a DOLLAR STRING; the mixed scale is the single easiest
    way to skew every P&L on the page, so both are normalized to cents here."""

    __tablename__ = "kalshi_settlements"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(96), unique=True)
    event_ticker: Mapped[str | None] = mapped_column(String(96), index=True)
    market_result: Mapped[str | None] = mapped_column(String(8))  # 'yes'|'no'|'scalar'
    yes_count: Mapped[float | None] = mapped_column(Float)
    no_count: Mapped[float | None] = mapped_column(Float)
    revenue_cents: Mapped[float | None] = mapped_column(Float)
    yes_cost_cents: Mapped[float | None] = mapped_column(Float)
    no_cost_cents: Mapped[float | None] = mapped_column(Float)
    fee_cents: Mapped[float | None] = mapped_column(Float)
    settled_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict | None] = mapped_column(JSONB)


class KalshiPositionTag(Base):
    """A user's tag(s) on one real Kalshi position. Keyed by market ticker — the
    thing you tag is a POSITION, not an individual fill. Shares its vocabulary
    and colours with UserTag so per-tag performance can span the manual ledger
    and real trades."""

    __tablename__ = "kalshi_position_tags"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app_users.id", ondelete="CASCADE"), index=True)
    market_ticker: Mapped[str] = mapped_column(String(96), index=True)
    tag: Mapped[str | None] = mapped_column(String(256))
    __table_args__ = (UniqueConstraint("user_id", "market_ticker",
                                       name="uq_kalshi_pos_tag_user_ticker"),)


class Advisory(Base):
    __tablename__ = "advisories"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    market_ticker: Mapped[str] = mapped_column(String(96), index=True)
    match_id: Mapped[int | None] = mapped_column(ForeignKey("matches.id"))
    recommended_player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    model_prob: Mapped[float] = mapped_column(Float)
    model_confidence: Mapped[float | None] = mapped_column(Float)
    executable_price_cents: Mapped[int] = mapped_column(Integer)
    implied_prob: Mapped[float] = mapped_column(Float)
    edge: Mapped[float] = mapped_column(Float)
    market_volume: Mapped[int | None] = mapped_column(Integer)
    inferred_state: Mapped[str | None] = mapped_column(String(16))
    state_confidence: Mapped[float | None] = mapped_column(Float)
    state_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    probation: Mapped[bool] = mapped_column(Boolean, default=False)
    fact_block: Mapped[dict | None] = mapped_column(JSONB)  # full audit incl. stat values used
    prose: Mapped[str | None] = mapped_column(Text)
    validator_passed: Mapped[bool | None] = mapped_column(Boolean)
    used_template_fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    # 'pending' | 'sent' | 'killed'
    kill_reason: Mapped[str | None] = mapped_column(String(64))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StateInferenceLog(Base):
    __tablename__ = "state_inference_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    market_ticker: Mapped[str] = mapped_column(String(96), index=True)
    session_id: Mapped[str | None] = mapped_column(String(64), index=True)
    inferred_state: Mapped[str] = mapped_column(String(16))
    inferred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    confirmed_state: Mapped[str | None] = mapped_column(String(16))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lead_time_seconds: Mapped[float | None] = mapped_column(Float)
    hit: Mapped[bool | None] = mapped_column(Boolean)  # inferred == confirmed
    session_had_gap: Mapped[bool] = mapped_column(Boolean, default=False)
    detail: Mapped[dict | None] = mapped_column(JSONB)


class MarketTick(Base):
    __tablename__ = "market_ticks"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    market_ticker: Mapped[str] = mapped_column(String(96))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    kind: Mapped[str] = mapped_column(String(16))  # 'quote' | 'trade' | 'score' | 'lifecycle'
    yes_bid: Mapped[int | None] = mapped_column(Integer)
    yes_ask: Mapped[int | None] = mapped_column(Integer)
    no_bid: Mapped[int | None] = mapped_column(Integer)
    no_ask: Mapped[int | None] = mapped_column(Integer)
    trade_price: Mapped[int | None] = mapped_column(Integer)
    trade_count: Mapped[int | None] = mapped_column(Integer)
    volume: Mapped[int | None] = mapped_column(Integer)
    degraded: Mapped[bool] = mapped_column(Boolean, default=False)  # REST-fallback tick
    raw: Mapped[dict | None] = mapped_column(JSONB)
    __table_args__ = (Index("ix_ticks_market_ts", "market_ticker", "ts"),)


class LiveMatchState(Base):
    __tablename__ = "live_match_state"
    market_ticker: Mapped[str] = mapped_column(String(96), primary_key=True)
    match_id: Mapped[int | None] = mapped_column(ForeignKey("matches.id"))
    state: Mapped[str] = mapped_column(String(16))  # '0-0' | '1-0' | '0-1' | '1-1' (sets won)
    confidence: Mapped[float] = mapped_column(Float)
    last_confirmed_state: Mapped[str | None] = mapped_column(String(16))
    stale: Mapped[bool] = mapped_column(Boolean, default=False)
    last_tick_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FeedGap(Base):
    __tablename__ = "feed_gaps"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    market_ticker: Mapped[str] = mapped_column(String(96), index=True)
    session_id: Mapped[str | None] = mapped_column(String(64))
    gap_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    gap_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[float | None] = mapped_column(Float)


class PlayerAlias(Base):
    """Manual override table for the market matcher."""

    __tablename__ = "player_aliases"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    alias_normalized: Mapped[str] = mapped_column(String(256), index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"))
    source: Mapped[str | None] = mapped_column(String(16))  # scope: kalshi/api_tennis/None=any
    note: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("alias_normalized", "source", name="uq_alias_source"),)


class MatchReviewQueue(Base):
    """Names/markets that could not be matched confidently — never silently dropped."""

    __tablename__ = "match_review_queue"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(16))
    raw_name: Mapped[str] = mapped_column(String(256))
    context: Mapped[dict | None] = mapped_column(JSONB)  # market ticker, candidates + scores, ...
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved_player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))


class Scenario(Base):
    """Daily pre-computed gameflow scenarios: state-conditional situations that
    would create betting value if they arise in-play. Generated by the daily
    cron; displayed on the dashboard. Advisory-only, like everything else."""

    __tablename__ = "scenarios"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_for: Mapped[date] = mapped_column(Date, index=True)  # generation day (UTC)
    event_ticker: Mapped[str] = mapped_column(String(96))
    market_ticker: Mapped[str] = mapped_column(String(96))  # the side to watch
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    opponent_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    scenario_state: Mapped[str] = mapped_column(String(16))  # e.g. '1-1', '0-1'
    kind: Mapped[str] = mapped_column(String(32))  # 'decider_edge' | 'resilient_favorite'
    model_prob_at_state: Mapped[float] = mapped_column(Float)
    prematch_prob: Mapped[float] = mapped_column(Float)
    salience: Mapped[float] = mapped_column(Float)
    narrative: Mapped[str] = mapped_column(Text)  # deterministic, fact-only prose
    facts: Mapped[dict | None] = mapped_column(JSONB)
    scheduled_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("created_for", "market_ticker", "scenario_state", "kind",
                         name="uq_scenarios_day"),
    )


class PaperBet(Base):
    """Bot testrun: imaginary one-contract bets the bot places for itself.

    NOT trading — no order ever exists. This is the strategy lab: selective
    self-picks whose record (target: ≥70% after month 1) drives tuning of the
    decision policy. One bet per event, ever."""

    __tablename__ = "paper_bets"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    bot: Mapped[str] = mapped_column(String(8), default="pre", index=True)  # pre|preSI|live|liveSI
    event_ticker: Mapped[str] = mapped_column(String(96))
    market_ticker: Mapped[str] = mapped_column(String(96))
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    side: Mapped[str] = mapped_column(String(4))  # 'yes' | 'no' (vs market_ticker)
    price_cents: Mapped[int] = mapped_column(Integer)  # executable ask at placement
    model_prob: Mapped[float] = mapped_column(Float)
    model_confidence: Mapped[float] = mapped_column(Float)
    edge: Mapped[float] = mapped_column(Float)
    basis: Mapped[str] = mapped_column(String(16))  # 'prematch' | 'advisory'
    units: Mapped[float] = mapped_column(Float, default=1.0)  # 1.0-3.0, decimal; multi-unit rare
    tier: Mapped[str | None] = mapped_column(String(8))
    state_at_placement: Mapped[str | None] = mapped_column(String(16))
    reasoning: Mapped[dict | None] = mapped_column(JSONB)  # snapshot for tuning
    status: Mapped[str] = mapped_column(String(8), default="open")  # open/won/lost/void
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pnl_cents: Mapped[float | None] = mapped_column(Float)  # fractional cents (decimal units)
    __table_args__ = (
        UniqueConstraint("bot", "event_ticker", name="uq_paper_bet_bot_event"),
    )


class ChartingStat(Base):
    """One player's aggregate line from a Match Charting Project match
    (Overview 'Total' row). Shot-level detail — winners, unforced errors,
    FH/BH splits — that no commercial feed exposes. Separate from the results
    tables: this is hand-charted depth over ~5000 matches, not full coverage.

    Data © Tennis Abstract Match Charting Project (CC BY-NC-SA 4.0):
    attribution required, non-commercial, personal research only.
    """

    __tablename__ = "charting_stats"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    match_id: Mapped[str] = mapped_column(String(160))  # MCP match_id
    tour: Mapped[str] = mapped_column(String(8))  # 'atp' | 'wta'
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    player_name: Mapped[str] = mapped_column(String(160))
    match_date: Mapped[date | None] = mapped_column(Date)
    tournament: Mapped[str | None] = mapped_column(String(128))
    surface: Mapped[str | None] = mapped_column(String(16))
    serve_pts: Mapped[int | None] = mapped_column(Integer)
    aces: Mapped[int | None] = mapped_column(Integer)
    dfs: Mapped[int | None] = mapped_column(Integer)
    first_in: Mapped[int | None] = mapped_column(Integer)
    first_won: Mapped[int | None] = mapped_column(Integer)
    second_in: Mapped[int | None] = mapped_column(Integer)
    second_won: Mapped[int | None] = mapped_column(Integer)
    bk_pts: Mapped[int | None] = mapped_column(Integer)  # break points faced
    bp_saved: Mapped[int | None] = mapped_column(Integer)
    return_pts: Mapped[int | None] = mapped_column(Integer)
    return_pts_won: Mapped[int | None] = mapped_column(Integer)
    winners: Mapped[int | None] = mapped_column(Integer)
    winners_fh: Mapped[int | None] = mapped_column(Integer)
    winners_bh: Mapped[int | None] = mapped_column(Integer)
    unforced: Mapped[int | None] = mapped_column(Integer)
    unforced_fh: Mapped[int | None] = mapped_column(Integer)
    unforced_bh: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (
        UniqueConstraint("match_id", "player_name", name="uq_charting_match_player"),
        Index("ix_charting_player", "player_id"),
    )


class MatchScoreLog(Base):
    """The bot's own game-by-game scoring record, built live from Kalshi's
    milestone feed. One row per observed game change (and set change) per
    match — a complete scoreline history, independent of the set-level
    estimator and richer than it. Recording only; never touches trading."""

    __tablename__ = "match_score_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    market_ticker: Mapped[str] = mapped_column(String(96))
    event_ticker: Mapped[str | None] = mapped_column(String(96))
    match_id: Mapped[int | None] = mapped_column(ForeignKey("matches.id"))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # a = YES-side player of market_ticker, b = opponent
    sets_a: Mapped[int] = mapped_column(Integer)
    sets_b: Mapped[int] = mapped_column(Integer)
    set_number: Mapped[int] = mapped_column(Integer)  # current set (1-indexed)
    games_a: Mapped[int] = mapped_column(Integer)
    games_b: Mapped[int] = mapped_column(Integer)
    scoreline: Mapped[str] = mapped_column(String(96))  # "6-3 4-6 2-1", a-perspective
    total_games: Mapped[int] = mapped_column(Integer)  # change detector
    is_final: Mapped[bool] = mapped_column(Boolean, default=False)
    detail: Mapped[dict | None] = mapped_column(JSONB)  # running stats snapshot
    __table_args__ = (Index("ix_scorelog_market_ts", "market_ticker", "ts"),)


class IngestState(Base):
    """Key-value watermarks for incremental ingest (repo commit SHAs, sync dates)."""

    __tablename__ = "ingest_state"
    key: Mapped[str] = mapped_column(String(96), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AppUser(Base):
    """A person allowed to use the web interface. Accounts are created only by an
    admin (no public sign-up) and every route is gated on a valid session — an
    unapproved visitor sees nothing but the login page. Passwords are stored as a
    salted scrypt hash (see bot.webauth), never in plaintext."""

    __tablename__ = "app_users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)  # admin can disable
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    created_by: Mapped[str | None] = mapped_column(String(128))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # My Bets page: the user's current stake per "unit" (USD). Stamped onto each
    # new bet so a later change never retroactively rescales past units.
    mybets_unit_usd: Mapped[int] = mapped_column(Integer, default=500)


class UserPin(Base):
    """A match a user has pinned on the live board for easy viewing. Per-user;
    keyed by Kalshi event_ticker (the match), so it survives market re-discovery.
    Pins are advisory bookmarks only — they change nothing about what the bot
    watches or bets."""

    __tablename__ = "user_pins"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app_users.id", ondelete="CASCADE"), index=True)
    event_ticker: Mapped[str] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    __table_args__ = (UniqueConstraint("user_id", "event_ticker",
                                       name="uq_user_pins_user_event"),)


class UserFavoritePlayer(Base):
    """A player a user has marked as a favorite (from the database or live pages).
    Per-user; keyed by player_id so a favorite follows the player across matches.
    Advisory bookmark only — it changes nothing about what the bot watches or bets."""

    __tablename__ = "user_favorite_players"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app_users.id", ondelete="CASCADE"), index=True)
    player_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("players.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    __table_args__ = (UniqueConstraint("user_id", "player_id",
                                       name="uq_user_fav_players_user_player"),)


class UserTag(Base):
    """Per-user colour for a My Bets tag (e.g. a person they tail). Cosmetic only —
    lets tag chips be colour-coded across the ledger. Keyed by (user, tag)."""

    __tablename__ = "user_tags"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app_users.id", ondelete="CASCADE"), index=True)
    tag: Mapped[str] = mapped_column(String(64))
    color: Mapped[str | None] = mapped_column(String(16))  # '#rrggbb' or NULL/''
    # per-tag stake per unit ($). NULL → fall back to the user's personal
    # (untagged) unit. Stamped onto new bets with this tag at creation.
    unit_usd: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (UniqueConstraint("user_id", "tag", name="uq_user_tags_user_tag"),)


class ModelCalibration(Base):
    """Latest fitted model-calibration parameters, refit walk-forward by the daily
    ingest. Currently the state-conditioned logit-scaling (per set-score). The
    model loads the newest row when it rebuilds; the hardcoded defaults in
    bot.prob.state_adjust are the fallback if the table is empty. Each refit is
    self-gated on out-of-sample log-loss lift, so a bad window can't regress."""

    __tablename__ = "model_calibration"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    fitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
    window_days: Mapped[int] = mapped_column(Integer)
    # {"best_of|sets_a|sets_b": scale_A} for canonical (leader-perspective) states
    state_scale: Mapped[dict | None] = mapped_column(JSONB)
    detail: Mapped[dict | None] = mapped_column(JSONB)  # per-state n / lift / calib
    # global pre-match Platt scalar (walk-forward refit); overrides the elo.py
    # default on model rebuild via load_platt_calibration(). NULL on state-only rows.
    platt_a: Mapped[float | None] = mapped_column(Float)


class UserBet(Base):
    """A user's personal, manually-logged bet on a Kalshi tennis market. Purely a
    record-keeping ledger — the app is advisory-only and places no orders; this
    just tracks what the user says they bought so their own P&L / CLV can be shown
    alongside the bot leaderboard. Outcome, closing line and settlement are read
    live off the referenced KalshiMarket, so nothing here is a duplicated result."""

    __tablename__ = "user_bets"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app_users.id", ondelete="CASCADE"), index=True)
    event_ticker: Mapped[str] = mapped_column(String(128), index=True)
    market_ticker: Mapped[str] = mapped_column(String(128), index=True)  # side bought
    side: Mapped[str] = mapped_column(String(4), default="yes")  # 'yes' | 'no'
    player_name: Mapped[str] = mapped_column(String(128))       # denormalized display
    opponent_name: Mapped[str | None] = mapped_column(String(128))
    entry_price_cents: Mapped[int] = mapped_column(Integer)     # 1..99 (¢ paid)
    shares: Mapped[int] = mapped_column(Integer)                # contracts held
    # $/unit in effect when this bet was placed (snapshot; NULL on legacy rows,
    # treated as the $500 default). Groups the ledger into unit-size epochs.
    unit_usd: Mapped[int | None] = mapped_column(Integer)
    # exit: NULL = still held (settles on the match result); set = cashed out at
    # this price, so P&L is realized at the sell regardless of the final outcome.
    exit_price_cents: Mapped[int | None] = mapped_column(Integer)
    exit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # partial cash-out: selling only SOME of a position splits it, and the sold
    # slice is a new row pointing back here. NULL = this row is the position
    # itself (whole, or the still-open remainder after slices were sold off).
    parent_bet_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("user_bets.id", ondelete="SET NULL"), index=True)
    note: Mapped[str | None] = mapped_column(String(256))
    # optional user tags (people tailed, strategies) — groups the ledger so
    # per-tag performance can be shown separately. NULL = untagged. A bet can
    # carry several, stored comma-joined; each one is capped at 64 chars to match
    # user_tags.tag, and the joined list at this column's width.
    tag: Mapped[str | None] = mapped_column(String(256), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
