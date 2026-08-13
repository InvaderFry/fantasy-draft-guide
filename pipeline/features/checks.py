"""Data quality checks over the built tables (S51).

Run by ``research validate``. Each check returns a message and a pass flag so
the CLI can report every problem rather than stopping at the first.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from pipeline.config import PROCESSED_DIR, decision_dates
from pipeline.features.schema import VALUE_TYPES

MIN_SEASON = 2012


def run_all(processed_dir: Path = PROCESSED_DIR) -> list[tuple[str, bool]]:
    results: list[tuple[str, bool]] = []
    for name, fn in (
        ("player_week", _check_player_week),
        ("player_season", _check_player_season),
        ("player_season_outcomes", _check_availability_coverage),
        ("team_season", _check_team_season),
        ("adp_history", _check_adp_history),
    ):
        path = processed_dir / f"{name}.parquet"
        if not path.exists():
            results.append((f"{name}: not built yet, skipped", True))
            continue
        results.extend(fn(pl.read_parquet(path)))
    return results


def _ok(message: str) -> tuple[str, bool]:
    return (f"  ok  {message}", True)


def _fail(message: str) -> tuple[str, bool]:
    return (f"FAIL  {message}", False)


def _season_check(frame: pl.DataFrame, name: str) -> list[tuple[str, bool]]:
    seasons = frame["season"].unique().to_list()
    bad = [s for s in seasons if s is None or s < MIN_SEASON or s > max(decision_dates())]
    return [_ok(f"{name}: seasons valid") if not bad else _fail(f"{name}: invalid seasons {bad}")]


def _value_type_check(frame: pl.DataFrame, name: str) -> list[tuple[str, bool]]:
    if "value_type" not in frame.columns:
        return [_fail(f"{name}: missing value_type (S37)")]
    bad = set(frame["value_type"].unique().to_list()) - VALUE_TYPES
    return [
        _ok(f"{name}: value_type valid")
        if not bad
        else _fail(f"{name}: invalid value_type {sorted(bad)}")
    ]


def _check_player_week(frame: pl.DataFrame) -> list[tuple[str, bool]]:
    out = _season_check(frame, "player_week") + _value_type_check(frame, "player_week")

    dupes = frame.group_by(["season", "week", "player_id"]).len().filter(pl.col("len") > 1).height
    out.append(
        _ok("player_week: keys unique")
        if dupes == 0
        else _fail(f"player_week: {dupes} duplicate season/week/player_id keys")
    )

    nulls = frame.filter(pl.col("player_id").is_null()).height
    out.append(
        _ok("player_week: player_id populated")
        if nulls == 0
        else _fail(f"player_week: {nulls} rows with a null player_id (S12)")
    )

    for col in ("snap_share", "target_share"):
        if col not in frame.columns:
            continue
        bad = frame.filter(pl.col(col).is_not_null() & ~pl.col(col).is_between(0, 1)).height
        out.append(
            _ok(f"player_week: {col} within 0-1")
            if bad == 0
            else _fail(f"player_week: {bad} rows with {col} outside 0-1")
        )

    # air_yard_share is NOT bounded by 0-1: air yards are negative on targets
    # behind the line of scrimmage, so a player's share of a small (or
    # negative-leaning) team total legitimately falls outside the unit
    # interval. About 17% of weekly rows are negative. The bound below catches
    # a broken denominator without flagging the metric's real behaviour.
    if "air_yard_share" in frame.columns:
        bad = frame.filter(
            pl.col("air_yard_share").is_not_null()
            & ~pl.col("air_yard_share").is_between(-3, 3)
        ).height
        out.append(
            _ok("player_week: air_yard_share within -3..3 (negative air yards are real)")
            if bad == 0
            else _fail(f"player_week: {bad} rows with air_yard_share outside -3..3")
        )
    return out


def _check_player_season(frame: pl.DataFrame) -> list[tuple[str, bool]]:
    out = _season_check(frame, "player_season") + _value_type_check(frame, "player_season")
    dupes = frame.group_by(["season", "player_id"]).len().filter(pl.col("len") > 1).height
    out.append(
        _ok("player_season: keys unique")
        if dupes == 0
        else _fail(f"player_season: {dupes} duplicate season/player_id keys")
    )
    if "age" in frame.columns:
        bad = frame.filter(pl.col("age").is_not_null() & ~pl.col("age").is_between(18, 48)).height
        out.append(
            _ok("player_season: age plausible")
            if bad == 0
            else _fail(f"player_season: {bad} rows with implausible age")
        )
    return out


def _check_team_season(frame: pl.DataFrame) -> list[tuple[str, bool]]:
    out = _season_check(frame, "team_season") + _value_type_check(frame, "team_season")
    counts = frame.group_by("season").len()
    bad = counts.filter(pl.col("len") != 32)
    out.append(
        _ok(f"team_season: 32 teams x {counts.height} season(s)")
        if bad.height == 0
        else _fail(f"team_season: seasons without 32 teams: {bad.to_dicts()}")
    )
    if "pass_rate" in frame.columns:
        bad_rate = frame.filter(
            pl.col("pass_rate").is_not_null() & ~pl.col("pass_rate").is_between(0.2, 0.85)
        ).height
        out.append(
            _ok("team_season: pass_rate plausible")
            if bad_rate == 0
            else _fail(f"team_season: {bad_rate} implausible pass_rate rows")
        )
    return out


def _check_adp_history(frame: pl.DataFrame) -> list[tuple[str, bool]]:
    if frame.height == 0:
        return [_ok("adp_history: empty (no snapshots captured yet)")]
    out = _season_check(frame, "adp_history") + _value_type_check(frame, "adp_history")
    out.extend(_check_adp_against_decision_dates(frame))
    bad = frame.filter(pl.col("adp").is_not_null() & ~pl.col("adp").is_between(1, 400)).height
    out.append(
        _ok("adp_history: adp within 1-400")
        if bad == 0
        else _fail(f"adp_history: {bad} rows with implausible adp")
    )
    unmatched = frame.filter(pl.col("match_method") == "unmatched").height
    share = unmatched / frame.height
    out.append(
        _ok(f"adp_history: {unmatched} unmatched rows ({share:.1%})")
        if share < 0.15
        else _fail(
            f"adp_history: {unmatched} unmatched rows ({share:.1%}) -- triage into "
            "config/manual_id_overrides.yaml (S12)"
        )
    )
    return out


# Reserve-list transaction codes are populated from 2020. Before that the
# weekly roster file records that a player was on a reserve list but not why,
# and 2012-2015 do not mark game-day inactives at all, so an absence can only be
# called injury-related when the weekly injury report happens to name it.
FIRST_SEASON_WITH_RESERVE_CODES = 2020

# 2020 has reserve codes but also Reserve/COVID-19 and Reserve/Opt-out, which
# are absences and not injuries. Its share sits near 38% for that reason rather
# than because anything is wrong with it.
PANDEMIC_SEASON = 2020

# Seasons with full coverage classify roughly 60% of missed games as
# injury-related. A season far below that is reporting a source limitation, not
# a healthier league.
EXPECTED_INJURY_SHARE = 0.45


def _check_availability_coverage(frame: pl.DataFrame) -> list[tuple[str, bool]]:
    """How much of `games_missed` can be explained, season by season (S15.1).

    Not a pass/fail on the data so much as a warning about comparing across
    2020: the classification improves sharply there because the source does,
    and an availability model fitted across the whole window would read that
    discontinuity as a change in the game.
    """
    if "games_missed" not in frame.columns:
        return [_fail("player_season_outcomes: missing games_missed (S15.1)")]

    by_season = (
        frame.group_by("season")
        .agg(
            pl.col("games_missed").sum().alias("missed"),
            pl.col("games_missed_injury").sum().alias("injury"),
        )
        .with_columns(
            pl.when(pl.col("missed") > 0)
            .then(pl.col("injury") / pl.col("missed"))
            .alias("share")
        )
        .sort("season")
    )

    thin = by_season.filter(
        (pl.col("season") >= FIRST_SEASON_WITH_RESERVE_CODES)
        & (pl.col("season") != PANDEMIC_SEASON)
        & (pl.col("share") < EXPECTED_INJURY_SHARE)
    )
    out = [
        _fail(
            "player_season_outcomes: injury-classified share below "
            f"{EXPECTED_INJURY_SHARE:.0%} in {thin['season'].to_list()} despite reserve "
            "codes being available -- the reserve-code mapping in player_week has "
            "probably drifted from the source"
        )
        if thin.height
        else _ok("player_season_outcomes: injury share as expected where codes exist")
    ]

    pandemic = by_season.filter(pl.col("season") == PANDEMIC_SEASON)
    if pandemic.height:
        out.append(
            _ok(
                f"player_season_outcomes: {PANDEMIC_SEASON} at "
                f"{pandemic['share'][0]:.0%} -- Reserve/COVID-19 and Reserve/Opt-out "
                "are absences, not injuries, and are counted as neither"
            )
        )

    legacy = by_season.filter(pl.col("season") < FIRST_SEASON_WITH_RESERVE_CODES)
    if legacy.height:
        shares = ", ".join(
            f"{row['season']}: {row['share']:.0%}" for row in legacy.to_dicts()
        )
        out.append(
            _ok(
                "player_season_outcomes: pre-2020 seasons carry no reserve codes, so "
                f"games_missed_injury undercounts there ({shares}). Do not pool these "
                f"with {FIRST_SEASON_WITH_RESERVE_CODES}+ in an availability model (S15.1)"
            )
        )
    return out


def _check_adp_against_decision_dates(frame: pl.DataFrame) -> list[tuple[str, bool]]:
    """How each season's ADP sits relative to the date we pretend to draft on (S6.1).

    Deliberately a report rather than an assertion. Fantasy Football Calculator
    serves a historical season as its *final preseason* window -- 2025 comes
    back covering 2025-08-25 to 2025-09-01 -- while `decision_dates.yaml` puts
    the 2025 draft on 2025-08-23. `assert_knowable` would therefore fail on
    data that is correct and is the only historical ADP that exists.

    The honest handling is to say how large the gap is, so an analysis that
    buckets players by price knows it is using a price fixed a few days after
    the notional draft, not to pretend the gap is not there.
    """
    if "window_end" not in frame.columns:
        return [_fail("adp_history: no window_end -- captures predate the S31.3 window fix")]

    dates = decision_dates()
    late = []
    for row in (
        frame.group_by("season")
        .agg(pl.col("as_of").max().alias("as_of"))
        .sort("season")
        .to_dicts()
    ):
        cutoff = dates.get(row["season"])
        if cutoff and row["as_of"] and row["as_of"] > cutoff:
            late.append(f"{row['season']}: +{(row['as_of'] - cutoff).days}d")
    if not late:
        return [_ok("adp_history: every season's ADP predates its decision date")]
    return [
        _ok(
            "adp_history: ADP postdates the decision date for " + ", ".join(late)
            + " -- FFC serves a historical season as its final preseason window, so a "
            "bucket analysis is using a price fixed after the notional draft (S6.1, S21.1)"
        )
    ]
