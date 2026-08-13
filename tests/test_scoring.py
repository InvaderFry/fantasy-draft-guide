"""Profile-driven scoring (S14)."""

import polars as pl

from pipeline.scoring import BUILT_IN_RECEPTION_VALUES, built_in_scoring_exprs, score_frame

STAT_ROW = {
    "passing_yards": [300.0],
    "passing_tds": [2.0],
    "interceptions": [1.0],
    "rushing_yards": [20.0],
    "rushing_tds": [0.0],
    "receptions": [0.0],
    "receiving_yards": [0.0],
    "receiving_tds": [0.0],
    "fumbles_lost": [0.0],
    "two_point_conversions": [0.0],
    "special_teams_tds": [0.0],
}


def test_scoring_uses_the_profile_not_a_hard_coded_system():
    frame = pl.DataFrame({**STAT_ROW, "receptions": [6.0], "receiving_yards": [80.0]})
    half = score_frame(frame, {"id": "half", "scoring": {"reception": 0.5, "receiving_yd": 0.1}})
    full = score_frame(frame, {"id": "ppr", "scoring": {"reception": 1.0, "receiving_yd": 0.1}})
    assert full["fantasy_points"][0] - half["fantasy_points"][0] == 3.0


def test_built_in_columns_differ_only_by_reception_value():
    frame = pl.DataFrame({**STAT_ROW, "receptions": [10.0]}).with_columns(
        built_in_scoring_exprs(list(STAT_ROW))
    )
    standard = frame["fantasy_points_standard"][0]
    for alias, value in BUILT_IN_RECEPTION_VALUES.items():
        assert frame[alias][0] == standard + 10.0 * value


def test_missing_stat_columns_are_treated_as_zero():
    frame = pl.DataFrame({"rushing_yards": [100.0]})
    scored = score_frame(frame, {"id": "x", "scoring": {"rush_yd": 0.1, "pass_td": 4}})
    assert scored["fantasy_points"][0] == 10.0
