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


PAYLOAD_WITH_WINDOW = {
    "meta": {"type": "PPR", "teams": 12, "total_drafts": 8470,
             "start_date": "2025-08-25", "end_date": "2025-09-01"},
    "players": PAYLOAD["players"],
}


def test_as_of_is_the_window_close_not_the_capture_date(tmp_path):
    """A 2025 ADP fetched in 2026 became knowable in 2025 (S6.1).

    This is what makes a retroactive backfill datable at all: without it every
    historical row would claim to have been knowable on the day we ran the job.
    """
    directory = tmp_path / "2026-08-13"
    directory.mkdir()
    (directory / "ffc_adp_ppr_12team_2025.json").write_text(json.dumps(PAYLOAD_WITH_WINDOW))

    frame = adp_history.build(tmp_path, crosswalk=CROSSWALK)
    assert frame["snapshot_date"].unique().to_list() == [dt.date(2026, 8, 13)]
    assert frame["as_of"].unique().to_list() == [dt.date(2025, 9, 1)]
    assert frame["source_as_of"].unique().to_list() == [dt.date(2025, 9, 1)]
    assert frame["window_start"].unique().to_list() == [dt.date(2025, 8, 25)]
    assert frame["total_drafts"].unique().to_list() == [8470]


def test_a_payload_without_a_window_falls_back_to_the_capture_date(archive):
    """The fixture payloads carry no meta; the capture date is the best available."""
    frame = adp_history.build(archive, crosswalk=CROSSWALK)
    assert frame["window_end"].null_count() == frame.height
    assert frame["as_of"].to_list() == frame["snapshot_date"].to_list()


def test_position_adp_does_not_interleave_two_seasons(tmp_path):
    """A backfill captures many seasons on one day; each ranks on its own."""
    directory = tmp_path / "2026-08-13"
    directory.mkdir()
    for year in (2024, 2025):
        (directory / f"ffc_adp_ppr_12team_{year}.json").write_text(json.dumps(PAYLOAD))

    frame = adp_history.build(tmp_path, crosswalk=CROSSWALK)
    assert frame.height == 4
    # one RB and one WR per season, so every row ranks 1 within its own season
    assert frame["position_adp"].unique().to_list() == [1]
