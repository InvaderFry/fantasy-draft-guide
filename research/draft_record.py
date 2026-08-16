"""S76 -- the recommendation audit trail. DESCRIPTIVE (S2.2, S88).

S59 promoted this into the MVP, and the reason it gives is the reason it is here
before the guide is:

    it is a persisted JSON write of data the pipeline already produces, it costs
    almost nothing to add now, it cannot be reconstructed later, and it is the
    only mechanism by which the evidence grades in S3.1 are ever checked against
    reality

What is not reconstructible is the pairing -- which board was in front of the
drafter, which seat was drawn, and what was actually taken while the sheet said
what it said. This module writes that pairing down.

**The board is not reconstructible either, and the sentence that used to stand
here saying it was is what made that invisible.** The captures are committed and
the dated editions are dated, both true; but the board on the table is
`2026-draft`, which the daily refresh regenerates in place, and a rebuild cannot
recover it -- `survival.py` prices off ``snapshot_date.max()`` and nothing in the
pipeline pins an as-of date. A board rebuilt the morning after quotes prices the
sheet never carried, pairs against them cleanly, and reports a calibration table
rather than an error. So the board is copied to a dated immutable edition when
the draft is recorded (`research/freeze.py`), the record names that edition, and
`run` reads the edition the record names.

**The survival check is the part worth the effort.** S31.1 is resolved and the
answer is negative: FFC publishes no pick distribution, so every P(available) on
the sheet is S19.4's labelled normal approximation. The log records whether the
player was in fact still there. Put side by side, one draft is an anecdote and
forty are a calibration curve -- and S77 is the review that eventually draws it.

No evidence grades: S88 forbids them here and the grading engine is S79 Step 4.
This records prices and contributions so S77 can grade them later, which is
exactly what S76 asks for.
"""

from __future__ import annotations

import json
from typing import Any

import polars as pl

from pipeline.config import PROCESSED_DIR, ConfigError, real_profiles
from pipeline.normalize.names import name_position_key, normalize_name
from research.foundations import survival as survival_mod
from research.method import ARTIFACT_DIR, MethodArtifact, default_edition

METHOD_ID = "draft_record"
VERSION = "1.0.0"


class BlockedError(ConfigError):
    """A prerequisite is missing, and guessing at it would produce a plausible lie."""


def picks(profile_id: str, *, processed_dir=None) -> pl.DataFrame:
    # Resolved at call time rather than bound as a default, so the whole command
    # can be driven over a temporary tree in a test -- the rule `refresh-check`
    # already follows for its artifact root.
    processed_dir = PROCESSED_DIR if processed_dir is None else processed_dir
    path = processed_dir / "draft_pick.parquet"
    if not path.exists():
        raise BlockedError(
            "data/processed/draft_pick.parquet has not been built. Record the draft with "
            "`research draft-record` and then run `research build-tables --tables "
            "draft_pick` (S13, S76)."
        )
    frame = pl.read_parquet(path).filter(pl.col("profile_id") == profile_id)
    if not frame.height:
        raise BlockedError(
            f"no draft recorded for profile {profile_id!r}. S76's audit trail is the "
            "pairing of what was recommended with what happened, and half of it is "
            "missing."
        )
    return frame.sort("overall_pick")


def _survival_quotes(edition: str, profile_id: str, slot: int, root=ARTIFACT_DIR) -> dict:
    """What the sheet said about each candidate at each of this seat's picks.

    Keyed by pick; the candidates carry their own identity, which is what the log
    is matched against. The artifact holds every slot when the order was undrawn,
    which is precisely why the drawn seat has to be recorded separately -- the
    artifact does not know which of the twelve actually happened.
    """
    path = root / edition / "methods" / f"{survival_mod.METHOD_ID}__{profile_id}.json"
    if not path.exists():
        raise BlockedError(
            f"no survival artifact at {path}. The sheet the drafter used was rendered "
            "from it, so without it there is no recommendation to audit (S16, S31.2). "
            "Editions are dated and the live board is `2026-draft` -- pass the edition "
            "whose sheet was actually on the table."
        )
    # NaN is legal in this repo's artifacts and illegal in strict JSON; the
    # writer emits it, so the reader accepts it (see research/sheet.py).
    results = json.loads(path.read_text())["primary_results"]
    entry = next((s for s in results.get("by_slot", []) if int(s["slot"]) == slot), None)
    if entry is None:
        raise BlockedError(
            f"the survival artifact covers no slot {slot}; the draft was recorded from "
            f"seat {slot} but the sheet was not rendered for it (S31.2)."
        )
    quotes: dict[int, list[dict]] = {}
    for block in entry["picks"]:
        quotes[block["pick"]] = [
            {**candidate, "survival_measured_at": block.get("survival_measured_at")}
            for candidate in block["candidates"]
        ]
    return {
        "held_picks": entry["held_picks"],
        "quotes": quotes,
        "adp_snapshot_date": results.get("adp_snapshot_date"),
        "opportunity_cost_method": results.get("opportunity_cost_method"),
    }


