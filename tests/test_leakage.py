"""Leakage assertions (S6.1, S51).

These are the tests r1 could not implement. They exist because `as_of` records
when a value became knowable, which turns "outcomes do not use future data"
from a review checklist item into an exception that stops the build.
"""

import datetime as dt

import polars as pl
import pytest

from pipeline.features.assertions import (
    LeakageError,
    assert_as_of_present,
    assert_knowable,
    assert_no_outcome_columns,
)


def test_a_frame_without_as_of_is_rejected():
    frame = pl.DataFrame({"season": [2023], "player_id": ["x"]})
    with pytest.raises(LeakageError, match="as-of columns"):
        assert_as_of_present(frame)


def test_a_null_as_of_is_rejected(feature_frame):
    frame = feature_frame.with_columns(pl.lit(None, dtype=pl.Date).alias("as_of"))
    with pytest.raises(LeakageError, match="null as_of"):
        assert_as_of_present(frame)


def test_an_invalid_value_type_is_rejected(feature_frame):
    frame = feature_frame.with_columns(pl.lit("guessed").alias("value_type"))
    with pytest.raises(LeakageError, match="value_type"):
        assert_as_of_present(frame)


def test_prior_season_features_are_knowable_at_the_next_decision_date(feature_frame):
    """A 2023 season aggregate is a legitimate 2024 feature."""
    assert_knowable(feature_frame, 2024)


def test_same_season_features_are_a_leakage_violation(feature_frame):
    """The same 2023 aggregate is NOT knowable at the 2023 draft."""
    with pytest.raises(LeakageError, match="postdate the 2023 decision date"):
        assert_knowable(feature_frame, 2023)


def test_a_single_future_dated_row_stops_the_build(feature_frame):
    """The subtle case: one backfilled or revised row among many good ones."""
    poisoned = pl.concat(
        [
            feature_frame,
            feature_frame.head(1).with_columns(
                pl.lit(dt.date(2024, 12, 1)).alias("as_of"),
                pl.lit("00-0000003").alias("player_id"),
            ),
        ]
    )
    with pytest.raises(LeakageError, match="1 row"):
        assert_knowable(poisoned, 2024)


def test_outcome_columns_may_not_enter_a_feature_frame(feature_frame):
    frame = feature_frame.with_columns(pl.lit(210.5).alias("fantasy_points_ppr"))
    with pytest.raises(LeakageError, match="outcome column"):
        assert_no_outcome_columns(frame)


def test_an_outcome_table_is_not_mistaken_for_a_feature_frame(feature_frame):
    frame = feature_frame.with_columns(pl.lit(True).alias("is_outcome"))
    with pytest.raises(LeakageError, match="is_outcome"):
        assert_no_outcome_columns(frame)
