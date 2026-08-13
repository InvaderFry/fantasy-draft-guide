"""Canonical table checks (S13, S51).

These run only when the tables have been built, so a clean checkout still has a
green suite. Build them with:

    uv run research ingest --seasons 2023-2024
    uv run research normalize-ids
    uv run research build-tables --seasons 2023-2024
"""

import polars as pl
import pytest

from pipeline.config import PROCESSED_DIR
from pipeline.features import checks

TABLES = ("player_week", "player_season", "player_season_outcomes", "team_season")


def _load(name: str) -> pl.DataFrame:
    path = PROCESSED_DIR / f"{name}.parquet"
    if not path.exists():
        pytest.skip(f"{name} not built")
    return pl.read_parquet(path)


def test_data_checks_pass():
    results = checks.run_all()
    failures = [message for message, ok in results if not ok]
    assert not failures, "\n".join(failures)


def test_player_week_keys_are_unique():
    frame = _load("player_week")
    assert frame.group_by(["season", "week", "player_id"]).len().select(
        pl.col("len").max()
    ).item() == 1


def test_scoring_matches_nflverse_ppr_exactly():
    """A drift check on the scoring engine against an independent computation."""
    from pipeline.features import sources

    frame = _load("player_week")
    seasons = frame["season"].unique().to_list()
    theirs = pl.concat(
        [
            sources.load(f"player_stats_{s}.parquet")
            .filter(pl.col("season_type") == "REG")
            .select("season", "week", "player_id", pl.col("fantasy_points_ppr").alias("theirs"))
            for s in seasons
        ]
    )
    joined = frame.select("season", "week", "player_id", "fantasy_points_ppr").join(
        theirs, on=["season", "week", "player_id"], how="inner"
    )
    worst = joined.select((pl.col("fantasy_points_ppr") - pl.col("theirs")).abs().max()).item()
    assert worst < 0.01, f"scoring drifted from nflverse by {worst}"


def test_outcomes_live_in_their_own_table():
    """S6.1: no outcome column may appear in the player_season feature table."""
    from pipeline.features.assertions import assert_no_outcome_columns

    features = _load("player_season")
    assert_no_outcome_columns(features.drop("depth_chart_rank_preseason_as_of"), "player_season")
    outcomes = _load("player_season_outcomes")
    assert outcomes["is_outcome"].all()


def test_availability_and_production_are_reported_separately():
    """S15.1: season points = games x points-per-game, modelled apart."""
    outcomes = _load("player_season_outcomes")
    for col in ("games", "games_missed", "games_missed_injury", "fantasy_ppg_active"):
        assert col in outcomes.columns
    assert outcomes.filter(pl.col("games") > pl.col("rostered_weeks")).height == 0

    # Per-game production conditional on playing divides by games played, the
    # season rate divides by weeks rostered, so the former is the larger of the
    # two -- except for the handful of players whose season total is negative
    # (fumbles and interceptions with no offsetting production), where dividing
    # by the smaller denominator makes it more negative.
    scored = outcomes.filter((pl.col("games") > 0) & (pl.col("fantasy_points_ppr") > 0))
    assert (scored["fantasy_ppg_active"] >= scored["fantasy_ppg"] - 1e-9).all()


def test_team_season_has_every_team():
    frame = _load("team_season")
    counts = frame.group_by("season").len()
    assert counts.filter(pl.col("len") != 32).height == 0


def test_every_table_carries_as_of():
    for name in TABLES:
        frame = _load(name)
        assert frame["as_of"].null_count() == 0, f"{name} has null as_of (S6.1)"
