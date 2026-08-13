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

# S13 projection_snapshot (S11 canonical schema).
# `projected_fantasy_points` and `projected_games` carry the prefix S11 does not:
# the bare names are in OUTCOME_COLUMNS above, where they mean what a player
# actually scored and actually played. Two columns with one name on two different
# meanings is the target_share bug this repo already shipped once.
PROJECTION_SNAPSHOT_COLUMNS = (
    "season", "snapshot_date", "as_of", "source_as_of", "value_type",
    "source", "provider_id", "transport",
    "player_id", "source_player_id", "source_player_name", "position", "team",
    # Stat names are player_week's, not S11's (`pass_yards`, `rush_yards`, ...).
    # Two reasons: pipeline/scoring.py already reads these, so a projection is
    # scored by the same code and the same profile as a realized season with no
    # second mapping to drift; and S66's backtest compares a projection against
    # the outcome, which is only a join if the columns line up.
    "pass_attempts", "pass_completions", "passing_yards", "passing_tds", "interceptions",
    "carries", "rushing_yards", "rushing_tds",
    "targets", "receptions", "receiving_yards", "receiving_tds",
    # The rest of scoring.STAT_TO_RULE. Omitting them silently dropped three
    # scoring terms from every projected total, which is the exact drift the
    # shared stat spelling above exists to prevent: a projection has to be
    # priceable by the same expression a realized season is.
    "fumbles_lost", "two_point_conversions", "special_teams_tds",
    "projected_fantasy_points", "projected_games",
    "match_method", "match_confidence",
)


def outcome_columns_in(columns) -> list[str]:
    return sorted(set(columns) & OUTCOME_COLUMNS)
