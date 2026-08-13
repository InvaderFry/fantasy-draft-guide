"""Survival probability at held picks (S31.2, S19.4).

The quantity S19.4 calls the most decision-relevant in the guide, computed by
the approximation S31.1 proved is the only one available: FFC publishes a mean
and a spread and no percentiles, so there is no empirical curve to draw.
"""

import datetime as dt

import polars as pl
import pytest

from research.foundations import survival

PROFILE = {
    "id": "fixture_12",
    "label": "12-team half-PPR fixture",
    "teams": 12,
    "draft_slot": 7,
    "real": True,
    "scoring": {"reception": 0.5},
    "starters": {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "FLEX": 1},
}


def _adp(rows=None) -> pl.DataFrame:
    rows = rows or [
        ("Ja'Marr Chase", "WR", 1.9, 1.1, 1.0),
        ("Bijan Robinson", "RB", 3.4, 1.8, 1.0),
        ("Puka Nacua", "WR", 8.2, 3.0, 3.0),
        ("Player Four", "RB", 15.0, 5.0, 6.0),
        ("Player Five", "TE", 24.0, 7.0, 11.0),
        ("No Spread", "WR", 30.0, None, None),
    ]
    return pl.DataFrame(
        [
            {
                "season": 2026,
                "snapshot_date": dt.date(2026, 8, 14),
                "format": "half-ppr",
                "teams": 12,
                "source_player_name": name,
                "position": pos,
                "team": "ATL",
                "adp": adp,
                "adp_stdev": sd,
                "pick_high": high,
            }
            for name, pos, adp, sd, high in rows
        ],
        schema_overrides={"adp_stdev": pl.Float64, "pick_high": pl.Float64},
    )


# -- the snake -------------------------------------------------------------


def test_held_picks_snake_back_and_forth():
    assert survival.held_picks(12, 7, 3) == [7, 18, 31]
    assert survival.held_picks(12, 1, 4) == [1, 24, 25, 48]
    assert survival.held_picks(12, 12, 3) == [12, 13, 36]


def test_a_slot_outside_the_league_is_an_error_not_a_pick_number():
    with pytest.raises(ValueError, match="outside"):
        survival.held_picks(12, 13, 3)


# -- the approximation (S19.4) ---------------------------------------------


def test_survival_falls_as_the_next_pick_gets_later():
    curve = [survival.probability_available(p, 20.0, 6.0) for p in (10, 20, 30, 40)]
    assert curve == sorted(curve, reverse=True)
    assert curve[1] == pytest.approx(0.5)  # at the mean, a coin flip


def test_no_published_spread_returns_no_number():
    """A 0/1 step at the mean would read on the sheet as certainty."""
    assert survival.probability_available(10, 20.0, None) is None
    assert survival.probability_available(10, 20.0, 0.0) is None


def test_the_approximation_flags_itself_where_it_puts_mass_on_impossible_picks():
    """Pick distributions are truncated at 1 and skewed right; a normal is not."""
    # A mid-round player whose spread stays inside what has actually happened.
    assert survival.calibration_note(50.0, 5.0, 35.0) is None
    # An early pick: 2 sigma below a 3.4 ADP is pick -0.2, which cannot occur.
    note = survival.calibration_note(3.4, 1.8, 1.0)
    assert note is not None and "earliest actually recorded" in note


# -- the report ------------------------------------------------------------


def test_compute_reports_the_method_it_used():
    """S19.4 requires the label by name so pages built on it can be found again."""
    results = survival.compute(_adp(), PROFILE, rounds=3)
    assert results["opportunity_cost_method"] == "normal_approximation"
    assert results["by_slot"][0]["held_picks"] == [7, 18, 31]


def test_an_undrawn_slot_reports_every_slot_rather_than_guessing_one():
    results = survival.compute(_adp(), {**PROFILE, "draft_slot": "unknown"}, rounds=2)
    assert results["draft_slot"] == "unknown"
    assert len(results["by_slot"]) == 12
    assert results["by_slot"][0]["held_picks"] == [1, 24]


def test_a_todo_slot_is_rejected_rather_than_treated_as_undrawn():
    """The placeholder and the answer are no longer the same thing.

    `unknown` is a stated answer -- the order is drawn an hour before the draft
    -- and it produces twelve slots on purpose. A leftover TODO producing the
    same twelve slots would be an unanswered question rendering as a confident
    output, so it raises instead.
    """
    from pipeline.config import ConfigError

    with pytest.raises(ConfigError, match="neither a seat number"):
        survival.compute(_adp(), {**PROFILE, "draft_slot": "TODO"}, rounds=1)


def test_an_unknown_draft_date_does_not_stop_survival():
    """The date is not known and nothing here reads it (S31.2)."""
    results = survival.compute(
        _adp(), {**PROFILE, "draft_slot": "unknown", "draft_date": "unknown"}, rounds=1
    )
    assert len(results["by_slot"]) == 12


def test_players_priced_just_ahead_of_the_pick_are_still_listed():
    """The player going six picks before you is exactly the one worth checking."""
    results = survival.compute(_adp(), {**PROFILE, "draft_slot": 12}, rounds=1)
    names = [c["player"] for c in results["by_slot"][0]["picks"][0]["candidates"]]
    assert "Puka Nacua" in names  # ADP 8.2, ahead of pick 12


def test_a_player_with_no_spread_is_carried_with_a_stated_reason():
    results = survival.compute(_adp(), {**PROFILE, "draft_slot": 12}, rounds=3)
    rows = [
        c
        for block in results["by_slot"][0]["picks"]
        for c in block["candidates"]
        if c["player"] == "No Spread"
    ]
    assert rows
    assert all(r["p_available"] is None for r in rows)
    assert all("no spread published" in r["approximation_note"] for r in rows)


def test_the_artifact_claims_no_more_than_descriptive():
    results = survival.compute(_adp(), PROFILE, rounds=3)
    artifact = survival.export(results, PROFILE)
    assert artifact.claim_type == "DESCRIPTIVE"
    assert artifact.population["opportunity_cost_method"] == "normal_approximation"
    assert any("NORMAL APPROXIMATION" in limit for limit in artifact.limitations)


def test_run_refuses_while_no_profile_is_real(monkeypatch):
    monkeypatch.setattr(survival, "real_profiles", list)
    with pytest.raises(survival.BlockedError, match="real: true"):
        survival.run()


def test_survival_is_measured_at_the_next_pick_not_the_current_one():
    """S19.4's question is what is lost by waiting, which is about the NEXT pick.

    Measured at the current pick instead, every block reported the top of the
    board at 0% -- true, useless, and printed on a sheet somebody drafts from.
    """
    results = survival.compute(_adp(), {**PROFILE, "draft_slot": 1}, rounds=2)
    first, last = results["by_slot"][0]["picks"]
    assert first["pick"] == 1 and first["survival_measured_at"] == 24
    assert last["pick"] == 24 and last["is_last_pick"] is True
    assert last["survival_measured_at"] == 24


def test_the_candidate_window_starts_near_the_pick():
    """A window reaching back past pick 1 filled every block with players who
    left in the first round."""
    results = survival.compute(_adp(), {**PROFILE, "draft_slot": 1}, rounds=2)
    late = results["by_slot"][0]["picks"][1]  # pick 24
    adps = [c["adp"] for c in late["candidates"]]
    assert adps and min(adps) >= 24 - survival.AVAILABILITY_BUFFER
