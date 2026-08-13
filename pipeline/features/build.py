"""Canonical table orchestration (S47 stages 4-6).

Builds player_week, player_season (+ outcomes), team_season and adp_history for
a season range and writes them to ``data/processed/``.

Every builder runs its own as-of assertions, and this module additionally runs
the forward-looking check: a season's features must be knowable at the NEXT
season's decision date, since that is the season they would be used to predict.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from pipeline.config import PROCESSED_DIR, ConfigError
from pipeline.features import (
    adp_history,
    player_season,
    player_week,
    projections,
    team_season,
)
from pipeline.features.assertions import assert_knowable

TABLES = (
    "player_week",
    "player_season",
    "team_season",
    "adp_history",
    "projection_snapshot",
)


def build_all(
    seasons: list[int],
    *,
    tables: list[str] | None = None,
    output_dir: Path = PROCESSED_DIR,
) -> dict[str, Path]:
    wanted = tables or list(TABLES)
    unknown = [t for t in wanted if t not in TABLES]
    if unknown:
        raise ValueError(f"unknown table(s) {unknown}; known: {list(TABLES)}")

    output_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    weekly_by_season: dict[int, pl.DataFrame] = {}

    if "player_week" in wanted or "player_season" in wanted:
        for season in seasons:
            weekly_by_season[season] = player_week.build(season)

    if "player_week" in wanted:
        frame = pl.concat(list(weekly_by_season.values()), how="diagonal_relaxed")
        written["player_week"] = _write(frame, output_dir / "player_week.parquet")

    if "player_season" in wanted:
        features, outcomes = [], []
        for season in seasons:
            f, o = player_season.build(season, weekly=weekly_by_season[season])
            _assert_usable_next_season(f, season, "player_season")
            features.append(f)
            outcomes.append(o)
        written["player_season"] = _write(
            pl.concat(features, how="diagonal_relaxed"), output_dir / "player_season.parquet"
        )
        written["player_season_outcomes"] = _write(
            pl.concat(outcomes, how="diagonal_relaxed"),
            output_dir / "player_season_outcomes.parquet",
        )

    if "team_season" in wanted:
        frames = []
        for season in seasons:
            frame = team_season.build(season)
            _assert_usable_next_season(frame, season, "team_season")
            frames.append(frame)
        written["team_season"] = _write(
            pl.concat(frames, how="diagonal_relaxed"), output_dir / "team_season.parquet"
        )

    if "adp_history" in wanted:
        frame = adp_history.build()
        written["adp_history"] = _write(frame, output_dir / "adp_history.parquet")

    if "projection_snapshot" in wanted:
        frame = projections.build()
        written["projection_snapshot"] = _write(
            frame, output_dir / "projection_snapshot.parquet"
        )

    return written


def _assert_usable_next_season(frame: pl.DataFrame, season: int, name: str) -> None:
    """A season-Y aggregate is a feature for season Y+1, and must be knowable then."""
    try:
        assert_knowable(frame, season + 1, f"{name}[{season}] used for {season + 1}")
    except ConfigError:
        # No decision date configured for season+1 yet. Every other exception
        # propagates: a bare `except Exception` here turned a dtype drift in
        # `as_of` -- the exact failure mode this repo has already hit -- into a
        # silently skipped leakage check on the whole build.
        return


def _write(frame: pl.DataFrame, path: Path) -> Path:
    frame.write_parquet(path)
    return path