def compute(
    log: pl.DataFrame, profile: dict[str, Any], edition: str, *, root=ARTIFACT_DIR
) -> dict[str, Any]:
    """Pair every pick this seat made with what the sheet said beforehand."""
    slot = int(log.filter(pl.col("is_drafter"))["slot"][0])
    survival = _survival_quotes(edition, profile["id"], slot, root=root)
    draft_day = str(log["draft_date"][0])

    # Independent of the freeze, and deliberately so. The freeze protects the
    # recording path; this protects everyone else -- anyone who passes --edition
    # by hand, or who rebuilds the live board and reviews against that. It is the
    # one failure in this module that produces no error and no odd-looking
    # number: a board priced after the draft still pairs, still fills every
    # bucket, and is measuring quotes that did not exist on the night.
    priced = survival.get("adp_snapshot_date")
    if priced and str(priced) > draft_day:
        raise BlockedError(
            f"the survival artifact in edition {edition!r} is priced off the ADP capture "
            f"of {priced}, which is after the draft on {draft_day}. That is not the board "
            "the drafter used, and auditing against it would report the approximation as "
            "better or worse than it was with nothing on the page saying so.\n"
            "  `research draft-record` freezes the board it drafted against and names that "
            "edition in the record, which is what this reads by default. To take one after "
            "the fact, check out the commit whose sheets were on the table and run "
            "`research freeze-edition` from there -- artifacts/2026-draft is committed "
            "(S7, S76)."
        )

    taken_at = {int(r["overall_pick"]): r for r in log.iter_rows(named=True)}

    rows = []
    for index, pick in enumerate(survival["held_picks"]):
        mine = taken_at.get(pick)
        if mine is None:
            continue  # the draft ended before this pick
        next_pick = (
            survival["held_picks"][index + 1] if index + 1 < len(survival["held_picks"]) else None
        )
        rows.append(
            {
                "pick": pick,
                "round": mine["round"],
                "took": mine["source_player_name"],
                "position": mine["position"],
                "next_held_pick": next_pick,
                "survival_calls": _calls(survival, pick, next_pick, taken_at),
            }
        )

    return {
        "profile_id": profile.get("id"),
        "profile_label": profile.get("label"),
        "edition": edition,
        "season": int(log["season"][0]),
        "draft_date": str(log["draft_date"][0]),
        "teams": int(log["teams"][0]),
        "draft_slot": slot,
        "adp_snapshot_date": survival["adp_snapshot_date"],
        "opportunity_cost_method": survival["opportunity_cost_method"],
        "picks": rows,
        "pairing": _pairing(rows),
        "survival_calibration": _calibration(rows),
        "n": len(rows),
    }


def _identities(player_id: str | None, name: str, position: str | None) -> list:
    """Every key one player can be recognised by, strongest first.

    S12's ``gsis_id`` when both sides have one, then name and position, then the
    name alone. The last is weak everywhere else in this repository and is safe
    here: the population is one draft, and two players sharing a normalized name
    inside 168 picks is not a thing that happens.
    """
    keys: list = []
    if player_id:
        keys.append(("id", player_id))
    keys.append(("name_pos", name_position_key(name, position)))
    keys.append(("name", normalize_name(name)))
    return keys


def _drafted_at(taken_at: dict) -> dict:
    """When each drafted player went, under every key he can be found by.

    A key two different picks both answer to is dropped rather than resolved to
    the first of them -- ``match_external``'s rule for its own loose key, and for
    the same reason. Attributing one man's pick to another does not look like an
    error downstream; it looks like a survival call that came out the other way.
    """
    index: dict = {}
    ambiguous = set()
    for overall, row in taken_at.items():
        for key in _identities(
            row.get("player_id"), row["source_player_name"], row.get("position")
        ):
            if key in index and index[key] != int(overall):
                ambiguous.add(key)
            index.setdefault(key, int(overall))
    for key in ambiguous:
        del index[key]
    return index


