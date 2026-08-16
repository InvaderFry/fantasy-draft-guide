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
from research.foundations import provider_agreement as agreement_mod
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


NAMES = ("Bijan Robinson", "Breece Hall", "Chase Brown", "Derrick Henry", "Josh Jacobs")


def _named_board(n: int = 3, position: str = "RB") -> pl.DataFrame:
    """A board with names S12's normalizer can actually tell apart.

    `_board` numbers its players RB1, RB2, RB3, and `normalize_name` strips
    digits -- so all three collapse to one key. That is invisible to the
    arithmetic tests and fatal to a join test.
    """
    from pipeline.scoring import score_frame

    rows = [
        {
            "season": 2026,
            "snapshot_date": dt.date(2026, 8, 14),
            "provider_id": "fixture",
            "player_id": f"00-{position}{i:05d}",
            "source_player_name": NAMES[i],
            "team": "ATL",
            "position": position,
            "receptions": 0.0,
            "receiving_yards": (300 - i * 5) * 10,
        }
        for i in range(n)
    ]
    return score_frame(pl.DataFrame(rows), PROFILE, alias="projected_points").sort(
        "projected_points", descending=True
    )


def _adp(rows) -> pl.DataFrame:
    """An archived capture, shaped as `adp_history` hands it to `latest_adp`."""
    return pl.DataFrame(
        [
            {
                "snapshot_date": dt.date(2026, 8, 14),
                "player_id": r.get("player_id"),
                "source_player_name": r["source_player_name"],
                "position": r["position"],
                "adp": r["adp"],
                "position_adp": r.get("position_adp"),
                "adp_delta": r.get("adp_delta"),
            }
            for r in rows
        ],
        schema_overrides={
            "player_id": pl.String,
            "position_adp": pl.Int64,
            "adp_delta": pl.Float64,
        },
    )


# -- the price on the board (S83) ------------------------------------------


def test_the_price_joins_on_the_shared_id():
    board = _named_board()
    priced, coverage = tiers.attach_adp(
        board,
        _adp([{"player_id": "00-RB00001", "source_player_name": "nothing like it",
               "position": "RB", "adp": 14.8, "position_adp": 6}]),
    )
    row = priced.filter(pl.col("player_id") == "00-RB00001")
    assert row["adp"].item() == 14.8
    assert row["position_adp"].item() == 6
    assert coverage["priced"] == 1
    assert coverage["unpriced"] == 2
    assert coverage["adp_snapshot_date"] == "2026-08-14"


def test_a_source_with_no_id_still_reaches_the_board_by_name():
    """FFC publishes no gsis_id; S12's weaker key is the whole fallback."""
    board = _named_board()
    priced, coverage = tiers.attach_adp(
        board,
        _adp([{"player_id": None, "source_player_name": "breece hall", "position": "RB",
               "adp": 27.2, "position_adp": 11}]),
    )
    assert priced.filter(pl.col("source_player_name") == "Breece Hall")["adp"].item() == 27.2
    assert coverage["priced"] == 1


def test_the_move_rides_across_on_both_join_paths():
    """S31.3's delta is carried by the same join as the price, and the failure it
    is written against is silent: a column resolved in one of the two branches
    arrives null for exactly the players the id join missed, which reads on the
    sheet as a market that did not move rather than as a column that was dropped.
    """
    board = _named_board()
    priced, _ = tiers.attach_adp(
        board,
        _adp([
            {"player_id": "00-RB00000", "source_player_name": "nothing like it",
             "position": "RB", "adp": 14.8, "adp_delta": -7.5},
            {"player_id": None, "source_player_name": "breece hall", "position": "RB",
             "adp": 27.2, "adp_delta": 9.1},
        ]),
    )
    by_id = priced.filter(pl.col("player_id") == "00-RB00000")
    by_name = priced.filter(pl.col("source_player_name") == "Breece Hall")
    assert by_id["adp_delta"].item() == -7.5
    assert by_name["adp_delta"].item() == 9.1


def test_a_capture_with_no_movement_attached_still_prices_the_board():
    """An archive one day old has no movement to attach, and the board is the
    deliverable -- it does not wait for a second capture."""
    board = _named_board()
    capture = _adp([{"player_id": "00-RB00001", "source_player_name": "x", "position": "RB",
                     "adp": 14.8}]).drop("adp_delta")
    priced, coverage = tiers.attach_adp(board, capture)
    assert coverage["priced"] == 1
    assert priced["adp_delta"].null_count() == priced.height


