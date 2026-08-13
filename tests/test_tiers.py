"""Tiers and value over replacement (S19.3, S19.4, S14).

Both gates are shut in the repository as it stands, so the arithmetic is
exercised against a fixture profile and a fixture board. The gate tests are the
other half: a module that refuses to run is only useful if it refuses for the
right reasons, and stops refusing when the reasons go away.
"""

import datetime as dt

import polars as pl
import pytest

from pipeline import config
from research.foundations import tiers

# 12-team, 2RB/3WR/1TE/1QB + 1 FLEX -- the shape of both real profiles.
PROFILE = {
    "id": "fixture_12",
    "label": "12-team half-PPR fixture",
    "teams": 12,
    "real": True,
    "scoring": {
        "pass_td": 4, "pass_yd": 0.04, "interception": -2,
        "rush_yd": 0.10, "rush_td": 6,
        "reception": 0.5, "receiving_yd": 0.10, "receiving_td": 6,
    },
    "starters": {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "FLEX": 1},
    "flex_eligible": ["RB", "WR", "TE"],
    "bench": 6,
}


def _board(counts=None, points=None) -> pl.DataFrame:
    """A projected board with a known, hand-checkable point ordering."""
    counts = counts or {"QB": 20, "RB": 40, "WR": 50, "TE": 20}
    rows = []
    for pos, n in counts.items():
        for i in range(n):
            pts = points(pos, i) if points else 300 - i * 5
            rows.append(
                {
                    "season": 2026,
                    "snapshot_date": dt.date(2026, 8, 14),
                    "provider_id": "fixture",
                    "player_id": f"00-{pos}{i:05d}",
                    "source_player_name": f"{pos}{i + 1}",
                    "team": "ATL",
                    "position": pos,
                    "receptions": 0.0,
                    "receiving_yards": pts * 10,  # scoring: 0.1/yd -> pts
                    "projected_points": None,
                }
            )
    frame = pl.DataFrame(rows).drop("projected_points")
    from pipeline.scoring import score_frame

    return score_frame(frame, PROFILE, alias="projected_points").sort(
        "projected_points", descending=True
    )


# -- the gates -------------------------------------------------------------


def test_both_blockers_are_reported_not_just_the_first(monkeypatch, tmp_path):
    """Fixing one and re-running should not surface a second surprise."""
    monkeypatch.setattr(tiers, "real_profiles", list)
    problems = tiers.blockers(processed_dir=tmp_path)  # no projection table
    assert len(problems) == 2
    assert any("real: true" in p for p in problems)
    assert any("projection" in p for p in problems)


def test_an_archived_capture_clears_the_projection_blocker_without_a_key(
    monkeypatch, tmp_path
):
    """The blocker asks what is on disk, not what this machine could fetch.

    Basing it on `projection_source_available()` made it permanent on every
    machine without FANTASYPROS_API_KEY -- including every rebuild from the
    committed archive, which is the reason the archive is committed.
    """
    import polars as pl

    monkeypatch.setattr(tiers, "real_profiles", lambda: [PROFILE])
    monkeypatch.delenv("FANTASYPROS_API_KEY", raising=False)
    assert tiers.blockers(processed_dir=tmp_path)  # nothing archived yet

    pl.DataFrame({"season": [2026], "projected_fantasy_points": [1.0]}).write_parquet(
        tmp_path / "projection_snapshot.parquet"
    )
    assert tiers.blockers(processed_dir=tmp_path) == []


def test_an_empty_projection_table_is_still_a_blocker(monkeypatch, tmp_path):
    """A table that exists and holds nothing prices nothing."""
    import polars as pl

    monkeypatch.setattr(tiers, "real_profiles", lambda: [PROFILE])
    pl.DataFrame({"season": [], "projected_fantasy_points": []}).write_parquet(
        tmp_path / "projection_snapshot.parquet"
    )
    assert any("projection" in p for p in tiers.blockers(processed_dir=tmp_path))


def test_a_configured_provider_clears_the_projection_blocker(monkeypatch, tmp_path):
    """Regression: the blocker used to be unclearable.

    `blockers()` read projection_providers off `config.sources()`, which returns
    the `sources:` sub-map; the key is a top-level sibling of it. The lookup
    returned None whatever was configured, so the projection blocker fired
    forever -- including after someone had done exactly what it asked.
    """
    (tmp_path / "sources.yaml").write_text(
        "sources:\n  nflverse:\n    url: x\n"
        "projection_providers:\n  fftoday_2026:\n    provider_id: fftoday\n"
        "    file: data/raw/projections/x.csv\n"
    )
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(tiers, "real_profiles", lambda: [PROFILE])
    config.projection_providers.cache_clear()
    config.sources.cache_clear()
    try:
        # The old lookup went through sources(), where the key does not live.
        assert config.sources().get("projection_providers") is None
        assert config.projection_providers()
        assert config.projection_source_available()
        # The provider is configured but nothing has been captured through it
        # yet, so the blocker stands -- and now names that as the reason.
        remaining = tiers.blockers(processed_dir=tmp_path)
        assert any("no projection capture has landed" in p for p in remaining)
        assert any("build-tables" in p for p in remaining)
    finally:
        config.projection_providers.cache_clear()
        config.sources.cache_clear()


