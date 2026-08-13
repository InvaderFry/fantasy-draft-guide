"""player_week: one row per player / season / week (S13).

Sources: nflverse player_stats (production), snap_counts (usage), play-by-play
(red-zone and goal-line opportunity, team context), injuries (inactive reason).

``as_of`` is the last gameday of that week: a week's production is knowable
once the week has been played, and not before.
"""

from __future__ import annotations

import polars as pl

from pipeline.features import sources
from pipeline.features.assertions import assert_as_of_present
from pipeline.normalize.player_ids import load_player_ids
from pipeline.scoring import built_in_scoring_exprs

# The current nflverse weekly stats release carries every position, including
# defenders; the legacy one carried offensive skill players only. Filtering
# keeps a 14-season table consistent across that change. Kickers and team
# defenses are out of scope for every S88 Week 2 analysis and have no scoring
# rules configured, so they are excluded rather than half-supported.
FANTASY_POSITIONS = ("QB", "RB", "WR", "TE", "FB")
RED_ZONE_YARDLINE = 20
GOAL_LINE_YARDLINE = 5

# nflverse moved weekly player stats to a new release with renamed columns
# (the `stats_player` release, which is the only one carrying 2025+). Seasons
# straddle the two shapes, so normalize to the legacy names the builder uses.
COLUMN_ALIASES = {
    "team": "recent_team",
    "passing_interceptions": "interceptions",
    "sacks_suffered": "sacks",
    "sack_yards_lost": "sack_yards",
}


def _normalize_columns(frame: pl.DataFrame) -> pl.DataFrame:
    renames = {
        new: old
        for new, old in COLUMN_ALIASES.items()
        if new in frame.columns and old not in frame.columns
    }
    return frame.rename(renames) if renames else frame


def build(season: int) -> pl.DataFrame:
    stats = _normalize_columns(
        sources.load(f"player_stats_{season}.parquet")
    ).filter(
        (pl.col("season_type") == "REG") & pl.col("position").is_in(FANTASY_POSITIONS)
    )

    stats = stats.with_columns(
        (
            pl.col("sack_fumbles_lost").fill_null(0)
            + pl.col("rushing_fumbles_lost").fill_null(0)
            + pl.col("receiving_fumbles_lost").fill_null(0)
        ).alias("fumbles_lost"),
        (
            pl.col("passing_2pt_conversions").fill_null(0)
            + pl.col("rushing_2pt_conversions").fill_null(0)
            + pl.col("receiving_2pt_conversions").fill_null(0)
        ).alias("two_point_conversions"),
        pl.col("special_teams_tds").fill_null(0),
    )
    stats = stats.with_columns(built_in_scoring_exprs(stats.columns))

    pbp_player, pbp_team = _pbp_aggregates(season)
    snaps = _snap_counts(season)
    injuries = _injury_status(season)
    weeks = sources.week_end_dates(season)

    frame = (
        stats.select(
            pl.col("season"),
            pl.col("week"),
            pl.col("player_id"),
            pl.col("position"),
            pl.col("recent_team").alias("team"),
            pl.col("opponent_team").alias("opponent"),
            pl.col("fantasy_points_standard"),
            pl.col("fantasy_points_half_ppr"),
            pl.col("fantasy_points_ppr"),
            pl.col("targets"),
            pl.col("receptions"),
            pl.col("receiving_yards"),
            pl.col("receiving_tds"),
            pl.col("receiving_air_yards").alias("air_yards"),
            pl.col("carries"),
            pl.col("rushing_yards"),
            pl.col("rushing_tds"),
            pl.col("passing_yards"),
            pl.col("passing_tds"),
            pl.col("interceptions"),
            pl.col("fumbles_lost"),
            pl.col("target_share"),
            pl.col("air_yards_share").alias("air_yard_share"),
        )
        .join(pbp_player, on=["season", "week", "player_id"], how="left")
        .join(pbp_team, on=["season", "week", "team"], how="left")
        .join(snaps, on=["season", "week", "player_id"], how="left")
        .join(injuries, on=["season", "week", "player_id"], how="left")
        .join(weeks, on="week", how="left")
    )

    frame = frame.with_columns(
        pl.lit(1).alias("games_active"),  # a player_stats row means the player played
        pl.when(pl.col("report_status") == "Out")
        .then(pl.lit("inactive"))
        .otherwise(pl.lit("active"))
        .alias("active_status"),
        pl.when(pl.col("report_status").is_in(["Out", "Doubtful"]))
        .then(pl.lit("injury"))
        .otherwise(pl.lit(None, dtype=pl.String))
        .alias("inactive_reason"),
        pl.col("week_end").alias("as_of"),
        pl.col("week_end").alias("source_as_of"),
        pl.lit("observed").alias("value_type"),
        (pl.col("red_zone_targets").fill_null(0)).alias("red_zone_targets"),
        (pl.col("red_zone_carries").fill_null(0)).alias("red_zone_carries"),
        (pl.col("goal_line_carries").fill_null(0)).alias("goal_line_carries"),
    ).drop("report_status", "week_end", "week_start")

    assert_as_of_present(frame, f"player_week[{season}]")
    return frame