def test_two_players_sharing_a_name_price_neither():
    """S12's rule for the loose key: an ambiguous match resolves to nothing.
    Guessing puts one man's price on another man's row."""
    board = _named_board()
    priced, _ = tiers.attach_adp(
        board,
        _adp([
            {"player_id": None, "source_player_name": "Bijan Robinson", "position": "RB",
             "adp": 3.0},
            {"player_id": None, "source_player_name": "bijan robinson", "position": "RB",
             "adp": 90.0},
        ]),
    )
    assert priced.filter(pl.col("source_player_name") == "Bijan Robinson")["adp"].item() is None


def test_two_players_on_the_board_sharing_a_name_price_neither():
    """The ambiguity runs the other way too, and one ADP row would price both."""
    board = _named_board(2).with_columns(
        pl.lit("Mike Williams").alias("source_player_name"),
        pl.lit(None, dtype=pl.String).alias("player_id"),
    )
    priced, coverage = tiers.attach_adp(
        board,
        _adp([{"player_id": None, "source_player_name": "Mike Williams",
               "position": "RB", "adp": 55.0}]),
    )
    assert coverage["priced"] == 0


def test_a_player_the_market_never_priced_keeps_his_row():
    """Dropping him would change how many players sit above replacement, and the
    whole board is measured from there."""
    board = _named_board()
    priced, coverage = tiers.attach_adp(
        board,
        _adp([{"player_id": "00-RB00000", "source_player_name": "Bijan Robinson",
               "position": "RB", "adp": 1.5, "position_adp": 1}]),
    )
    assert priced.height == board.height
    assert coverage["priced_share"] == round(1 / 3, 4)


def test_an_unarchived_market_degrades_the_board_rather_than_withholding_it():
    """S84's archive and S11's projections are separate failure domains."""
    board = _named_board()
    priced, coverage = tiers.attach_adp(board, None)
    assert priced.height == board.height
    assert priced["adp"].null_count() == board.height
    assert coverage == {
        "board_rows": 3, "priced": 0, "unpriced": 3, "priced_share": 0.0,
        "market_rows_in_scope": 0, "matched_share_of_market": None,
        "adp_snapshot_date": None,
    }


def test_an_absent_archive_is_not_a_third_gate(monkeypatch, tmp_path):
    """`blockers()` has two gates and gains no third: a morning the capture job
    did not run must still produce a sheet."""
    monkeypatch.setattr(tiers, "real_profiles", lambda: [PROFILE])
    (tmp_path / "projection_snapshot.parquet").write_bytes(b"")
    price, movement = tiers.market_price(PROFILE, processed_dir=tmp_path)
    assert price is None
    # ...and the movement says why rather than reading as "nothing moved" (S31.3).
    assert movement["available"] is False
    assert "adp_history" in movement["reason"]


def test_compute_carries_the_price_beside_the_value_and_not_inside_it():
    board = pl.concat(
        [_named_board(3), _board(counts={"QB": 8, "WR": 40, "TE": 8})], how="diagonal"
    ).sort("projected_points", descending=True)
    market = _adp([{"player_id": "00-RB00000", "source_player_name": "Bijan Robinson",
                    "position": "RB", "adp": 1.5, "position_adp": 1}])
    unpriced = tiers.compute(board, PROFILE)
    priced = tiers.compute(board, PROFILE, adp=market)

    top = priced["positions"]["RB"]["players"][0]
    assert top["adp"] == 1.5
    assert top["position_adp"] == 1
    assert priced["positions"]["RB"]["players"][1]["adp"] is None
    assert priced["adp_coverage"]["priced"] == 1

    # The S19.3 metric is computed as if the price column were absent.
    assert [p["value_over_replacement"] for p in priced["positions"]["RB"]["players"]] == [
        p["value_over_replacement"] for p in unpriced["positions"]["RB"]["players"]
    ]


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
    results = tiers.compute(tiers.board(PROFILE, frame=_board()), PROFILE)
    dispersion = results["provider_dispersion"]
    assert dispersion["measurable"] is False
    assert dispersion["reason"] == agreement_mod.NO_SECOND_PROVIDER
    # ...and no player carries a label that would print as a mark.
    rb = results["positions"]["RB"]["players"]
    assert all(p["provider_agreement"] is None for p in rb)


def test_the_artifact_claims_no_more_than_descriptive():
    results = tiers.compute(tiers.board(PROFILE, frame=_board()), PROFILE)
    artifact = tiers.export(results, PROFILE)
    assert artifact.claim_type == "DESCRIPTIVE"
    assert artifact.method_id.endswith("fixture_12")
    assert "evidence_grade" not in artifact.to_dict()


# -- which provider the board is drawn from (S38.1) --------------------------


def _relabel(frame: pl.DataFrame, provider: str) -> pl.DataFrame:
    return frame.with_columns(pl.lit(provider).alias("provider_id"))


