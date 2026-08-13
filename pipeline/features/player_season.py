"""player_season: features and outcomes, kept in separate tables (S13, S15.1, S6.1).

Two frames come out of one build:

* ``player_season``          -- role, opportunity and biographical features
* ``player_season_outcomes`` -- games, fantasy points and per-game production,
                                flagged ``is_outcome`` and never joined into a
                                feature frame for the same season

The split is S15.1's requirement made structural. Season outcome is
``games_played x points_per_game_when_active``; those are different processes,
and modelling the product mixes a learnable signal with a large, mostly
irreducible noise term. Availability lives in the outcome table with its own
columns so a method can fit the two components separately.

``as_of`` for both is the season's final regular-season gameday: a season
aggregate is knowable only once the season is over. That is why player_season
for year Y is a legitimate feature for year Y+1 and a leakage violation for
year Y -- the assertion in ``assertions.py`` enforces exactly that.
"""

from __future__ import annotations

import datetime as dt

import polars as pl

from pipeline.config import decision_date
from pipeline.features import player_week as pw
from pipeline.features import sources
from pipeline.features.assertions import assert_as_of_present, assert_no_outcome_columns
from pipeline.normalize.player_ids import load_player_ids

ROSTERED_STATUSES = ("ACT", "INA", "RES")


