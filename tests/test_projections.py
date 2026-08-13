"""Projection ingest and the projection_snapshot table (S11, S13, S38.1).

Neither projection path is configured in the repository as it stands -- no
FANTASYPROS_API_KEY and no manual export -- so these run against fixtures. That
is the point: the table, the adapter and the fallback order all have to be
correct before the first real payload arrives, because the first payload arrives
on a runner during draft week.
"""

import datetime as dt
import json

import polars as pl
import pytest

from pipeline import config
from pipeline.features import projections
from pipeline.features.assertions import AS_OF_COLUMNS
from pipeline.features.schema import OUTCOME_COLUMNS, PROJECTION_SNAPSHOT_COLUMNS
from pipeline.ingest import fantasypros, projections_csv
from pipeline.normalize.names import match_key, name_position_key

FP_CONFIG = {
    "api_base": "https://api.example.invalid/public/v2/json",
    "projections_path": "nfl/{season}/projections",
    "positions": ["RB", "WR"],
    "key_header": "x-api-key",
    "container_key": "players",
    "id_col": "fpid",
    "name_col": "name",
    "team_col": "team_id",
    "position_col": "position_id",
    "stat_map": {
        "rush_yds": "rushing_yards",
        "rush_tds": "rushing_tds",
        "rec": "receptions",
        "rec_yds": "receiving_yards",
        "rec_tds": "receiving_tds",
        "fpts": "projected_fantasy_points",
    },
}

PAYLOAD = {
    "players": [
        {"fpid": "1", "name": "Bijan Robinson", "team_id": "ATL", "position_id": "RB",
         "rush_yds": 1200, "rush_tds": 11, "rec": 60, "rec_yds": 480, "rec_tds": 2,
         "fpts": 290.5},
        {"fpid": "2", "name": "Ja'Marr Chase", "team_id": "CIN", "position_id": "WR",
         "rush_yds": 10, "rush_tds": 0, "rec": 105, "rec_yds": 1450, "rec_tds": 12,
         "fpts": 310.0},
    ]
}

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
    """One capture day holding an API payload, as the runner would write it."""
    directory = tmp_path / "2026-08-14"
    directory.mkdir()
    (directory / "fantasypros_projections_rb_2026.json").write_text(json.dumps(PAYLOAD))
    return tmp_path


# -- the adapter -----------------------------------------------------------


def test_no_key_is_a_named_error_rather_than_a_crash(monkeypatch):
    """S11's fallback order needs a signal it can branch on."""
    monkeypatch.delenv("FANTASYPROS_API_KEY", raising=False)
    with pytest.raises(fantasypros.MissingKeyError):
        fantasypros.FantasyProsAdapter(season=2026, config=FP_CONFIG).fetch()


def test_the_request_url_carries_no_credential(monkeypatch):
    """The URL is written into a manifest that is committed to a public repo.

    A key in the query string would be a key in the git history, and unlike a
    leaked file it cannot be deleted out of a clone someone already took.
    """
    monkeypatch.setenv("FANTASYPROS_API_KEY", "sk-secret-value")
    adapter = fantasypros.FantasyProsAdapter(season=2026, config=FP_CONFIG)
    url = adapter.url_for("RB")
    assert "sk-secret-value" not in url
    assert url.endswith("/nfl/2026/projections")


def test_the_key_travels_in_a_header(monkeypatch):
    monkeypatch.setenv("FANTASYPROS_API_KEY", "sk-secret-value")
    seen = {}

    def fake_get(url, *, params=None, headers=None, **kw):
        seen["headers"] = headers or {}
        return json.dumps(PAYLOAD).encode()

    monkeypatch.setattr(fantasypros, "http_get", fake_get)
    fetched = fantasypros.FantasyProsAdapter(season=2026, config=FP_CONFIG).fetch()
    assert seen["headers"]["x-api-key"] == "sk-secret-value"
    assert all("sk-secret-value" not in (f.url or "") for f in fetched)
    assert all("sk-secret-value" not in json.dumps(f.extra) for f in fetched)


