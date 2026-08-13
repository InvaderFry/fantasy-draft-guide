"""team_season: one row per team per season (S13).

This is the table S25 (team scoring / TD regression) runs on -- roughly 448
team-seasons across 2012-2025, which S5.1 identifies as the best-powered
population in the project.

Built entirely from play-by-play, scanned lazily and reduced immediately.
``as_of`` is the season's final regular-season gameday.
"""

from __future__ import annotations

import polars as pl

from pipeline.features import sources
from pipeline.features.assertions import assert_as_of_present

RED_ZONE_YARDLINE = 20


def build(season: int) -> pl.DataFrame:
    lf = (
        pl.scan_parquet(sources.raw_path(f"play_by_play_{season}.parquet"))
        .with_columns(pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32))
        .filter((pl.col("season_type") == "REG") & pl.col("posteam").is_not_null())
    )

    # `plays` and `yards` have to be counted over the same set or the ratio is
    # meaningless. `play == 1` includes 2,701 penalty-nullified snaps in 2024
    # that gain nothing, while `yards_gained` was summed over every row
    # including kneel-downs that `play` excludes -- yards_per_play came out at
    # 5.08 against a real scrimmage figure of 5.41. Both now use the scrimmage
    # set, which is also the denominator pass_rate is built from.
    scrimmage = (pl.col("pass_attempt").fill_null(0) + pl.col("rush_attempt").fill_null(0)) > 0

    base = (
        lf.group_by("posteam")
        .agg(
            pl.col("game_id").n_unique().alias("games"),
            scrimmage.sum().alias("plays"),
            pl.col("pass_attempt").fill_null(0).sum().alias("pass_attempts"),
            pl.col("rush_attempt").fill_null(0).sum().alias("rush_attempts"),
            pl.col("yards_gained").fill_null(0).filter(scrimmage).sum().alias("yards"),
            pl.col("pass_touchdown").fill_null(0).sum().alias("passing_tds"),
            pl.col("rush_touchdown").fill_null(0).sum().alias("rushing_tds"),
            pl.col("interception").fill_null(0).sum().alias("interceptions"),
            pl.col("fumble_lost").fill_null(0).sum().alias("fumbles_lost"),
        )
        .rename({"posteam": "team"})
    )

    # Neutral game script: S25 wants pass rate that is not an artefact of
    # trailing. First three quarters, score within one score.
    neutral = (
        lf.filter(
            (pl.col("qtr") <= 3)
            & (pl.col("score_differential").abs() <= 8)
            & ((pl.col("pass_attempt") == 1) | (pl.col("rush_attempt") == 1))
        )
        .group_by("posteam")
        .agg(
            pl.col("pass_attempt").fill_null(0).sum().alias("_neutral_pass"),
            pl.col("rush_attempt").fill_null(0).sum().alias("_neutral_rush"),
        )
        .rename({"posteam": "team"})
    )

    # Two corrections to what counts as a red-zone trip.
    #
    # Extra points are snapped from the 15 and two-point tries from the 2, and
    # nflverse assigns them to the drive that scored. A drive that scored from
    # 60 yards out therefore has no scrimmage play inside the 20 but does have
    # its extra point there, so it was being counted as a red-zone trip that
    # failed to reach the end zone. In 2024 that was 365 of 2,195 trips, all
    # scored as no-TD, dragging the league red-zone TD rate from 0.561 to 0.467.
    #
    # And `touchdown` is set on any score, including one by the defence: a
    # red-zone pick-six would otherwise be credited to the offence's conversion
    # rate. Four of 2024's 1,026 red-zone touchdown plays.
    red_zone = (
        lf.filter(
            (pl.col("yardline_100") <= RED_ZONE_YARDLINE)
            & (pl.col("extra_point_attempt").fill_null(0) == 0)
            & (pl.col("two_point_attempt").fill_null(0) == 0)
        )
        .group_by(["posteam", "game_id", "fixed_drive"])
        .agg(
            (pl.col("touchdown").fill_null(0) * (pl.col("td_team") == pl.col("posteam")))
            .max()
            .alias("_drive_td")
        )
        .group_by("posteam")
        .agg(
            pl.len().alias("red_zone_trips"),
            pl.col("_drive_td").sum().alias("_red_zone_tds"),
        )
        .rename({"posteam": "team"})
    )

    frame = (
        base.join(neutral, on="team", how="left")
        .join(red_zone, on="team", how="left")
        .collect()
    )

    points = _points_for(season)
    as_of = sources.season_end_date(season)

    frame = (
        frame.join(points, on="team", how="left")
        .with_columns(
            pl.lit(season).cast(pl.Int32).alias("season"),
            pl.lit(as_of).alias("as_of"),
            pl.lit(as_of).alias("source_as_of"),
            pl.lit("derived").alias("value_type"),
            (pl.col("plays") / pl.col("games")).alias("plays_per_game"),
            (pl.col("passing_tds") + pl.col("rushing_tds")).alias("offensive_tds"),
            (
                pl.col("pass_attempts") / (pl.col("pass_attempts") + pl.col("rush_attempts"))
            ).alias("pass_rate"),
            (
                pl.col("_neutral_pass") / (pl.col("_neutral_pass") + pl.col("_neutral_rush"))
            ).alias("neutral_pass_rate"),
            (pl.col("yards") / pl.col("plays")).alias("yards_per_play"),
            (pl.col("_red_zone_tds") / pl.col("red_zone_trips")).alias("red_zone_td_rate"),
            (pl.col("interceptions") + pl.col("fumbles_lost")).alias("turnovers"),
        )
        .select(
            "season", "team", "as_of", "source_as_of", "value_type",
            "games", "plays", "plays_per_game", "points",
            "offensive_tds", "passing_tds", "rushing_tds",
            "pass_attempts", "rush_attempts", "pass_rate", "neutral_pass_rate",
            "yards_per_play", "red_zone_trips", "red_zone_td_rate", "turnovers",
        )
        .sort("team")
    )

    assert_as_of_present(frame, f"team_season[{season}]")
    return frame


def _points_for(season: int) -> pl.DataFrame:
    games = sources.schedule().filter(
        (pl.col("season") == season) & (pl.col("game_type") == "REG")
    )
    home = games.select(pl.col("home_team").alias("team"), pl.col("home_score").alias("points"))
    away = games.select(pl.col("away_team").alias("team"), pl.col("away_score").alias("points"))
    return (
        pl.concat([home, away])
        .group_by("team")
        .agg(pl.col("points").cast(pl.Float64).sum().alias("points"))
    )
