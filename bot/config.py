"""Central configuration. All tunable thresholds live here, env-overridable."""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://tennisbot:tennisbot@localhost:5432/tennisbot"

    # --- data sources ---
    # Sackmann's canonical repos went private in mid-2026; these forks are full
    # mirrors (CC BY-NC-SA — attribution required, non-commercial, personal
    # research only; never redistribute the data).
    sackmann_atp_repo: str = "Kadantte/tennis_atp"
    sackmann_wta_repo: str = "VictorSquidWei/tennis_wta"
    backfill_start_year: int = 2022  # >3 years of history
    api_tennis_key: str = ""
    api_tennis_base: str = "https://api.api-tennis.com/tennis/"
    schedule_horizon_hours: int = 48

    # --- Kalshi (READ ONLY — never any trading endpoint) ---
    kalshi_api_key_id: str = ""
    kalshi_private_key_b64: str = ""
    kalshi_api_base: str = "https://external-api.kalshi.com/trade-api/v2"
    kalshi_ws_url: str = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
    kalshi_discovery_interval_s: int = 900
    kalshi_score_poll_interval_s: int = 25

    # --- advisory delivery ---
    discord_webhook_url: str = ""
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"

    # --- stats engine: minimum sample sizes (fallback hierarchy gates) ---
    min_sample_form: int = 5
    min_sample_decider: int = 4
    min_sample_surface: int = 5
    min_sample_h2h: int = 2
    min_sample_common_opponents: int = 3

    # --- market matcher ---
    fuzzy_match_threshold: int = 88  # rapidfuzz score 0-100; below → review queue
    fuzzy_review_threshold: int = 70  # below this, not even queued as a candidate

    # --- state estimator (Phase 3.5) ---
    boundary_jump_cents: int = 8  # X: price discontinuity size
    boundary_window_seconds: int = 45  # Y: window the jump must occur within
    boundary_min_trade_contracts: int = 5  # volume confirmation floor

    # --- edge / advisory gating (Phase 5) ---
    edge_threshold: float = 0.06
    min_model_confidence: float = 0.5
    min_market_volume: int = 100
    min_state_confidence: float = 0.85

    # --- probation (Phase 5) — flip to False manually, only after `graduate` passes ---
    probation: bool = True
    graduate_min_confirmed_transitions: int = 200
    graduate_min_hit_rate: float = 0.90
    graduate_max_false_boundary_rate: float = 0.05

    # --- restart / feed-gap protocol (Phase 6) ---
    stale_gap_seconds: int = 60
    feed_gap_quarantine_seconds: int = 30


@lru_cache
def settings() -> Settings:
    return Settings()
