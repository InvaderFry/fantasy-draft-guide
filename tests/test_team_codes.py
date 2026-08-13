"""One franchise, one code (S13).

nflverse spells the same team three ways depending on the file. Left alone the
mismatch does not raise -- it drops rows from joins, which is why it survived
into a build that passed every check.
"""

import polars as pl

from pipeline.features.sources import CANONICAL_TEAM_CODES, normalize_team_codes


def test_era_contemporary_codes_map_to_the_current_one():
    """The three spellings of the Rams, Chargers and Raiders converge."""
    frame = pl.DataFrame({"team": ["STL", "SL", "LAR", "LA", "SD", "LAC", "OAK", "LV"]})
    out = normalize_team_codes(frame)["team"].to_list()
    assert out == ["LA", "LA", "LA", "LA", "LAC", "LAC", "LV", "LV"]


def test_roster_spellings_map_to_the_play_by_play_one():
    frame = pl.DataFrame({"team": ["ARZ", "BLT", "CLV", "HST"]})
    assert normalize_team_codes(frame)["team"].to_list() == ["ARI", "BAL", "CLE", "HOU"]


def test_every_team_column_is_normalized_not_just_the_first():
    frame = pl.DataFrame({"home_team": ["STL"], "away_team": ["SD"], "posteam": ["OAK"]})
    out = normalize_team_codes(frame)
    assert out["home_team"][0] == "LA"
    assert out["away_team"][0] == "LAC"
    assert out["posteam"][0] == "LV"


def test_a_frame_with_no_team_column_is_untouched():
    frame = pl.DataFrame({"season": [2024], "player_id": ["00-0000001"]})
    assert normalize_team_codes(frame).equals(frame)


def test_canonical_codes_are_not_themselves_keys():
    """A map whose values appear as keys would depend on application order."""
    assert not set(CANONICAL_TEAM_CODES.values()) & set(CANONICAL_TEAM_CODES)
