"""FFC adapter and the S31.1 source question.

Fantasy Football Calculator is unreachable from the sandbox this repo is
developed in, so these tests pin the adapter's behaviour against both possible
payload shapes. The real answer comes from the first archived payload the
GitHub Actions capture writes.
"""

import datetime as dt
import json

from pipeline.ingest.ffc_adp import distribution_fields_present, parse

MEAN_ONLY = {
    "players": [
        {"player_id": 1, "name": "Bijan Robinson", "position": "RB", "team": "ATL",
         "adp": 3.4, "bye": 12}
    ]
}

WITH_DISTRIBUTION = {
    "players": [
        {"player_id": 1, "name": "Bijan Robinson", "position": "RB", "team": "ATL",
         "adp": 3.4, "stdev": 1.2, "high": 1, "low": 9, "times_drafted": 412, "bye": 12}
    ]
}


def test_detects_a_mean_only_payload():
    assert distribution_fields_present(MEAN_ONLY) == set()


def test_detects_a_payload_carrying_distribution_fields():
    found = distribution_fields_present(WITH_DISTRIBUTION)
    assert {"stdev", "high", "low", "times_drafted"} <= found


def test_distribution_columns_are_null_when_the_source_publishes_only_a_mean():
    """S13: never approximate a distribution in the table -- label it at analysis time."""
    row = parse(
        json.dumps(MEAN_ONLY).encode(),
        snapshot_date=dt.date(2026, 8, 13), fmt="half-ppr", teams=12, year=2026,
    )[0]
    assert row["adp"] == 3.4
    assert row["adp_stdev"] is None
    assert row["n_drafts"] is None
    assert all(row[f"pick_p{p}"] is None for p in (10, 25, 50, 75, 90))


def test_published_distribution_fields_are_carried_through():
    row = parse(
        json.dumps(WITH_DISTRIBUTION).encode(),
        snapshot_date=dt.date(2026, 8, 13), fmt="half-ppr", teams=12, year=2026,
    )[0]
    assert row["adp_stdev"] == 1.2
    assert row["n_drafts"] == 412
    assert row["as_of"] == dt.date(2026, 8, 13)
    assert row["value_type"] == "observed"
