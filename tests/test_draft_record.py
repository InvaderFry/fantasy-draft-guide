"""The recommendation audit trail (S76), end to end.

S76's value is a pairing that cannot be reconstructed after the fact: which
edition was on the table, which seat was drawn, and what was taken while the
sheet said what it said. These tests build a draft where the right answer is
known by construction, so a wrong pairing is visible rather than plausible.
"""

import datetime as dt
import json

import polars as pl
import pytest

from pipeline.features import draft_pick
from pipeline.ingest import draft_log
from pipeline.normalize.names import match_key, name_position_key
from research import draft_record
from research.foundations import survival as survival_mod

TEAMS = 12
SLOT = 7
PROFILE = {"id": "fixture_12", "label": "12-team fixture", "teams": TEAMS, "real": True}

# Seat 7 of a 12-team snake holds 7 and 18. The audit asks what the sheet said at
# pick 7 about surviving to pick 18, so picks 8-17 are the ones that decide it.
HELD = [7, 18]


# Named in words, not digits. S12's normalizer strips digits, so "Player07" and
# "Player18" are the same name to every join in this repository -- a fixture that
# spelled them that way could not tell a correct pairing from one that matched
# every candidate to pick 1.
ORDINALS = (
    "One Two Three Four Five Six Seven Eight Nine Ten Eleven Twelve Thirteen "
    "Fourteen Fifteen Sixteen Seventeen Eighteen Nineteen Twenty Twentyone "
    "Twentytwo Twentythree Twentyfour"
).split()


def _name(pick: int) -> str:
    """The player taken at this overall pick, in the fixture board below."""
    return f"Player {ORDINALS[pick - 1]}"


def _board() -> str:
    """Two rounds, 24 picks, every name distinct under S12's normalizer."""
    return "\n".join(f"{n}. {_name(n)} RB ATL" for n in range(1, TEAMS * 2 + 1))


def _survival_artifact(candidates: list[dict]) -> dict:
    return {
        "method_id": f"{survival_mod.METHOD_ID}__{PROFILE['id']}",
        "primary_results": {
            "teams": TEAMS,
            "adp_snapshot_date": "2026-08-14",
            "opportunity_cost_method": "normal_approximation",
            "by_slot": [
                {
                    "slot": SLOT,
                    "held_picks": HELD,
                    "picks": [
                        {"pick": 7, "survival_measured_at": 18, "candidates": candidates},
                        {"pick": 18, "survival_measured_at": 18, "is_last_pick": True,
                         "candidates": []},
                    ],
                }
            ],
        },
    }


def _crosswalk(names: dict[str, str]) -> pl.DataFrame:
    """A crosswalk resolving the given fixture names to S12 ids."""
    return pl.DataFrame(
        {
            "gsis_id": list(names.values()),
            "match_key": [match_key(n, "RB", "ATL") for n in names],
            "name_position_key": [name_position_key(n, "RB") for n in names],
            "override_match_key": [None] * len(names),
        },
        schema_overrides={"override_match_key": pl.String},
    )


def _record(
    tmp_path, artifact: dict | None, board: str | None = None, crosswalk=None
) -> dict:
    """Write a draft log and a survival artifact, then run the audit."""
    snapshots = tmp_path / "snapshots" / "2026-08-30"
    snapshots.mkdir(parents=True)
    body = draft_log.payload(
        board if board is not None else _board(),
        profile_id=PROFILE["id"],
        season=2026,
        teams=TEAMS,
        draft_slot=SLOT,
        draft_date=dt.date(2026, 8, 30),
    )
    (snapshots / draft_log.filename(PROFILE["id"], 2026)).write_text(json.dumps(body))

    # An empty-but-correctly-shaped crosswalk: none of these fixture names is a
    # real player, so every pick lands unmatched -- which is itself the right
    # behaviour to exercise. match_external keeps unmatched rows with a null id
    # rather than dropping them, and a draft log that silently lost its unmatched
    # picks would be wrong about who was on the board.
    from tests.test_projections import CROSSWALK

    log = draft_pick.build(
        tmp_path / "snapshots", crosswalk=CROSSWALK if crosswalk is None else crosswalk
    )

    methods = tmp_path / "artifacts" / "ed" / "methods"
    methods.mkdir(parents=True)
    if artifact is not None:
        (methods / f"{survival_mod.METHOD_ID}__{PROFILE['id']}.json").write_text(
            json.dumps(artifact)
        )
    return draft_record.compute(log, PROFILE, "ed", root=tmp_path / "artifacts")