def test_the_payload_is_persisted_unmodified(monkeypatch):
    """S31.1 was answered from the archive, not from a re-fetch. Same rule here.

    The FantasyPros response shape is unverified -- the API is unreachable from
    the development sandbox -- so the bytes have to survive the adapter intact
    for the first runner call to be able to correct the mapping.
    """
    monkeypatch.setenv("FANTASYPROS_API_KEY", "k")
    raw = json.dumps(PAYLOAD).encode()
    monkeypatch.setattr(fantasypros, "http_get", lambda *a, **k: raw)
    fetched = fantasypros.FantasyProsAdapter(season=2026, config=FP_CONFIG).fetch()
    assert fetched[0].data == raw
    # And it records what it saw, so a wrong container_key is diagnosable.
    assert "fpid" in fetched[0].extra["observed_row_keys"]


def test_an_unmatched_stat_map_names_the_keys_it_saw():
    """A frame of nulls looks exactly like a provider with no projections."""
    bad = dict(FP_CONFIG, stat_map={"nonexistent_column": "rushing_yards"})
    with pytest.raises(fantasypros.ResponseShapeError) as exc:
        fantasypros.parse(
            json.dumps(PAYLOAD).encode(), snapshot_date=dt.date(2026, 8, 14),
            season=2026, config=bad,
        )
    assert "fpts" in str(exc.value)


def test_a_wrong_container_key_is_reported_with_the_keys_present(monkeypatch):
    monkeypatch.setenv("FANTASYPROS_API_KEY", "k")
    monkeypatch.setattr(fantasypros, "http_get", lambda *a, **k: json.dumps(PAYLOAD).encode())
    bad = dict(FP_CONFIG, container_key="results")
    with pytest.raises(fantasypros.ResponseShapeError) as exc:
        fantasypros.FantasyProsAdapter(season=2026, config=bad).fetch()
    assert "players" in str(exc.value)


# -- the table -------------------------------------------------------------


def test_an_empty_archive_still_satisfies_the_as_of_contract(tmp_path):
    frame = projections.build(tmp_path)
    assert frame.height == 0
    for column in AS_OF_COLUMNS:
        assert column in frame.columns


def test_the_empty_schema_matches_the_built_frame(archive, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "fantasypros_config", lambda: FP_CONFIG)
    monkeypatch.setattr(fantasypros, "fantasypros_config", lambda: FP_CONFIG)
    built = projections.build(archive, crosswalk=CROSSWALK)
    empty = projections.build(tmp_path / "no-captures-here")
    assert sorted(built.columns) == sorted(empty.columns)
    assert sorted(built.columns) == sorted(PROJECTION_SNAPSHOT_COLUMNS)


def test_a_projection_is_derived_not_observed(archive, monkeypatch):
    """S37. A projection is a model output that happens to be knowable today."""
    monkeypatch.setattr(fantasypros, "fantasypros_config", lambda: FP_CONFIG)
    frame = projections.build(archive, crosswalk=CROSSWALK)
    assert set(frame["value_type"].to_list()) == {"derived"}


def test_projection_columns_do_not_collide_with_outcome_columns():
    """`fantasy_points` on a projection row and on a result row is one name for
    two different things -- the target_share bug this repo already shipped."""
    assert not set(PROJECTION_SNAPSHOT_COLUMNS) & OUTCOME_COLUMNS
    assert "projected_fantasy_points" in PROJECTION_SNAPSHOT_COLUMNS


def test_ids_are_matched_and_coverage_is_reported(archive, monkeypatch):
    monkeypatch.setattr(fantasypros, "fantasypros_config", lambda: FP_CONFIG)
    frame = projections.build(archive, crosswalk=CROSSWALK)
    coverage = projections.coverage(frame)
    assert coverage["rows"] == 2
    assert coverage["matched"] == 2
    assert coverage["providers"] == ["fantasypros"]