def _calls(survival: dict, pick: int, next_pick: int | None, taken_at: dict) -> list[dict]:
    """For each name the sheet quoted at this pick: predicted, then observed.

    **The pairing goes through the id, not the spelling.** The quote is Fantasy
    Football Calculator's name and the log is whatever a platform's results page
    printed, and the two do not agree -- FFC and FantasyPros already differ on
    Kenneth Walker III and Patrick Mahomes II on the board this was written
    against, and the paste is a third spelling that cannot be seen from here in
    advance. Matching on the string silently fails open: a quoted player whose
    name spells differently is never found among the picks, so he reads as still
    available, and the calibration table reports the approximation as far better
    than it is. ``player_id`` is on both sides -- ``draft_pick.build`` and
    ``adp_history`` both run ``match_external`` -- and name keys are the fallback.

    Two filters, and the first one is easy to miss. S31.2 draws its candidate
    list from ADP with a buffer reaching back half a round (S19.4's
    AVAILABILITY_BUFFER), so at pick 7 the block can name players whose ADP is
    1.3 -- players who, in the draft that actually happened, were gone by pick 3.
    They were never a decision this seat had, and scoring them inflates the
    observed availability rate towards 1 without any of it being a real call.
    Counting them once turned a 4%-predicted bucket into a 91%-observed one,
    which reads as a devastating finding about the approximation and is an
    artifact of the window.

    The second filter is the actual question: of those genuinely on the board,
    who was still there at the next pick. The log knows that exactly, which is
    why every seat is recorded rather than only this one.
    """
    if next_pick is None:
        return []
    drafted = _drafted_at(taken_at)

    out = []
    for candidate in survival["quotes"].get(pick, []):
        if candidate.get("p_available") is None:
            continue
        went, matched_by = None, None
        for key in _identities(
            candidate.get("player_id"), candidate["player"], candidate.get("position")
        ):
            if key in drafted:
                went, matched_by = drafted[key], key[0]
                break
        if went is not None and went < pick:
            continue  # off the board before this seat was on the clock
        out.append(
            {
                "player": candidate["player"],
                "adp": candidate.get("adp"),
                "p_available_predicted": candidate["p_available"],
                "was_available": not (went is not None and pick < went < next_pick),
                # A quote that matched nothing anywhere in the log is either a
                # player who went undrafted or a pairing that failed, and the two
                # are indistinguishable from one row. Recorded per call so
                # `pairing` can count them and say which this draft looks like.
                "matched_in_log": went is not None,
                "matched_by": matched_by,
                "approximation_note": candidate.get("approximation_note"),
            }
        )
    return sorted(out, key=lambda r: r["adp"] if r["adp"] is not None else 999)


def _pairing(rows: list[dict]) -> dict[str, Any]:
    """How the quotes were joined to the picks, and how much of it held.

    The number to read is ``unmatched``. Every one of those calls scores as
    available whether or not the player was, so a pairing that quietly half
    fails does not look like a failure -- it looks like a strikingly well
    calibrated approximation.
    """
    calls = [c for row in rows for c in row["survival_calls"]]
    by = {"id": 0, "name_pos": 0, "name": 0}
    for call in calls:
        if call["matched_by"] in by:
            by[call["matched_by"]] += 1
    unmatched = sum(1 for c in calls if not c["matched_in_log"])
    return {
        "calls": len(calls),
        "matched_by_id": by["id"],
        "matched_by_name_and_position": by["name_pos"],
        "matched_by_name": by["name"],
        "unmatched": unmatched,
        "unmatched_share": round(unmatched / len(calls), 4) if calls else None,
    }


# Predicted-probability buckets. Coarse on purpose: one draft supplies a couple
# of hundred calls at most, and a twenty-bin curve off that many is noise with a
# shape. S77 draws the real curve once there are seasons of them.
CALIBRATION_BINS = ((0.0, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 1.0))