# -- the pairing -----------------------------------------------------------


def test_the_audit_records_what_this_seat_actually_took(tmp_path):
    results = _record(tmp_path, _survival_artifact([]))
    assert results["draft_slot"] == SLOT
    assert [p["pick"] for p in results["picks"]] == HELD
    assert results["picks"][0]["took"] == _name(7)
    assert results["picks"][1]["took"] == _name(18)
    assert results["adp_snapshot_date"] == "2026-08-14"


def test_survival_is_scored_against_what_the_log_says_happened(tmp_path):
    """The check S31.1 said the market could not supply.

    Player Eight goes at pick 8, between this seat's picks -- so he was NOT available
    at 18, whatever the approximation said. Player Twenty is not taken until after 18,
    so he was.
    """
    candidates = [
        {"player": _name(8), "position": "RB", "adp": 8.0, "p_available": 0.9,
         "approximation_note": None},
        {"player": _name(20), "position": "RB", "adp": 20.0, "p_available": 0.1,
         "approximation_note": None},
    ]
    results = _record(tmp_path, _survival_artifact(candidates))
    calls = {c["player"]: c for c in results["picks"][0]["survival_calls"]}
    assert calls[_name(8)]["was_available"] is False   # taken at 8, before 18
    assert calls[_name(20)]["was_available"] is True    # taken at 20, after 18
    assert calls[_name(8)]["p_available_predicted"] == 0.9


def test_the_last_held_pick_makes_no_survival_calls(tmp_path):
    """There is no next pick to survive to, so there is nothing to score."""
    results = _record(tmp_path, _survival_artifact([]))
    assert results["picks"][-1]["next_held_pick"] is None
    assert results["picks"][-1]["survival_calls"] == []


def test_a_candidate_with_no_published_spread_is_not_scored(tmp_path):
    """S31.2 returns None rather than a point estimate when FFC published no
    stdev. A null prediction cannot be right or wrong and must not be counted."""
    candidates = [
        {"player": _name(8), "position": "RB", "adp": 8.0, "p_available": None,
         "approximation_note": "no spread published; survival not computable"},
    ]
    results = _record(tmp_path, _survival_artifact(candidates))
    assert results["picks"][0]["survival_calls"] == []
    assert results["survival_calibration"] == []


def test_calibration_buckets_predicted_against_observed(tmp_path):
    candidates = [
        # High confidence, and wrong: taken before the next pick.
        {"player": _name(8), "position": "RB", "adp": 8.0, "p_available": 0.8,
         "approximation_note": None},
        {"player": _name(9), "position": "RB", "adp": 9.0, "p_available": 0.9,
         "approximation_note": None},
        # High confidence, and right.
        {"player": _name(21), "position": "RB", "adp": 21.0, "p_available": 0.85,
         "approximation_note": None},
    ]
    results = _record(tmp_path, _survival_artifact(candidates))
    bucket = next(b for b in results["survival_calibration"] if b["predicted_from"] == 0.75)
    assert bucket["n"] == 3
    assert bucket["observed_rate"] == pytest.approx(1 / 3, abs=0.001)   # 1 of 3 lasted
    assert bucket["mean_predicted"] == pytest.approx(0.85)


# -- the refusals ----------------------------------------------------------


def test_a_missing_survival_artifact_blocks_rather_than_audits_nothing(tmp_path):
    """Auditing a recommendation that cannot be read is auditing nothing."""
    with pytest.raises(draft_record.BlockedError, match="no survival artifact"):
        _record(tmp_path, None)


def test_a_sheet_never_rendered_for_the_drawn_seat_blocks(tmp_path):
    """The artifact covers every slot when the order was undrawn -- but if it
    does not cover the one drawn, there is no recommendation to audit."""
    artifact = _survival_artifact([])
    artifact["primary_results"]["by_slot"][0]["slot"] = 3
    with pytest.raises(draft_record.BlockedError, match="no slot 7"):
        _record(tmp_path, artifact)


# -- the pairing goes through the id, not the spelling ---------------------