def test_latest_keeps_one_row_per_provider_per_player(tmp_path, monkeypatch):
    """Stacking every snapshot date makes the table an archive; a board wants today."""
    monkeypatch.setattr(fantasypros, "fantasypros_config", lambda: FP_CONFIG)
    for day, points in (("2026-08-10", 250.0), ("2026-08-14", 290.5)):
        directory = tmp_path / day
        directory.mkdir()
        payload = json.loads(json.dumps(PAYLOAD))
        payload["players"][0]["fpts"] = points
        (directory / "fantasypros_projections_rb_2026.json").write_text(json.dumps(payload))

    frame = projections.build(tmp_path, crosswalk=CROSSWALK)
    assert frame.height == 4
    newest = projections.latest(frame)
    assert newest.height == 2
    bijan = newest.filter(pl.col("source_player_name") == "Bijan Robinson")
    assert bijan["projected_fantasy_points"].item() == 290.5


def test_a_csv_export_without_its_mapping_raises_rather_than_guessing(tmp_path, monkeypatch):
    """The mapping is what makes the columns mean anything (S11)."""
    monkeypatch.setattr(projections, "projection_providers", dict)
    directory = tmp_path / "2026-08-14"
    directory.mkdir()
    (directory / "projections_someprovider.csv").write_text("player_name,pts\nA,1\n")
    with pytest.raises(KeyError, match="no longer declared"):
        projections.build(tmp_path, crosswalk=CROSSWALK)


def test_the_manual_csv_path_produces_the_same_row_shape():
    """S11's two transports normalize into one schema, or the table is two tables."""
    spec = {
        "provider_id": "fftoday",
        "name_col": "player_name",
        "team_col": "team",
        "position_col": "position",
        "stat_map": {"rush_yds": "rushing_yards", "pts": "projected_fantasy_points"},
    }
    rows = projections_csv.parse(
        b"player_name,team,position,rush_yds,pts\nBijan Robinson,ATL,RB,1200,290.5\n",
        spec,
        snapshot_date=dt.date(2026, 8, 14),
        season=2026,
    )
    assert rows[0]["value_type"] == "derived"
    assert rows[0]["transport"] == "manual_csv"
    assert rows[0]["projected_fantasy_points"] == 290.5
    assert set(rows[0]) <= set(PROJECTION_SNAPSHOT_COLUMNS)


def test_every_scorable_projection_column_is_one_the_scorer_reads():
    """The bug this guards against produced a board of zeroes, silently.

    S11's canonical schema calls them `pass_yards` and `rush_yards`;
    pipeline/scoring.py reads `passing_yards` and `rushing_yards`. Under S11's
    names `points_expr` finds no matching column, contributes no term, and every
    player's projected points come out 0 -- so the tier board is empty and
    nothing raises. The two vocabularies have to be the same one.
    """
    from pipeline.scoring import STAT_TO_RULE

    scorable = {
        "passing_yards", "passing_tds", "interceptions",
        "rushing_yards", "rushing_tds",
        "receptions", "receiving_yards", "receiving_tds",
    }
    assert scorable <= set(PROJECTION_SNAPSHOT_COLUMNS)
    assert scorable <= set(STAT_TO_RULE)


def test_a_projection_scores_to_something_other_than_zero():
    """The end-to-end version of the check above, through the real scorer."""
    from pipeline.scoring import score_frame

    profile = {"id": "t", "scoring": {"rush_yd": 0.1, "rush_td": 6, "reception": 0.5}}
    rows = fantasypros.parse(
        json.dumps(PAYLOAD).encode(),
        snapshot_date=dt.date(2026, 8, 14),
        season=2026,
        config=FP_CONFIG,
    )
    scored = score_frame(pl.DataFrame(rows), profile, alias="projected_points")
    assert scored["projected_points"].max() > 0


# -- the shape, once it was finally observed (S11) --------------------------