def _pbp_aggregates(season: int) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Red-zone / goal-line opportunity per player, and team context per week.

    Play-by-play is scanned lazily and reduced immediately: 14 seasons of raw
    pbp is ~270MB and none of it needs to be held once aggregated.
    """
    path = sources.raw_path(f"play_by_play_{season}.parquet")
    lf = pl.scan_parquet(path).with_columns(
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)
    ).filter(pl.col("season_type") == "REG")

    rz_targets = (
        lf.filter(
            (pl.col("pass_attempt") == 1)
            & (pl.col("yardline_100") <= RED_ZONE_YARDLINE)
            & pl.col("receiver_player_id").is_not_null()
        )
        .group_by(["season", "week", "receiver_player_id"])
        .agg(pl.len().alias("red_zone_targets"))
        .rename({"receiver_player_id": "player_id"})
    )
    rz_carries = (
        lf.filter(
            (pl.col("rush_attempt") == 1)
            & (pl.col("yardline_100") <= RED_ZONE_YARDLINE)
            & pl.col("rusher_player_id").is_not_null()
        )
        .group_by(["season", "week", "rusher_player_id"])
        .agg(pl.len().alias("red_zone_carries"))
        .rename({"rusher_player_id": "player_id"})
    )
    gl_carries = (
        lf.filter(
            (pl.col("rush_attempt") == 1)
            & (pl.col("yardline_100") <= GOAL_LINE_YARDLINE)
            & pl.col("rusher_player_id").is_not_null()
        )
        .group_by(["season", "week", "rusher_player_id"])
        .agg(pl.len().alias("goal_line_carries"))
        .rename({"rusher_player_id": "player_id"})
    )
    player = (
        rz_targets.join(rz_carries, on=["season", "week", "player_id"], how="full", coalesce=True)
        .join(gl_carries, on=["season", "week", "player_id"], how="full", coalesce=True)
        .collect()
    )

    team = (
        lf.filter(pl.col("posteam").is_not_null())
        .group_by(["season", "week", "posteam"])
        .agg(
            pl.col("pass_attempt").fill_null(0).sum().alias("team_pass_attempts"),
            pl.col("rush_attempt").fill_null(0).sum().alias("team_rush_attempts"),
        )
        .rename({"posteam": "team"})
        .collect()
    )
    team_points = _team_points(season)
    team = team.join(team_points, on=["season", "week", "team"], how="left")
    return player, team


def _team_points(season: int) -> pl.DataFrame:
    """Points scored per team per week, from the schedule."""
    games = sources.schedule().filter(
        (pl.col("season") == season) & (pl.col("game_type") == "REG")
    )
    home = games.select(
        pl.col("season"),
        pl.col("week"),
        pl.col("home_team").alias("team"),
        pl.col("home_score").alias("team_points"),
    )
    away = games.select(
        pl.col("season"),
        pl.col("week"),
        pl.col("away_team").alias("team"),
        pl.col("away_score").alias("team_points"),
    )
    return pl.concat([home, away]).with_columns(pl.col("team_points").cast(pl.Float64))


def _snap_counts(season: int) -> pl.DataFrame:
    """Snap counts keyed by pfr_player_id -- joined to gsis_id via the crosswalk (S12)."""
    snaps = sources.load(f"snap_counts_{season}.parquet").filter(pl.col("game_type") == "REG")
    xwalk = load_player_ids().select(
        pl.col("pfr_id").alias("pfr_player_id"), pl.col("gsis_id").alias("player_id")
    ).drop_nulls("pfr_player_id").unique(subset="pfr_player_id", keep="none")
    return (
        snaps.join(xwalk, on="pfr_player_id", how="inner")
        .group_by(["season", "week", "player_id"])
        .agg(
            pl.col("offense_snaps").sum().alias("offensive_snaps"),
            pl.col("offense_pct").mean().alias("snap_share"),
        )
    )


def _injury_status(season: int) -> pl.DataFrame:
    injuries = sources.load(f"injuries_{season}.parquet").filter(pl.col("game_type") == "REG")
    return (
        injuries.select(
            pl.col("season"),
            pl.col("week"),
            pl.col("gsis_id").alias("player_id"),
            pl.col("report_status"),
        )
        .drop_nulls("player_id")
        .unique(subset=["season", "week", "player_id"], keep="first")
    )