def build(season: int, weekly: pl.DataFrame | None = None) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Return (features, outcomes) for one season."""
    weekly = weekly if weekly is not None else pw.build(season)
    as_of = sources.season_end_date(season)

    per_player = weekly.group_by(["season", "player_id"]).agg(
        pl.col("position").drop_nulls().first().alias("position"),
        pl.col("team").drop_nulls().last().alias("team"),
        pl.len().alias("games"),
        pl.col("targets").sum().alias("targets"),
        pl.col("receptions").sum().alias("receptions"),
        pl.col("receiving_yards").sum().alias("receiving_yards"),
        pl.col("air_yards").sum().alias("air_yards"),
        pl.col("red_zone_targets").sum().alias("red_zone_targets"),
        pl.col("carries").sum().alias("carries"),
        pl.col("red_zone_carries").sum().alias("red_zone_carries"),
        pl.col("goal_line_carries").sum().alias("goal_line_carries"),
        pl.col("offensive_snaps").sum().alias("offensive_snaps"),
        pl.col("snap_share").mean().alias("snap_share"),
        # denominators cover only the weeks the player was active, so these are
        # shares of team opportunity while playing, not season-wide shares
        pl.col("team_pass_attempts").sum().alias("_team_pass_attempts_active"),
        pl.col("team_rush_attempts").sum().alias("_team_rush_attempts_active"),
        pl.col("fantasy_points_standard").sum().alias("fantasy_points_standard"),
        pl.col("fantasy_points_half_ppr").sum().alias("fantasy_points_half_ppr"),
        pl.col("fantasy_points_ppr").sum().alias("fantasy_points_ppr"),
    )

    team_air_yards = (
        weekly.group_by(["season", "team"]).agg(pl.col("air_yards").sum().alias("team_air_yards"))
    )

    per_player = per_player.join(team_air_yards, on=["season", "team"], how="left").with_columns(
        (pl.col("targets") / pl.col("_team_pass_attempts_active")).alias("target_share"),
        (pl.col("carries") / pl.col("_team_rush_attempts_active")).alias("rush_share"),
        (pl.col("air_yards") / pl.col("team_air_yards")).alias("air_yard_share"),
    )

    bio = _biographical(season)
    depth = _preseason_depth_chart(season)

    features = (
        per_player.join(bio, on="player_id", how="left")
        .join(depth, on=["season", "player_id"], how="left")
        .with_columns(
            pl.lit(as_of).alias("as_of"),
            pl.lit(as_of).alias("source_as_of"),
            pl.lit("derived").alias("value_type"),
        )
    )
    features = features.with_columns(
        pl.struct(["targets", "carries"])
        .map_elements(lambda s: (s["targets"] or 0) + (s["carries"] or 0), return_dtype=pl.Int64)
        .alias("_opportunity")
    )
    features = features.with_columns(
        pl.col("_opportunity")
        .rank("dense", descending=True)
        .over(["season", "team", "position"])
        .alias("team_position_share_rank")
    )

    outcomes = _outcomes(season, per_player, as_of)

    features = features.select(
        "season", "player_id", "position", "team",
        "as_of", "source_as_of", "value_type",
        "age", "experience", "draft_round", "draft_pick",
        "targets", "target_share", "receptions", "receiving_yards", "air_yards",
        "air_yard_share", "red_zone_targets",
        "carries", "rush_share", "goal_line_carries", "red_zone_carries",
        "offensive_snaps", "snap_share",
        "depth_chart_rank_preseason", "depth_chart_rank_preseason_as_of",
        "team_position_share_rank",
    )

    assert_as_of_present(features, f"player_season[{season}]")
    assert_no_outcome_columns(
        features.drop("depth_chart_rank_preseason_as_of"), f"player_season[{season}]"
    )
    assert_as_of_present(outcomes, f"player_season_outcomes[{season}]")
    return features, outcomes


def _outcomes(season: int, per_player: pl.DataFrame, as_of: dt.date) -> pl.DataFrame:
    """Availability and production, reported separately (S15.1)."""
    availability = _availability(season)
    frame = per_player.join(availability, on=["season", "player_id"], how="left").with_columns(
        pl.lit(as_of).alias("as_of"),
        pl.lit(as_of).alias("source_as_of"),
        pl.lit("derived").alias("value_type"),
        pl.lit(True).alias("is_outcome"),
    )
    frame = frame.with_columns(
        (pl.col("fantasy_points_ppr") / pl.col("rostered_weeks")).alias("fantasy_ppg"),
        (pl.col("fantasy_points_ppr") / pl.col("games")).alias("fantasy_ppg_active"),
    )
    return frame.select(
        "season", "player_id", "position", "team",
        "as_of", "source_as_of", "value_type", "is_outcome",
        "games", "rostered_weeks", "games_missed", "games_missed_injury",
        "fantasy_points_standard", "fantasy_points_half_ppr", "fantasy_points_ppr",
        "fantasy_ppg", "fantasy_ppg_active",
    )


def _availability(season: int) -> pl.DataFrame:
    """Weeks rostered vs weeks played, and how many absences were injuries.

    S15.1 is explicit that injury history predicts future injury only weakly.
    The purpose of these columns is to remove injury noise from per-game
    production so the signals under test can be measured cleanly.
    """
    try:
        weekly_roster = sources.load(f"roster_weekly_{season}.parquet")
    except sources.MissingRawData:
        return pl.DataFrame(
            schema={
                "season": pl.Int32, "player_id": pl.String, "rostered_weeks": pl.Int64,
                "games_missed": pl.Int64, "games_missed_injury": pl.Int64,
            }
        )

    reg_weeks = sources.week_end_dates(season)["week"].to_list()
    rostered = (
        weekly_roster.filter(
            pl.col("status").is_in(ROSTERED_STATUSES) & pl.col("week").is_in(reg_weeks)
        )
        .group_by(["season", pl.col("gsis_id").alias("player_id")])
        .agg(pl.col("week").n_unique().alias("rostered_weeks"))
        .drop_nulls("player_id")
    )

    played = (
        pl.read_parquet(sources.raw_path(f"player_stats_{season}.parquet"))
        .filter(pl.col("season_type") == "REG")
        .group_by(["season", "player_id"])
        .agg(pl.col("week").n_unique().alias("_played"))
    )

    injured_out = (
        sources.load(f"injuries_{season}.parquet")
        .filter((pl.col("game_type") == "REG") & (pl.col("report_status") == "Out"))
        .group_by(["season", pl.col("gsis_id").alias("player_id")])
        .agg(pl.col("week").n_unique().alias("_weeks_out"))
        .drop_nulls("player_id")
    )

    return (
        rostered.join(played, on=["season", "player_id"], how="left")
        .join(injured_out, on=["season", "player_id"], how="left")
        .with_columns(
            # Counts arrive as unsigned; subtracting them without a cast wraps a
            # negative difference to ~4.3 billion instead of failing.
            pl.col("rostered_weeks").cast(pl.Int64),
            pl.col("_played").fill_null(0).cast(pl.Int64),
            pl.col("_weeks_out").fill_null(0).cast(pl.Int64),
        )
        .with_columns(
            # A player who appeared in a game was rostered that week, whatever
            # the weekly roster file says -- practice-squad elevations show up
            # as DEV and would otherwise report more games than weeks.
            pl.max_horizontal("rostered_weeks", "_played").alias("rostered_weeks")
        )
        .with_columns(
            (pl.col("rostered_weeks") - pl.col("_played")).alias("games_missed"),
            pl.col("_weeks_out").alias("games_missed_injury"),
        )
        .select("season", "player_id", "rostered_weeks", "games_missed", "games_missed_injury")
    )


def _biographical(season: int) -> pl.DataFrame:
    """Age, experience and draft capital from the crosswalk (S12)."""
    season_start = dt.date(season, 9, 1)
    xwalk = load_player_ids().select(
        pl.col("gsis_id").alias("player_id"),
        pl.col("birth_date"),
        pl.col("rookie_season"),
        pl.col("draft_round"),
        pl.col("draft_pick"),
    )
    return xwalk.with_columns(
        (
            (pl.lit(season_start) - pl.col("birth_date").cast(pl.Date)).dt.total_days() / 365.25
        ).alias("age"),
        (pl.lit(season) - pl.col("rookie_season")).alias("experience"),
    ).select("player_id", "age", "experience", "draft_round", "draft_pick")


def _preseason_depth_chart(season: int) -> pl.DataFrame:
    """Depth-chart rank as of the last chart published on or before the decision date (S86).

    Two upstream formats have to be handled, and they differ in a way that
    matters for leakage rather than only for parsing:

    * legacy (through 2024) -- keyed by week, earliest chart is regular-season
      week 1. Its ``as_of`` therefore falls AFTER the August decision date, so
      the value is not knowable at the draft and this function returns nothing
      for those seasons. That is the honest answer, not a gap to paper over:
      dating a week-1 chart as if it were preseason is exactly the leakage
      S6.1 exists to stop.
    * current (2025+) -- carries a real publication timestamp ``dt``, with
      charts published from early August. Those genuinely are preseason, so the
      latest chart at or before the decision date is used, dated by ``dt``.
    """
    empty = pl.DataFrame(
        schema={
            "season": pl.Int32,
            "player_id": pl.String,
            "depth_chart_rank_preseason": pl.Int64,
            "depth_chart_rank_preseason_as_of": pl.Date,
        }
    )
    try:
        charts = sources.load(f"depth_charts_{season}.parquet")
    except sources.MissingRawData:
        return empty
    if charts.height == 0:
        return empty

    cutoff = decision_date(season)

    if "dt" in charts.columns:  # current format
        charts = charts.with_columns(
            pl.col("dt").str.slice(0, 10).str.to_date(strict=False).alias("chart_date")
        ).filter((pl.col("chart_date") <= pl.lit(cutoff)) & pl.col("gsis_id").is_not_null())
        if charts.height == 0:
            return empty
        latest = charts.select(pl.col("chart_date").max()).item()
        return (
            charts.filter(pl.col("chart_date") == latest)
            .group_by(pl.col("gsis_id").alias("player_id"))
            .agg(pl.col("pos_rank").cast(pl.Int64).min().alias("depth_chart_rank_preseason"))
            .with_columns(
                pl.lit(season, dtype=pl.Int32).alias("season"),
                pl.lit(latest).alias("depth_chart_rank_preseason_as_of"),
            )
            .select(
                "season", "player_id",
                "depth_chart_rank_preseason", "depth_chart_rank_preseason_as_of",
            )
        )

    # legacy format: the earliest chart is week 1, which postdates the draft
    return empty