# The real envelope, from the first successful runner call on 2026-08-13:
# data/snapshots/2026-08-13/fantasypros_projections_rb_2026.json. Identity is
# flat on the row and every projected quantity is nested under `stats`, which the
# blind guess in config/sources.yaml did not anticipate.
LIVE_CONFIG = {
    **FP_CONFIG,
    "stat_container_key": "stats",
    "stat_map": {
        "rush_att": "carries",
        "rush_yds": "rushing_yards",
        "rush_tds": "rushing_tds",
        "rec_rec": "receptions",
        "rec_yds": "receiving_yards",
        "rec_tds": "receiving_tds",
        "fumbles": "fumbles_lost",
        "points": "projected_fantasy_points",
    },
}

LIVE_PAYLOAD = {
    "season": "2026",
    "week": "0",
    "count": "1",
    "positions": "RB",
    "scoring": "STD",
    "players": [
        {
            "fpid": 22968,
            "name": "Jahmyr Gibbs",
            "position_id": "RB",
            "team_id": "DET",
            "stats": {
                "points": 301.85,
                "points_ppr": 372.76,
                "points_half": 337.31,
                "rush_att": 274.73,
                "rush_yds": 1382.69,
                "rush_tds": 13.83,
                "rec_rec": 70.92,
                "rec_yds": 580.79,
                "rec_tds": 4.13,
                "fumbles": 1.13,
            },
        }
    ],
}


def test_nested_stats_are_read_through_the_configured_container():
    """The shape is configuration, so correcting it is a YAML edit (S11)."""
    rows = fantasypros.parse(
        json.dumps(LIVE_PAYLOAD).encode(),
        snapshot_date=dt.date(2026, 8, 13),
        season=2026,
        config=LIVE_CONFIG,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["source_player_name"] == "Jahmyr Gibbs"   # identity stays flat
    assert row["team"] == "DET"
    assert row["rushing_yards"] == 1382.69               # stats come from `stats`
    assert row["receptions"] == 70.92
    assert row["fumbles_lost"] == 1.13


def test_a_flat_payload_still_parses_with_no_container_configured():
    """Not every provider nests. An unset key means the row IS the stats."""
    rows = fantasypros.parse(
        json.dumps(PAYLOAD).encode(),
        snapshot_date=dt.date(2026, 8, 13),
        season=2026,
        config=FP_CONFIG,
    )
    assert rows and rows[0]["rushing_yards"] is not None


def test_nested_stats_under_the_wrong_container_key_raise_rather_than_null_out():
    """A frame of nulls looks like a provider with no projections."""
    with pytest.raises(fantasypros.ResponseShapeError, match="no configured stat_map"):
        fantasypros.parse(
            json.dumps(LIVE_PAYLOAD).encode(),
            snapshot_date=dt.date(2026, 8, 13),
            season=2026,
            config={**LIVE_CONFIG, "stat_container_key": "projections"},
        )


def test_the_repository_scorer_reproduces_the_providers_own_half_ppr_total():
    """The check that the stat map is right, not merely non-empty.

    S11 maps FantasyPros' columns onto player_week's spelling so that
    pipeline/scoring.py prices a projection with the same expression it prices a
    real season with. If that mapping is wrong the points still compute -- they
    are just quietly wrong. FantasyPros publishes its own half-PPR total, so the
    mapping has an independent answer to be checked against.
    """
    import polars as pl

    from pipeline.scoring import score_frame

    rows = fantasypros.parse(
        json.dumps(LIVE_PAYLOAD).encode(),
        snapshot_date=dt.date(2026, 8, 13),
        season=2026,
        config=LIVE_CONFIG,
    )
    profile = {
        "id": "half_ppr",
        "scoring": {
            "rush_yd": 0.10, "rush_td": 6, "reception": 0.5,
            "receiving_yd": 0.10, "receiving_td": 6, "fumble_lost": -2,
        },
    }
    scored = score_frame(pl.DataFrame(rows), profile, alias="projected_points")
    published_half_ppr = LIVE_PAYLOAD["players"][0]["stats"]["points_half"]
    assert scored["projected_points"][0] == pytest.approx(published_half_ppr, abs=0.01)
