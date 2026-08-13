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
