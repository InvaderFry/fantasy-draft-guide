"""ID normalization (S12). Never join solely by player name."""

import polars as pl

from pipeline.normalize.names import match_key, normalize_name, normalize_team
from pipeline.normalize.player_ids import match_external


def test_name_normalization_strips_suffixes_punctuation_and_accents():
    assert normalize_name("Marvin Harrison Jr.") == "marvin harrison"
    assert normalize_name("Ja'Marr Chase") == "ja marr chase"
    assert normalize_name("Amon-Ra St. Brown") == "amon ra st brown"
    assert normalize_name(None) == ""


def test_team_aliases_resolve_to_nflverse_abbreviations():
    assert normalize_team("JAC") == "JAX"
    assert normalize_team("oak") == "LV"
    assert normalize_team("STL") == "LAR"


CROSSWALK = pl.DataFrame(
    {
        "gsis_id": ["00-0000001", "00-0000002", "00-0000003"],
        "match_key": [
            match_key("Mike Evans", "WR", "TB"),
            match_key("Mike Williams", "WR", "NYJ"),
            match_key("Mike Williams", "WR", "LAC"),
        ],
        "name_position_key": ["mike evans|WR", "mike williams|WR", "mike williams|WR"],
        "override_match_key": [None, None, match_key("Mike Williams", "WR", "LA")],
    }
)


def _frame(rows):
    return pl.DataFrame(
        rows,
        schema={"source_player_name": pl.String, "position": pl.String, "team": pl.String},
        orient="row",
    )


def test_exact_name_position_team_match_is_labelled():
    out = match_external(_frame([("Mike Evans", "WR", "TB")]), crosswalk=CROSSWALK)
    assert out["gsis_id"][0] == "00-0000001"
    assert out["match_method"][0] == "name_position_team"
    assert out["match_confidence"][0] == 0.9


def test_ambiguous_names_do_not_silently_resolve():
    """Two active Mike Williams: a name-only match must not pick one."""
    out = match_external(_frame([("Mike Williams", "WR", "SF")]), crosswalk=CROSSWALK)
    assert out["gsis_id"][0] is None
    assert out["match_method"][0] == "unmatched"


def test_manual_override_wins():
    out = match_external(_frame([("Mike Williams", "WR", "LA")]), crosswalk=CROSSWALK)
    assert out["gsis_id"][0] == "00-0000003"
    assert out["match_method"][0] == "manual_override"
    assert out["match_confidence"][0] == 1.0


def test_unmatched_rows_are_kept_not_dropped():
    out = match_external(_frame([("Nobody At All", "WR", "TB")]), crosswalk=CROSSWALK)
    assert out.height == 1
    assert out["match_method"][0] == "unmatched"