def test_run_refuses_while_a_gate_is_shut(monkeypatch):
    monkeypatch.setattr(tiers, "real_profiles", list)
    with pytest.raises(tiers.BlockedError, match="blocked, not killed"):
        tiers.run()


def test_an_empty_projection_table_is_a_block_not_an_empty_board(monkeypatch):
    monkeypatch.setattr(
        tiers.projection_table, "latest", lambda frame, season=None: frame.head(0)
    )
    with pytest.raises(tiers.BlockedError, match="empty"):
        tiers.board(PROFILE, frame=_board())


# -- replacement level (S19.4) ---------------------------------------------


def test_base_demand_is_teams_times_starters():
    ranked = {pos: [100.0] * 60 for pos in tiers.POSITIONS}
    no_flex = {**PROFILE, "starters": {"QB": 1, "RB": 2, "WR": 3, "TE": 1}}
    demand = tiers.positional_demand(no_flex, ranked)
    assert demand == {"QB": 12, "RB": 24, "WR": 36, "TE": 12}


def test_the_flex_slots_go_where_the_points_are():
    """A flex slot belongs to whichever position supplies the better player.

    Charging it all to RB, or splitting it evenly, moves the baseline for two
    positions at once -- and every value on the board is measured from it.
    """
    ranked = {
        "QB": [400.0] * 30,
        "RB": [50.0] * 30,          # every RB below the cutoff is weak
        "WR": [200.0] * 60,         # every WR below the cutoff is strong
        "TE": [40.0] * 30,
    }
    demand = tiers.positional_demand(PROFILE, ranked)
    assert demand["WR"] == 36 + 12  # all twelve flex slots land on receivers
    assert demand["RB"] == 24
    assert demand["TE"] == 12


def test_replacement_is_the_first_player_nobody_has_to_start():
    """12 teams starting 2 RBs makes RB25 the replacement, not RB24."""
    ranked = {"RB": [float(300 - i) for i in range(40)]}
    replacement = tiers.replacement_points(ranked, {"RB": 24})
    assert replacement["RB"]["rank"] == 25
    assert replacement["RB"]["points"] == 300 - 24


def test_a_board_shorter_than_demand_says_so_rather_than_pretending():
    ranked = {"RB": [float(300 - i) for i in range(10)]}
    replacement = tiers.replacement_points(ranked, {"RB": 24})
    assert replacement["RB"]["truncated"] is True


# -- tier breaks (S19.3) ---------------------------------------------------


def test_a_break_falls_where_the_gap_is():
    values = [100.0, 98.0, 96.0, 94.0, 60.0, 58.0, 56.0, 54.0]
    tiers_out = tiers.assign_tiers(values)
    assert tiers_out == [1, 1, 1, 1, 2, 2, 2, 2]


def test_an_evenly_spaced_board_is_one_tier():
    """No break is a legitimate answer, and 'one tier per player' is not."""
    assert set(tiers.assign_tiers([float(100 - i) for i in range(20)])) == {1}


def test_a_short_list_is_not_tiered():
    """With five players every gap is the median or twice it, and the tiers
    would describe the sample size rather than the board."""
    assert tiers.assign_tiers([100.0, 50.0, 49.0, 10.0]) == [1, 1, 1, 1]


# -- end to end ------------------------------------------------------------


def test_the_board_prices_players_under_the_profiles_own_scoring():
    """Points come from pipeline.scoring, so a reception is worth what the
    league says it is worth and there is no second scoring path to drift."""
    frame = _board()
    board = tiers.board(PROFILE, frame=frame)
    assert board["projected_points"].max() == pytest.approx(300.0)
    assert set(board["position"].unique().to_list()) <= set(tiers.POSITIONS)


def test_compute_produces_a_tiered_board_with_replacement_and_coverage():
    results = tiers.compute(tiers.board(PROFILE, frame=_board()), PROFILE)
    assert results["profile_id"] == "fixture_12"
    rb = results["positions"]["RB"]
    assert rb["replacement"]["rank"] >= 25          # 24 starters plus flex
    assert rb["players"][0]["value_over_replacement"] > 0
    # The best RB is measured from replacement, not from zero.
    assert rb["players"][0]["value_over_replacement"] < rb["players"][0]["projected_points"]
    assert results["coverage"]["rows"] > 0


def test_one_provider_reports_that_it_has_no_error_bar():
    """S38.1's number is a spread between providers. With one there is none, and
    saying so beats a board that looks as certain as a consensus."""
    dispersion = tiers.provider_dispersion(tiers.board(PROFILE, frame=_board()))
    assert dispersion["measurable"] is False


def test_the_artifact_claims_no_more_than_descriptive():
    results = tiers.compute(tiers.board(PROFILE, frame=_board()), PROFILE)
    artifact = tiers.export(results, PROFILE)
    assert artifact.claim_type == "DESCRIPTIVE"
    assert artifact.method_id.endswith("fixture_12")
    assert "evidence_grade" not in artifact.to_dict()
