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

# Depth charts carry an offensive group, two defensive groups and this one.
SPECIAL_TEAMS_GROUP = "Special Teams"


def build(season: int, weekly: pl.DataFrame | None = None) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Return (features, outcomes) for one season."""
    weekly = weekly if weekly is not None else pw.build(season)
    as_of = sources.season_end_date(season)

    # player_week is the roster population, so a row is a week on the roster and
    # `games_active` says whether it was played. Availability falls out of the
    # same aggregation as production rather than being re-derived from the raw
    # roster and injury files a second time.
    active = pl.col("games_active") == 1
    per_player = weekly.sort(["season", "player_id", "week"]).group_by(
        ["season", "player_id"]
    ).agg(
        pl.col("position").drop_nulls().first().alias("position"),
        pl.col("team").drop_nulls().last().alias("team"),
        pl.len().alias("rostered_weeks"),
        pl.col("games_active").sum().alias("games"),
        _injury_absences().alias("games_missed_injury"),
        pl.col("targets").sum().alias("targets"),
        pl.col("receptions").sum().alias("receptions"),
        pl.col("receiving_yards").sum().alias("receiving_yards"),
        pl.col("air_yards").sum().alias("air_yards"),
        pl.col("red_zone_targets").sum().alias("red_zone_targets"),
        pl.col("carries").sum().alias("carries"),
        pl.col("red_zone_carries").sum().alias("red_zone_carries"),
        pl.col("goal_line_carries").sum().alias("goal_line_carries"),
        pl.col("offensive_snaps").sum().alias("offensive_snaps"),
        pl.col("snap_share").filter(active).mean().alias("snap_share"),
        # denominators cover only the weeks the player was active, so these are
        # shares of team opportunity while playing, not season-wide shares
        pl.col("team_targets").filter(active).sum().alias("_team_targets_active"),
        pl.col("team_rush_attempts").filter(active).sum().alias("_team_rush_attempts_active"),
        pl.col("team_air_yards").filter(active).sum().alias("_team_air_yards_active"),
        pl.col("fantasy_points_standard").sum().alias("fantasy_points_standard"),
        pl.col("fantasy_points_half_ppr").sum().alias("fantasy_points_half_ppr"),
        pl.col("fantasy_points_ppr").sum().alias("fantasy_points_ppr"),
    ).with_columns(
        (pl.col("rostered_weeks") - pl.col("games")).cast(pl.Int64).alias("games_missed")
    )
    per_player = _blank_availability_without_roster_data(per_player, weekly)

    # All three shares use the same denominator convention: team opportunity
    # over the weeks the player was active. air_yard_share previously divided a
    # player's full-season air yards by the full-season total of whichever team
    # they finished on, which understated every player who missed time and was
    # simply wrong for anyone traded mid-season. target_share divided by team
    # pass attempts, which count sacks -- see _team_receiving in player_week.
    per_player = per_player.with_columns(
        _share("targets", "_team_targets_active").alias("target_share"),
        _share("carries", "_team_rush_attempts_active").alias("rush_share"),
        _share("air_yards", "_team_air_yards_active").alias("air_yard_share"),
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
    frame = per_player.with_columns(
        pl.lit(as_of).alias("as_of"),
        pl.lit(as_of).alias("source_as_of"),
        pl.lit("derived").alias("value_type"),
        pl.lit(True).alias("is_outcome"),
    )
    frame = frame.with_columns(
        _share("fantasy_points_ppr", "rostered_weeks").alias("fantasy_ppg"),
        _share("fantasy_points_ppr", "games").alias("fantasy_ppg_active"),
    )
    _assert_absences_add_up(frame, season)
    return frame.select(
        "season", "player_id", "position", "team",
        "as_of", "source_as_of", "value_type", "is_outcome",
        "games", "rostered_weeks", "games_missed", "games_missed_injury",
        "fantasy_points_standard", "fantasy_points_half_ppr", "fantasy_points_ppr",
        "fantasy_ppg", "fantasy_ppg_active",
    )


def _share(numerator: str, denominator: str) -> pl.Expr:
    """A ratio that is null, not NaN, when the denominator is zero.

    A player rostered all season who never played has no team opportunity to
    take a share of. Polars gives 0/0 as NaN, which survives `is_null()`,
    poisons any `mean()`, and sorts ahead of every real value in a descending
    sort -- so "top ten by target share" returned ten players with no targets.
    """
    return (
        pl.when(pl.col(denominator) > 0)
        .then(pl.col(numerator) / pl.col(denominator))
        .otherwise(None)
    )


def _injury_absences() -> pl.Expr:
    """Weeks on the roster, not played, for a health reason (S15.1).

    Counting injury-report rows with ``report_status == "Out"`` -- the previous
    definition -- misses the absences that matter most. A player placed on IR or
    PUP stops appearing on the weekly injury report altogether, so a season
    ending in week 2 contributed one injury-related absence and a dozen
    unexplained ones. player_week classifies from the reserve list first and the
    injury report second, which is why this is now a count of its labels.
    """
    return (
        ((pl.col("games_active") == 0) & (pl.col("inactive_reason") == "injury"))
        .sum()
        .cast(pl.Int64)
    )


AVAILABILITY_COLUMNS = ("rostered_weeks", "games_missed", "games_missed_injury")


def _blank_availability_without_roster_data(
    per_player: pl.DataFrame, weekly: pl.DataFrame
) -> pl.DataFrame:
    """Null, not zero, when the season has no weekly roster file.

    Without it player_week falls back to the stat sheet, where every row is a
    game played. Reporting `games_missed = 0` for such a season would assert
    that nobody missed a game, which is a stronger claim than the data supports.
    """
    if weekly.height and weekly["active_status"].null_count() < weekly.height:
        return per_player
    return per_player.with_columns(
        pl.lit(None, dtype=pl.Int64).alias(col) for col in AVAILABILITY_COLUMNS
    )


def _assert_absences_add_up(frame: pl.DataFrame, season: int) -> None:
    """An injury-related absence is an absence (S15.1)."""
    bad = frame.filter(pl.col("games_missed_injury") > pl.col("games_missed"))
    if bad.height:
        raise AssertionError(
            f"player_season_outcomes[{season}]: {bad.height} player(s) with more "
            f"injury absences than absences:\n"
            f"{bad.select('player_id', 'games', 'games_missed', 'games_missed_injury').head(5)}"
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
        # A return man appears twice: once in the offensive group at his real
        # depth, and once in Special Teams as PR1/KR1. Taking the minimum rank
        # across both promoted 86 of 2025's 727 player-seasons to "the team's
        # number one" -- Rashid Shaheed, a WR2, and several WR7s among them.
        # 2025 is the only season this feature has any data for, so that was
        # 11.8% of the entire population, biased toward exactly the players
        # whose role a depth-chart rank is supposed to describe correctly.
        charts = charts.filter(pl.col("pos_grp") != SPECIAL_TEAMS_GROUP)
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
