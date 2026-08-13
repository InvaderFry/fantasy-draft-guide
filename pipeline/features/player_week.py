"""player_week: one row per player / season / week (S13).

Sources: nflverse weekly rosters (who was on the roster, and on what list),
player_stats (production), snap_counts (usage and evidence of appearing),
play-by-play (red-zone and goal-line opportunity, team context), injuries
(designation and reported injury).

The population is the **roster**, not the stat sheet. A table built from
weekly stat rows can only describe players who played, so `active_status`
collapses to a constant and the IR, inactive and DNP weeks that S15.1 needs in
order to separate availability from per-game production are simply absent.
Stats are left-joined onto the roster instead, and a missing stat row is
information rather than an omission.

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

# Roster lists that count as "on the team this week". DEV (practice squad) is
# excluded: a practice-squad week is not a missed game. Elevated practice-squad
# players still reach the table, because any week with a stat row or a snap is
# unioned into the population below.
ROSTERED_STATUSES = ("ACT", "INA", "RES")

# NFL reserve-list transaction codes, as they appear in
# `status_description_abbr`. These were read off the 2024 weekly roster file and
# checked against known cases rather than assumed, because the classification
# they drive is the difference between "missed 13 games" and "missed 13 games,
# 1 of them injury-related":
#
#   R01  Reserve/Injured .............. McCaffrey, weeks 2-8 and 14-18
#   R48  Reserve/Injured, designated to return ... only ever follows R01
#   R04  Reserve/PUP ................... Bradley Chubb, Abraham Lucas
#   R05  Reserve/Non-Football Injury ... Jonathon Brooks, Jamal Adams
#   R27  Reserve/Non-Football Illness .. BJ Thompson, MarShawn Lloyd
#   R47  the designated-to-return variant of R27 ... Christian Barmore
#   R49  the designated-to-return variant of R05/R27
INJURY_RESERVE_CODES = ("R01", "R04", "R05", "R27", "R47", "R48", "R49")

#   R33  Reserve/Suspended by club ......... Diontae Johnson, weeks 15-18
#   R40  Reserve/Suspended by commissioner .. Cameron Sutton, Azeez Al-Shaair
SUSPENSION_RESERVE_CODES = ("R33", "R40")

# Every other reserve code is a real absence that is not a health absence and
# not a suspension -- R03 did not report (Haason Reddick's holdout), R06 left
# squad, R23 reserve/future (postseason only) -- and is labelled `unknown`
# rather than folded into one of the categories it is not.

# An injury-report designation strong enough to explain a missed game. A player
# listed Questionable who then does not appear was ruled out at gametime.
INJURY_REPORT_STATUSES = ("Out", "Doubtful", "Questionable")

# nflverse moved weekly player stats to a new release with renamed columns
# (the `stats_player` release, which is the only one carrying 2025+). Seasons
# straddle the two shapes, so normalize to the legacy names the builder uses.
COLUMN_ALIASES = {
    "team": "recent_team",
    "passing_interceptions": "interceptions",
    "sacks_suffered": "sacks",
    "sack_yards_lost": "sack_yards",
}

# Filled with 0 on a week the player did not play: they recorded none of it.
# Rate columns (target_share, snap_share, air_yard_share) are deliberately left
# null instead -- a share of a game that was not played is undefined, not zero.
COUNTING_STATS = (
    "fantasy_points_standard", "fantasy_points_half_ppr", "fantasy_points_ppr",
    "targets", "receptions", "receiving_yards", "receiving_tds", "air_yards",
    "carries", "rushing_yards", "rushing_tds",
    "passing_yards", "passing_tds", "interceptions", "fumbles_lost",
    "red_zone_targets", "red_zone_carries", "goal_line_carries",
)


def _normalize_columns(frame: pl.DataFrame) -> pl.DataFrame:
    renames = {
        new: old
        for new, old in COLUMN_ALIASES.items()
        if new in frame.columns and old not in frame.columns
    }
    return frame.rename(renames) if renames else frame


def build(season: int) -> pl.DataFrame:
    stats = _weekly_stats(season)
    roster = _roster_weeks(season)
    snaps = _snap_counts(season)
    pbp_player, pbp_team = _pbp_aggregates(season)
    injuries = _injury_status(season)
    weeks = sources.week_end_dates(season)

    key = ["season", "week", "player_id"]
    if roster is None:
        # No weekly roster file for this season: the population falls back to
        # players who recorded something. Say so through the data rather than
        # silently reporting every row as active -- `active_status` is null and
        # the availability columns derived from it are null too.
        frame = stats.with_columns(
            pl.lit(None, dtype=pl.String).alias("roster_status"),
            pl.lit(None, dtype=pl.String).alias("roster_code"),
            pl.lit(None, dtype=pl.String).alias("roster_team"),
            pl.col("stats_position").alias("roster_position"),
        )
    else:
        # Full join, not left: a practice-squad elevation carries a stat row
        # without an ACT/INA/RES roster row, and dropping it would report more
        # games than weeks.
        frame = roster.join(stats, on=key, how="full", coalesce=True)

    # The stat sheet's position wins when it is a fantasy position at all. Where
    # it is not, the depth chart does: a cornerback used as a receiver or a
    # defensive tackle lined up at fullback records real fantasy production, and
    # filtering the stat rows on their own position column dropped it.
    frame = frame.with_columns(
        pl.coalesce("team", "roster_team").alias("team"),
        pl.when(pl.col("stats_position").is_in(FANTASY_POSITIONS))
        .then(pl.col("stats_position"))
        .otherwise(pl.col("roster_position"))
        .alias("position"),
    ).filter(pl.col("position").is_in(FANTASY_POSITIONS))

    frame = (
        frame.join(snaps, on=key, how="left")
        .join(pbp_player, on=key, how="left")
        .join(pbp_team, on=["season", "week", "team"], how="left")
        .join(injuries, on=key, how="left")
        .join(weeks, on="week", how="left")
    )

    # Two independent pieces of evidence that the player was in the game: the
    # stat sheet, and the snap count. Neither is complete on its own -- 729 of
    # 2024's active-roster player-weeks took offensive snaps without producing a
    # stat line, and some stat rows (return touchdowns, crosswalk gaps) have no
    # snap row.
    played = pl.col("_has_stats").is_not_null() | pl.col("_has_snaps").is_not_null()
    frame = frame.with_columns(
        pl.when(played).then(1).otherwise(0).alias("games_active"),
    )

    frame = frame.with_columns(
        _active_status().alias("active_status"),
        _inactive_reason().alias("inactive_reason"),
        pl.col("week_end").alias("as_of"),
        pl.col("week_end").alias("source_as_of"),
        pl.lit("observed").alias("value_type"),
        *[pl.col(c).fill_null(0).alias(c) for c in COUNTING_STATS],
    ).drop(
        "report_status", "report_injury", "week_end", "week_start",
        "roster_status", "roster_code", "roster_team", "roster_position", "stats_position",
        "_has_stats", "_has_snaps", "_on_injury_report",
    )

    assert_as_of_present(frame, f"player_week[{season}]")
    return frame


def _active_status() -> pl.Expr:
    """active | inactive | ir | dnp (S13).

    `ir` is a health reserve list specifically -- Reserve/Suspended is a reserve
    list too, and reads as `inactive`. `dnp` is the residual: on the active
    roster, no stat line and no snap. It covers healthy scratches the roster
    file did not mark inactive as well as players whose appearance neither
    source recorded.
    """
    return (
        pl.when(pl.col("games_active") == 1)
        .then(pl.lit("active"))
        .when(pl.col("roster_status").is_null())
        .then(pl.lit(None, dtype=pl.String))
        .when(pl.col("roster_code").is_in(INJURY_RESERVE_CODES))
        .then(pl.lit("ir"))
        .when(pl.col("roster_status").is_in(["RES", "INA"]))
        .then(pl.lit("inactive"))
        .otherwise(pl.lit("dnp"))
    )


def _inactive_reason() -> pl.Expr:
    """injury | coach | suspension | unknown, for weeks the player did not play.

    Reserve codes come first because they are the reliable signal: a player on
    IR or PUP leaves the weekly injury report entirely, which is why counting
    only `report_status == "Out"` reported one injury-related absence for a
    player who missed thirteen games with a torn Achilles.
    """
    return (
        pl.when(pl.col("games_active") == 1)
        .then(pl.lit(None, dtype=pl.String))
        .when(pl.col("roster_code").is_in(INJURY_RESERVE_CODES))
        .then(pl.lit("injury"))
        .when(pl.col("roster_code").is_in(SUSPENSION_RESERVE_CODES))
        .then(pl.lit("suspension"))
        .when(pl.col("report_status").is_in(INJURY_REPORT_STATUSES))
        .then(pl.lit("injury"))
        .when(pl.col("report_injury").is_not_null())
        .then(pl.lit("injury"))
        .when(pl.col("roster_status") == "RES")
        .then(pl.lit("unknown"))  # a reserve list that is neither health nor discipline
        .when(pl.col("roster_status").is_null())
        .then(pl.lit(None, dtype=pl.String))
        .when(pl.col("_on_injury_report").is_not_null())
        .then(pl.lit("unknown"))  # on the report, but nothing that explains the absence
        .otherwise(pl.lit("coach"))  # inactive and not on the injury report at all
    )


def _roster_weeks(season: int) -> pl.DataFrame | None:
    """The population: every fantasy-position player on a roster, by week.

    Returns None when the season has no weekly roster file, which is the honest
    answer for a season whose availability cannot be reconstructed.
    """
    try:
        roster = sources.load(f"roster_weekly_{season}.parquet")
    except sources.MissingRawData:
        return None

    reg_weeks = sources.week_end_dates(season)["week"].to_list()
    # depth_chart_position is the finer of the two: the roster `position`
    # column folds fullbacks into RB.
    return (
        roster.filter(
            pl.col("status").is_in(ROSTERED_STATUSES)
            & pl.col("week").is_in(reg_weeks)
            & pl.col("gsis_id").is_not_null()
        )
        .select(
            pl.col("season"),
            pl.col("week"),
            pl.col("gsis_id").alias("player_id"),
            pl.col("team").alias("roster_team"),
            pl.coalesce("depth_chart_position", "position").alias("roster_position"),
            pl.col("status").alias("roster_status"),
            pl.col("status_description_abbr").alias("roster_code"),
        )
        .filter(pl.col("roster_position").is_in(FANTASY_POSITIONS))
        .unique(subset=["season", "week", "player_id"], keep="first")
    )


def _weekly_stats(season: int) -> pl.DataFrame:
    """Weekly production, scored, one row per player-week actually recorded."""
    stats = _normalize_columns(
        sources.load(f"player_stats_{season}.parquet")
    ).filter(pl.col("season_type") == "REG")

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

    return stats.select(
        pl.col("season"),
        pl.col("week"),
        pl.col("player_id"),
        pl.col("position").alias("stats_position"),
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
        pl.lit(1).alias("_has_stats"),
    ).unique(subset=["season", "week", "player_id"], keep="first")


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
    team = (
        team.join(team_points, on=["season", "week", "team"], how="left")
        .join(_team_air_yards(season), on=["season", "week", "team"], how="left")
    )
    return player, team


def _team_air_yards(season: int) -> pl.DataFrame:
    """Air yards thrown by each team each week (S13).

    Summed from the same column the player numerator comes from, and over every
    position rather than the fantasy subset, so a player's share of it is
    internally consistent. nflverse publishes its own ``air_yards_share`` off a
    slightly different denominator; using theirs would leave the season
    aggregate disagreeing with the weekly rows it is built from.
    """
    stats = _normalize_columns(
        sources.load(f"player_stats_{season}.parquet")
    ).filter(pl.col("season_type") == "REG")
    return (
        stats.group_by(["season", "week", pl.col("recent_team").alias("team")])
        .agg(pl.col("receiving_air_yards").fill_null(0).sum().alias("team_air_yards"))
    )


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
    """Snap counts keyed by pfr_player_id -- joined to gsis_id via the crosswalk (S12).

    A row here is also evidence the player appeared, which the stat sheet alone
    does not give: a blocking tight end or a rotational back can play a full
    game and record nothing.
    """
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
            pl.lit(1).alias("_has_snaps"),
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
            pl.col("report_primary_injury").alias("report_injury"),
            pl.lit(1).alias("_on_injury_report"),
        )
        .drop_nulls("player_id")
        .unique(subset=["season", "week", "player_id"], keep="first")
    )
