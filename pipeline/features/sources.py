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


# Key dtypes drift across nflverse seasons -- `injuries` publishes season and
# week as Float64 before 2024 and Int32 after -- which turns a join into a
# schema error halfway through a 14-season build. Normalize on load rather than
# at every call site.
KEY_DTYPES: dict[str, pl.DataType] = {
    "season": pl.Int32,
    "week": pl.Int32,
}

# nflverse spells the same franchise differently depending on which file you
# open, and the differences are invisible until a join quietly drops rows:
#
#   play_by_play / player_stats  current code, retroactively applied  (LA, LAC, LV)
#   roster_weekly                era-contemporary, PFR-flavoured      (ARZ, BLT, CLV, HST, SD, SL)
#   games.csv                    era-contemporary, NFL-flavoured      (STL, SD, OAK)
#
# Left alone this splits one franchise into two: 2012 Arizona appears as ARI on
# the rows of players who played and ARZ on the rows of players who did not,
# and `points` goes missing for every Rams, Chargers and Raiders season before
# their moves -- 17 of 448 team-seasons, silently, on the column S25 regresses.
#
# Distinct from names.TEAM_ALIASES, which maps in the other direction (LA ->
# LAR) to meet Fantasy Football Calculator's spelling. That is the matching
# layer; this is the table layer. They are not interchangeable.
TEAM_CODE_COLUMNS = ("team", "posteam", "defteam", "recent_team", "home_team", "away_team")

CANONICAL_TEAM_CODES = {
    "ARZ": "ARI",
    "BLT": "BAL",
    "CLV": "CLE",
    "HST": "HOU",
    "SD": "LAC",
    "SL": "LA",
    "STL": "LA",
    "LAR": "LA",
    "OAK": "LV",
    "LVR": "LV",
    "WSH": "WAS",
}


def normalize_team_codes(frame: pl.DataFrame) -> pl.DataFrame:
    """Map every team column onto the play-by-play spelling."""
    present = [c for c in TEAM_CODE_COLUMNS if c in frame.columns]
    if not present:
        return frame
    return frame.with_columns(
        pl.col(c).replace(CANONICAL_TEAM_CODES).alias(c) for c in present
    )


def normalize_keys(frame: pl.DataFrame) -> pl.DataFrame:
    casts = [
        pl.col(col).cast(dtype)
        for col, dtype in KEY_DTYPES.items()
        if col in frame.columns and frame.schema[col] != dtype
    ]
    return frame.with_columns(casts) if casts else frame


def load(filename: str) -> pl.DataFrame:
    return normalize_team_codes(normalize_keys(pl.read_parquet(raw_path(filename))))


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
            f"{SCHEDULE_FILE} not found. Run `research ingest` -- it fetches the game "
            "calendar alongside the release assets (S85.1)."
        )
    return normalize_team_codes(
        pl.read_csv(SCHEDULE_FILE, infer_schema_length=20000).with_columns(
            pl.col("gameday").str.to_date(strict=False).alias("gameday")
        )
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
