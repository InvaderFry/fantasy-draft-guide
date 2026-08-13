"""adp_history builds from the snapshot archive (S13, S6.1).

These exercise `build()` itself rather than `parse()` alone. The distinction
matters: `parse()` was tested field by field while the builder's as-of
assertion was never reached, because an empty archive returned early and the
first real capture would have been the first failing build.
"""

import datetime as dt
import json

import polars as pl
import pytest

from pipeline.features import adp_history
from pipeline.features.assertions import LeakageError
from pipeline.features.schema import ADP_HISTORY_COLUMNS, AS_OF_COLUMNS
from pipeline.normalize.names import match_key, name_position_key

PAYLOAD = {
    "players": [
        {"player_id": 1, "name": "Bijan Robinson", "position": "RB", "team": "ATL",
         "adp": 3.4, "bye": 12},
        {"player_id": 2, "name": "Ja'Marr Chase", "position": "WR", "team": "CIN",
         "adp": 1.9, "bye": 10},
    ]
}

# Keys built with the real normalizers so the fixture cannot drift from them.
CROSSWALK = pl.DataFrame(
    {
        "gsis_id": ["00-0037746", "00-0036900"],
        "match_key": [
            match_key("Bijan Robinson", "RB", "ATL"),
            match_key("Ja'Marr Chase", "WR", "CIN"),
        ],
        "name_position_key": [
            name_position_key("Bijan Robinson", "RB"),
            name_position_key("Ja'Marr Chase", "WR"),
        ],
        "override_match_key": [None, None],
    },
    schema_overrides={"override_match_key": pl.String},
)


@pytest.fixture
def archive(tmp_path):
    """Two capture days of the same format, as the workflow would write them."""
    for day in ("2026-08-13", "2026-08-14"):
        directory = tmp_path / day
        directory.mkdir()
        (directory / "ffc_adp_half-ppr_12team_2026.json").write_text(json.dumps(PAYLOAD))
    return tmp_path


def test_an_empty_archive_still_satisfies_the_as_of_contract(tmp_path):
    """The empty schema is the one that drifted; assert it rather than trust it."""
    frame = adp_history.build(tmp_path)
    assert frame.height == 0
    for column in AS_OF_COLUMNS:
        assert column in frame.columns


def test_the_empty_schema_matches_the_built_frame(archive, tmp_path):
    built = adp_history.build(archive, crosswalk=CROSSWALK)
    empty = adp_history.build(tmp_path / "no-captures-here")
    assert sorted(built.columns) == sorted(empty.columns)
    assert sorted(built.columns) == sorted(ADP_HISTORY_COLUMNS)


def test_a_real_capture_builds_and_carries_source_as_of(archive):
    frame = adp_history.build(archive, crosswalk=CROSSWALK)
    assert frame.height == 4  # two players x two capture days
    assert frame["source_as_of"].null_count() == 0
    assert set(frame["snapshot_date"].to_list()) == {
        dt.date(2026, 8, 13), dt.date(2026, 8, 14)
    }
    assert frame.filter(pl.col("match_method") == "unmatched").height == 0


def test_position_adp_ranks_within_a_snapshot(archive):
    frame = adp_history.build(archive, crosswalk=CROSSWALK)
    assert frame["position_adp"].unique().to_list() == [1]  # one player per position per day


def test_a_capture_missing_source_as_of_stops_the_build(archive, monkeypatch):
    """The defect this file exists for: strip the column and the build must fail."""
    original = adp_history.ffc_adp.parse

    def without_source_as_of(*args, **kwargs):
        return [{k: v for k, v in row.items() if k != "source_as_of"}
                for row in original(*args, **kwargs)]

    monkeypatch.setattr(adp_history.ffc_adp, "parse", without_source_as_of)
    with pytest.raises(LeakageError, match="source_as_of"):
        adp_history.build(archive, crosswalk=CROSSWALK)
