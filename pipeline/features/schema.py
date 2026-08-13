"""Column contracts for the canonical tables (S13) and the as-of fields (S6.1).

Three columns appear on every feature row, from the first table written:

    as_of          date the value first became knowable to someone standing
                   outside the future
    source_as_of   date the upstream source published or last revised it
    value_type     observed | derived | imputed | unavailable   (S37)

Outcome columns come from the future by construction. They live in their own
tables, are never joined into a feature frame, and are declared here so the
assertion in ``assertions.py`` skips them deliberately rather than by omission.
"""

from __future__ import annotations

AS_OF_COLUMNS = ("as_of", "source_as_of", "value_type")

VALUE_TYPES = frozenset({"observed", "derived", "imputed", "unavailable"})

# Columns that describe the future relative to their own season (S6.1).
OUTCOME_COLUMNS = frozenset(
    {
        "fantasy_points",
        "fantasy_points_standard",
        "fantasy_points_half_ppr",
        "fantasy_points_ppr",
        "fantasy_ppg",
        "fantasy_ppg_active",
        "position_finish",
        "position_finish_ppg",
        "games",
        "games_missed",
        "games_missed_injury",
        "hit_high_end",
    }
)

# S13 player_week
PLAYER_WEEK_COLUMNS = (
    "season", "week", "player_id", "position", "team", "opponent",
    "as_of", "source_as_of", "value_type",
    "games_active", "active_status", "inactive_reason",
    "fantasy_points_standard", "fantasy_points_half_ppr", "fantasy_points_ppr",
    "targets", "receptions", "receiving_yards", "receiving_tds", "air_yards",
    "carries", "rushing_yards", "rushing_tds",
    "passing_yards", "passing_tds", "interceptions", "fumbles_lost",
    "offensive_snaps", "snap_share",
    "red_zone_targets", "red_zone_carries", "goal_line_carries",
    "team_pass_attempts", "team_rush_attempts", "team_targets", "team_air_yards",
    "team_points",
    "target_share", "air_yard_share",
)

# S13 player_season (feature side)
PLAYER_SEASON_COLUMNS = (
    "season", "player_id", "position", "team",
    "as_of", "source_as_of", "value_type",
    "age", "experience", "draft_round", "draft_pick",
    "targets", "target_share", "receptions", "receiving_yards", "air_yards",
    "air_yard_share", "red_zone_targets",
    "carries", "rush_share", "goal_line_carries", "red_zone_carries",
    "offensive_snaps", "snap_share",
    "depth_chart_rank", "depth_chart_rank_preseason", "team_position_share_rank",
)

# S13 player_season outcome side (S15.1: availability and per-game production
# are separate processes and are reported separately).
PLAYER_SEASON_OUTCOME_COLUMNS = (
    "season", "player_id", "position", "team",
    "as_of", "source_as_of", "value_type", "is_outcome",
    "games", "rostered_weeks", "games_missed", "games_missed_injury",
    "fantasy_points_standard", "fantasy_points_half_ppr", "fantasy_points_ppr",
    "fantasy_ppg", "fantasy_ppg_active",
)

# S13 team_season
TEAM_SEASON_COLUMNS = (
    "season", "team",
    "as_of", "source_as_of", "value_type",
    "games", "plays", "plays_per_game", "points",
    "offensive_tds", "passing_tds", "rushing_tds",
    "pass_attempts", "rush_attempts", "pass_rate", "neutral_pass_rate",
    "yards_per_play", "red_zone_trips", "red_zone_td_rate", "turnovers",
)

# S13 adp_history
ADP_HISTORY_COLUMNS = (
    "season", "snapshot_date", "as_of", "source_as_of",
    "window_start", "window_end", "total_drafts", "value_type",
    "source", "format", "teams",
    "player_id", "source_player_id", "source_player_name", "position", "team",
    "bye", "adp", "position_adp", "sample_size_if_available",
    "adp_stdev", "pick_high", "pick_low",
    "pick_p10", "pick_p25", "pick_p50", "pick_p75", "pick_p90",
    "n_drafts", "match_method", "match_confidence",
)


def outcome_columns_in(columns) -> list[str]:
    return sorted(set(columns) & OUTCOME_COLUMNS)
