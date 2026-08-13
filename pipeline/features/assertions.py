"""Leakage assertions (S6.1).

r1 asked for a test that "outcomes do not use future data" and could not
implement it, because nothing recorded when a value became knowable. The
``as_of`` column supplies that, and these assertions turn leakage from a
question a reviewer might think to ask into an exception that stops the build.

They are called by the builders, not by a checklist.
"""

from __future__ import annotations

import datetime as dt

import polars as pl

from pipeline.config import decision_date
from pipeline.features.schema import AS_OF_COLUMNS, VALUE_TYPES, outcome_columns_in


class LeakageError(AssertionError):
    """A feature row postdates the decision date it would be used at."""


def assert_as_of_present(frame: pl.DataFrame, name: str = "frame") -> None:
    missing = [c for c in AS_OF_COLUMNS if c not in frame.columns]
    if missing:
        raise LeakageError(
            f"{name} is missing required as-of columns {missing}. S6.1 requires every "
            "feature row to carry as_of, source_as_of and value_type from the first table."
        )
    null_as_of = frame.filter(pl.col("as_of").is_null()).height
    if null_as_of:
        raise LeakageError(f"{name}: {null_as_of} row(s) have a null as_of (S6.1)")
    bad_types = (
        frame.filter(~pl.col("value_type").is_in(list(VALUE_TYPES)))
        .select("value_type")
        .unique()
        .to_series()
        .to_list()
    )
    if bad_types:
        raise LeakageError(
            f"{name}: invalid value_type(s) {bad_types}; allowed {sorted(VALUE_TYPES)}"
        )


def assert_knowable(frame: pl.DataFrame, season: int, name: str = "frame") -> None:
    """Every feature row must have been knowable at the season's decision date.

    Catches the cases review reliably misses: end-of-season roster data used as
    a preseason feature, a statistic backfilled by the provider after the fact,
    an injury designation assigned in November, a player_season aggregate
    joined onto a preseason population.
    """
    assert_as_of_present(frame, name)
    cutoff = decision_date(season)
    bad = frame.filter(pl.col("as_of") > pl.lit(cutoff))
    if bad.height:
        cols = [c for c in ("player_id", "team", "season", "as_of") if c in bad.columns]
        raise LeakageError(
            f"{bad.height} row(s) in {name} postdate the {season} decision date {cutoff}:\n"
            f"{bad.select(cols).head(10)}"
        )


def assert_no_outcome_columns(frame: pl.DataFrame, name: str = "frame") -> None:
    """A feature frame must not carry outcome-flagged columns (S6.1)."""
    if frame.columns and "is_outcome" in frame.columns:
        raise LeakageError(
            f"{name} carries is_outcome and is therefore an outcome table, not a feature frame"
        )
    present = outcome_columns_in(frame.columns)
    if present:
        raise LeakageError(
            f"{name} carries outcome column(s) {present}. Outcomes live in their own tables "
            "and are joined only from a season strictly earlier than the one being "
            "predicted (S6.1)."
        )


def max_as_of(frame: pl.DataFrame) -> dt.date | None:
    if "as_of" not in frame.columns or frame.height == 0:
        return None
    return frame.select(pl.col("as_of").max()).item()