def _calibration(rows: list[dict]) -> list[dict]:
    """Predicted P(available) against the rate actually observed.

    The empirical check on S19.4's normal approximation that S31.1 said the
    market could not provide. Reported with counts so it is obvious how thin it
    is; a single draft is an anecdote, and the artifact says so in limitations.
    """
    calls = [c for row in rows for c in row["survival_calls"]]
    out = []
    for low, high in CALIBRATION_BINS:
        bucket = [c for c in calls if low <= c["p_available_predicted"] < high]
        if not bucket:
            continue
        out.append(
            {
                "predicted_from": low,
                "predicted_to": high,
                "mean_predicted": round(
                    sum(c["p_available_predicted"] for c in bucket) / len(bucket), 3
                ),
                "observed_rate": round(
                    sum(1 for c in bucket if c["was_available"]) / len(bucket), 3
                ),
                "n": len(bucket),
            }
        )
    return out


def export(results: dict[str, Any], profile: dict[str, Any]) -> MethodArtifact:
    return MethodArtifact(
        method_id=f"{METHOD_ID}__{profile['id']}__{results['season']}",
        title=f"Draft record and recommendation audit -- {profile.get('label')}",
        version=VERSION,
        claim_type="DESCRIPTIVE",
        spec_sections=["S76", "S77", "S31.1", "S31.2", "S19.4"],
        population={
            "registry_id": METHOD_ID,
            "profile_id": profile.get("id"),
            "season": results.get("season"),
            "draft_date": results.get("draft_date"),
            "draft_slot": results.get("draft_slot"),
            "edition_used": results.get("edition"),
            "adp_snapshot_date": results.get("adp_snapshot_date"),
            "opportunity_cost_method": results.get("opportunity_cost_method"),
        },
        outcome=None,
        sample_size=results.get("n", 0),
        primary_results=results,
        limitations=[
            "ONE DRAFT. The calibration table is an anecdote with counts attached, not a "
            "calibration curve. S77 draws the curve once several seasons are recorded; "
            "this artifact exists so those seasons have something to accumulate into.",
            "The survival numbers being checked are S19.4's normal approximation, not an "
            "empirical curve -- S31.1 established that Fantasy Football Calculator "
            "publishes no pick distribution. A miss here is a miss of the approximation, "
            "and cannot separate that from the market simply moving after the capture.",
            "ADP is the FFC mock population and the draft is one home league (S10B). A "
            "league that reaches or fades against the market will look like a survival "
            "miss and is not one.",
            "Only the picks this seat held are audited. What the sheet said about the "
            "other eleven seats is in the artifact and is not evaluated here.",
            "No evidence grades (S88, S79 Step 4). Prices and contributions are recorded "
            "so S77 can grade them; nothing here grades anything.",
        ],
        sources=[
            "league draft log (S76, recorded by hand)",
            f"survival artifact {survival_mod.METHOD_ID} (S31.2)",
            "Fantasy Football Calculator ADP archive (S84)",
        ],
    )


def recorded_edition(log: pl.DataFrame) -> str | None:
    """The frozen board this league's record says it drafted against (S76).

    Written by `research draft-record` into the immutable snapshot payload and
    carried into `draft_pick` as a column, so it is read back rather than guessed.
    None for a record taken before the freeze existed, which falls back and then
    blocks loudly -- the old behaviour, and better than quietly auditing against
    whatever `2026-draft` holds today.
    """
    if "board_edition" not in log.columns:
        return None
    named = log["board_edition"].drop_nulls().unique().to_list()
    return str(named[0]) if named else None


def run(
    *, edition: str | None = None, processed_dir=None, root=None
) -> list[tuple[dict[str, Any], MethodArtifact]]:
    """Audit every league that has drafted, each against its own board.

    The edition is resolved per profile, not once for the run. The two leagues
    draft on different nights against boards a week apart, so one edition for both
    would audit at least one of them against a board it never saw. An explicit
    `edition` still wins, for the recovery case where the record names one that
    has to be corrected.
    """
    root = ARTIFACT_DIR if root is None else root
    out = []
    for profile in real_profiles():
        try:
            log = picks(profile["id"], processed_dir=processed_dir)
        except BlockedError:
            continue  # this league has not drafted yet; the other one may have
        used = edition or recorded_edition(log) or default_edition()
        results = compute(log, profile, used, root=root)
        out.append((results, export(results, profile)))
    if not out:
        raise BlockedError(
            "no draft has been recorded for any real profile. `research draft-record` "
            "writes the log; this reads it (S76)."
        )
    return out
