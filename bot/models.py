"""SQLAlchemy models. All timestamps stored UTC."""
from datetime import date, datetime

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
    __table_args__ = (
        UniqueConstraint("tour", "sackmann_id", name="uq_players_tour_sackmann"),
        Index("ix_players_api_tennis", "tour", "api_tennis_id"),
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
    event_ticker: Mapped[str] = mapped_column(String(96), unique=True)
    market_ticker: Mapped[str] = mapped_column(String(96))
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))
    side: Mapped[str] = mapped_column(String(4))  # 'yes' | 'no' (vs market_ticker)
    price_cents: Mapped[int] = mapped_column(Integer)  # executable ask at placement
    model_prob: Mapped[float] = mapped_column(Float)
    model_confidence: Mapped[float] = mapped_column(Float)
    edge: Mapped[float] = mapped_column(Float)
    basis: Mapped[str] = mapped_column(String(16))  # 'prematch' | 'advisory'
    units: Mapped[int] = mapped_column(Integer, default=1)  # 1-3; 3 is rare
    tier: Mapped[str | None] = mapped_column(String(8))
    state_at_placement: Mapped[str | None] = mapped_column(String(16))
    reasoning: Mapped[dict | None] = mapped_column(JSONB)  # snapshot for tuning
    status: Mapped[str] = mapped_column(String(8), default="open")  # open/won/lost/void
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pnl_cents: Mapped[int | None] = mapped_column(Integer)


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