def test_a_second_provider_cannot_take_over_the_board_by_sorting_first(monkeypatch):
    """The defect S38.1's second capture would otherwise have introduced.

    `chosen_provider` used to return `sorted(providers)[0]`. Archiving any
    provider whose id sorts before the incumbent would have redrawn every tier,
    every replacement level and every VOR on 26 sheets from a board nobody chose
    -- and no row count falls, so `refresh-check`'s thinning gate is blind to it.
    """
    monkeypatch.setattr(tiers, "board_provider", lambda: "fantasypros")
    incumbent = _relabel(_board(), "fantasypros")
    usurper = _relabel(_board(), "aaa_sorts_first")

    assert tiers.chosen_provider(pl.concat([incumbent, usurper])) == "fantasypros"


def test_a_board_computed_beside_a_second_provider_is_the_same_board(monkeypatch):
    """S38.1 and S80: the second provider is CARRIED, never blended in.

    The value metric is `projected_points - replacement_points` and it must be
    computed as if the other provider's rows were not in the frame. This is the
    guarantee that would fail silently -- a board subtly shifted by a provider
    nobody chose to rank on looks exactly like a board, and every number on it is
    plausible.
    """
    monkeypatch.setattr(tiers, "board_provider", lambda: "fantasypros")
    board = _relabel(_board(), "fantasypros")
    # A second opinion that disagrees about everything, so blending could not
    # possibly leave the arithmetic untouched.
    other = _relabel(_board(points=lambda pos, i: 100 + i * 3), "other")

    alone = tiers.compute(board, PROFILE)
    beside = tiers.compute(board, PROFILE, other=other)

    assert alone["replacement_points"] == beside["replacement_points"]
    assert alone["positional_demand"] == beside["positional_demand"]
    for pos in tiers.POSITIONS:
        mine = alone["positions"][pos]["players"]
        theirs = beside["positions"][pos]["players"]
        assert [p["value_over_replacement"] for p in mine] == [
            p["value_over_replacement"] for p in theirs
        ]
        assert [p["tier"] for p in mine] == [p["tier"] for p in theirs]
        assert [p["player"] for p in mine] == [p["player"] for p in theirs]
    # ...and the only thing that changed is the label carried beside the value.
    assert all(p["provider_agreement"] is None for p in alone["positions"]["RB"]["players"])
    assert any(
        p["provider_agreement"] is not None for p in beside["positions"]["RB"]["players"]
    )


def test_the_second_providers_label_reaches_the_player_it_describes(monkeypatch):
    """A mark against the wrong row is worse than no mark."""
    monkeypatch.setattr(tiers, "board_provider", lambda: "fantasypros")
    board = _relabel(_named_board(n=3), "fantasypros")
    # Breece Hall is the disagreement: 295 against 200 is a cv well past S38.1's
    # 0.15. The other two are within a point.
    other = _relabel(
        _named_board(n=3).with_columns(
            pl.when(pl.col("source_player_name") == "Breece Hall")
            .then(pl.lit(200.0))
            .otherwise(pl.col("projected_points"))
            .alias("projected_points")
        ),
        "other",
    )

    results = tiers.compute(board, PROFILE, other=other)
    labels = {
        p["player"]: p["provider_agreement"] for p in results["positions"]["RB"]["players"]
    }
    assert labels["Breece Hall"] == agreement_mod.LOW
    assert labels["Bijan Robinson"] == agreement_mod.HIGH
    assert results["provider_dispersion"]["measurable"] is True
    assert results["provider_dispersion"]["range_points"]["low"] == 200.0


def test_several_unconfigured_providers_refuse_rather_than_pick_one(monkeypatch):
    """Picking one of several unconfigured boards is the original defect renamed."""
    monkeypatch.setattr(tiers, "board_provider", lambda: "fantasypros")
    frame = pl.concat([_relabel(_board(), "cbs"), _relabel(_board(), "espn")])

    with pytest.raises(tiers.BlockedError) as exc:
        tiers.chosen_provider(frame)
    assert "board_provider" in str(exc.value)


def test_a_lone_unconfigured_provider_draws_the_board_and_says_so(monkeypatch):
    """S83: stale-but-complete beats fresh-but-blocked.

    Refusing here would render TIERS as BLOCKED across 26 sheets because a
    provider was renamed. The board is drawn from the only archive there is and
    the substitution is recorded, so it is readable rather than assumed.
    """
    monkeypatch.setattr(tiers, "board_provider", lambda: "fantasypros")
    results = tiers.compute(_relabel(_board(), "fixture"), PROFILE)

    assert results["provider"] == "fixture"
    assert results["provider_selection"]["substituted"] is True
    assert results["provider_selection"]["configured"] == "fantasypros"
