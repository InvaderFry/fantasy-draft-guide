"""Loaders for raw nflverse files, plus the game calendar that dates them (S6.1).

Every canonical table needs to answer "when did this become knowable?", and for
weekly football data the answer is the calendar: a week's production is knowable
once that week's games have been played.
"""

from __future__ import annotations

import datetime as dt
import functools
from pathlib import Path

import polars as pl

from pipeline.config import RAW_DIR

NFLVERSE_DIR = RAW_DIR / "nflverse"
SCHEDULE_FILE = NFLVERSE_DIR / "games.csv"


class MissingRawData(FileNotFoundError):
    pass


def raw_path(filename: str) -> Path:
    path = NFLVERSE_DIR / filename
    if not path.exists():
        raise MissingRawData(
            f"{path} not found. Run `research ingest` for the seasons you are building (S10A)."
        )
    return path


def load(filename: str) -> pl.DataFrame:
    return pl.read_parquet(raw_path(filename))


def available_seasons(prefix: str = "player_stats_") -> list[int]:
    seasons = []
    for path in NFLVERSE_DIR.glob(f"{prefix}*.parquet"):
        stem = path.stem.replace(prefix, "")
        if stem.isdigit():
            seasons.append(int(stem))
    return sorted(seasons)


@functools.cache
def schedule() -> pl.DataFrame:
    """Game calendar with parsed dates."""
    if not SCHEDULE_FILE.exists():
        raise MissingRawData(
            f"{SCHEDULE_FILE} not found. Run `research ingest --datasets schedules` (S85.1)."
        )
    return pl.read_csv(SCHEDULE_FILE, infer_schema_length=20000).with_columns(
        pl.col("gameday").str.to_date(strict=False).alias("gameday")
    )


@functools.cache
def week_end_dates(season: int) -> pl.DataFrame:
    """Last gameday per (season, week) -- the date that week became knowable."""
    return (
        schedule()
        .filter((pl.col("season") == season) & (pl.col("game_type") == "REG"))
        .group_by("week")
        .agg(
            pl.col("gameday").max().alias("week_end"),
            pl.col("gameday").min().alias("week_start"),
        )
        .sort("week")
    )


def season_end_date(season: int) -> dt.date:
    """Last regular-season gameday: when a season aggregate became knowable."""
    weeks = week_end_dates(season)
    if weeks.height == 0:
        raise MissingRawData(f"no regular-season schedule rows for {season}")
    return weeks.select(pl.col("week_end").max()).item()