def test_a_quote_and_a_pick_spelled_differently_are_still_the_same_player(tmp_path):
    """The quote is FFC's spelling and the log is a platform's, and they differ.

    On the real board FFC says "Kenneth Walker" where FantasyPros says "Kenneth
    Walker III"; the paste is a third spelling nobody can see in advance. Matched
    on the string this fails open -- the player is never found among the picks, so
    he reads as still available and the approximation looks better than it is.
    """
    ids = {_name(8): "00-0000008"}
    candidates = [
        {"player": f"{_name(8)} III", "player_id": "00-0000008", "position": "RB",
         "adp": 8.0, "p_available": 0.9, "approximation_note": None},
    ]
    results = _record(tmp_path, _survival_artifact(candidates), crosswalk=_crosswalk(ids))
    call = results["picks"][0]["survival_calls"][0]
    assert call["matched_in_log"] is True
    assert call["matched_by"] == "id"
    assert call["was_available"] is False       # taken at 8, before this seat's 18


def test_a_quote_that_matched_nothing_is_counted_rather_than_scored_as_available(tmp_path):
    """A pairing that half fails does not look like a failure. It looks like a
    strikingly well calibrated approximation."""
    candidates = [
        {"player": "Nobody In This Draft", "position": "RB", "adp": 8.0,
         "p_available": 0.9, "approximation_note": None},
    ]
    results = _record(tmp_path, _survival_artifact(candidates))
    assert results["pairing"]["unmatched"] == 1
    assert results["pairing"]["unmatched_share"] == 1.0
    assert results["picks"][0]["survival_calls"][0]["matched_in_log"] is False


def test_a_name_two_picks_answer_to_resolves_to_neither(tmp_path):
    """match_external's rule for its loose key. Attributing one man's pick to
    another reads downstream as a survival call that came out the other way."""
    board = "\n".join(
        f"{n}. {'Same Name' if n in (8, 20) else _name(n)} RB ATL"
        for n in range(1, TEAMS * 2 + 1)
    )
    candidates = [
        {"player": "Same Name", "position": "RB", "adp": 8.0, "p_available": 0.9,
         "approximation_note": None},
    ]
    results = _record(tmp_path, _survival_artifact(candidates), board=board)
    call = results["picks"][0]["survival_calls"][0]
    assert call["matched_in_log"] is False
    assert results["pairing"]["unmatched"] == 1


def test_the_pairing_block_says_how_every_call_was_joined(tmp_path):
    candidates = [
        {"player": _name(8), "position": "RB", "adp": 8.0, "p_available": 0.9,
         "approximation_note": None},
        {"player": "Nobody In This Draft", "position": "RB", "adp": 9.0,
         "p_available": 0.5, "approximation_note": None},
    ]
    results = _record(tmp_path, _survival_artifact(candidates))
    pairing = results["pairing"]
    assert pairing["calls"] == 2
    assert pairing["matched_by_name_and_position"] == 1
    assert pairing["unmatched"] == 1


def test_the_artifact_carries_no_evidence_grade(tmp_path):
    """S88 forbids grades here; S79 Step 4 is the grading engine."""
    results = _record(tmp_path, _survival_artifact([]))
    artifact = draft_record.export(results, PROFILE).to_dict()
    assert "evidence_grade" not in artifact
    assert artifact["claim_type"] == "DESCRIPTIVE"
    assert any("ONE DRAFT" in lim for lim in artifact["limitations"])


def test_a_candidate_already_gone_before_this_pick_is_not_scored(tmp_path):
    """S31.2's candidate window reaches back half a round (S19.4), so the block
    at pick 7 can name a player whose ADP is 1.3 -- long gone in the draft that
    happened. He was never a decision this seat had.

    Counting him is not a rounding error: on a board drafted close to ADP it
    turns a 4%-predicted bucket into a 91%-observed one, which reads as a
    devastating finding about the approximation and is an artifact of the window.
    """
    candidates = [
        # Taken at pick 2, five picks before this seat was on the clock.
        {"player": _name(2), "position": "RB", "adp": 2.0, "p_available": 0.01,
         "approximation_note": None},
        # Genuinely on the board at 7, and taken at 8.
        {"player": _name(8), "position": "RB", "adp": 8.0, "p_available": 0.4,
         "approximation_note": None},
    ]
    results = _record(tmp_path, _survival_artifact(candidates))
    scored = {c["player"] for c in results["picks"][0]["survival_calls"]}
    assert scored == {_name(8)}
