"""The draft-night path, driven through the CLI (S76, S84, S51).

The three commands this exercises are the ones that get exactly one attempt.
S84 makes the record immutable, so a paste frozen with a defect cannot be
re-recorded that day, and S10B says every draft before the first successful
record is unrecoverable. Twenty minutes after a draft is not when to find out
that the parser rejects a platform's line shape.

Every other test in this suite exercises the functions. These drive
`pipeline.cli.app` itself, because the wiring -- the options, the snapshot
write, the refusals -- is the part nothing else covers.

**Nothing here may touch `data/snapshots/`.** That directory is the committed
immutable archive; a synthetic draft written into it is corpus corruption, not a
test artifact. `pipeline.snapshot` reads a module constant, so it is pointed at
`tmp_path` the way `conftest.synthetic_season` points the raw directory.
"""

import datetime as dt
import json

import polars as pl
import pytest
from typer.testing import CliRunner

from pipeline import cli, snapshot
from pipeline.features import draft_pick
from pipeline.normalize.names import match_key, name_position_key
from research import draft_record
from research.foundations import survival as survival_mod

runner = CliRunner()

TEAMS = 12
ROUNDS = 14          # 8 starters + 6 bench, the shape of both real profiles
SLOT = 7
SEASON = 2026
DRAFT_DAY = dt.date(2026, 8, 30)
PROFILE_ID = "half_ppr_12"

# Names in the shape a results page prints them, distinct under S12's normalizer.
FIRST = ("Jahmyr Bijan Puka Jamarr Jaxon Jonathan Derrick Devon James Ashton Chase "
         "Saquon Josh Kenneth Omarion Breece Jeremiyah Kyren Cam Javonte Travis "
         "Dandre Bucky Quinshon").split()
LAST = ("Gibbs Robinson Nacua Chase Smith Taylor Henry Achane Cook Jeanty Brown "
        "Barkley Jacobs Walker Hampton Hall Love Williams Skattebo Wilson Etienne "
        "Swift Irving Judkins").split()
POSITIONS = ("RB", "WR", "QB", "TE")


def player(overall: int) -> str:
    """The player taken at this overall pick. Distinct for all 168.

    Distinct *under S12's normalizer*, which is the only distinctness that
    counts: it strips digits, so a numeric disambiguator is no disambiguator at
    all. 24 first names against 24 surnames indexed independently gives 576
    pairs, and the board needs 168.
    """
    index = overall - 1
    return f"{FIRST[index % len(FIRST)]} {LAST[index // len(FIRST)]}"


def position(overall: int) -> str:
    return POSITIONS[(overall - 1) % len(POSITIONS)]


# Picks whose results page prints a generational suffix the price feed does not.
# This is the real disagreement, in the real direction: on the 2026 board FFC
# quotes "Kenneth Walker" while every other source says "Kenneth Walker III".
SUFFIXED = frozenset({8, 9, 32})


def printed(overall: int) -> str:
    """The name as the platform's results page prints it -- what gets pasted."""
    name = player(overall)
    return f"{name} III" if overall in SUFFIXED else name


def board_text() -> str:
    """A full 12x14 board in a platform's `round.pick` shape, with real furniture.

    Deliberately not clean. A header row, a blank line and a section label are
    what a paste from a results page actually contains, and a parser that only
    survives hand-typed input has not been rehearsed.
    """
    lines = ["Round\tPick\tPlayer\tPos\tTeam", ""]
    for overall in range(1, TEAMS * ROUNDS + 1):
        rnd = (overall - 1) // TEAMS + 1
        in_round = (overall - 1) % TEAMS + 1
        if in_round == 1:
            lines.append(f"Round {rnd}")
        lines.append(f"{rnd}.{in_round:02d} {printed(overall)} {position(overall)} ATL")
    return "\n".join(lines) + "\n"


def crosswalk(unresolvable: frozenset[str] = frozenset()) -> pl.DataFrame:
    """S12's crosswalk, resolving every drafted name but the ones named."""
    rows = [
        (f"00-{overall:07d}", player(overall), position(overall))
        for overall in range(1, TEAMS * ROUNDS + 1)
        if player(overall) not in unresolvable
    ]
    return pl.DataFrame(
        {
            "gsis_id": [r[0] for r in rows],
            "match_key": [match_key(r[1], r[2], "ATL") for r in rows],
            "name_position_key": [name_position_key(r[1], r[2]) for r in rows],
            "override_match_key": [None] * len(rows),
        },
        schema_overrides={"override_match_key": pl.String},
    )


def survival_artifact() -> dict:
    """What the sheet said, quoted at each pick seat 7 holds.

    The quoted spellings are FFC's and the log's are the platform's, and they
    differ the way the real ones do -- a generational suffix the results page
    prints and the price feed does not. The pairing has to go through the id.
    """
    held = survival_mod.held_picks(TEAMS, SLOT, rounds=ROUNDS)
    blocks = []
    for index, pick in enumerate(held):
        nxt = held[index + 1] if index + 1 < len(held) else None
        candidates = []
        # Quote the six players who really went between this pick and the next,
        # plus two who did not go until much later. The right answer is known by
        # construction: the first six were not available, the last two were.
        for offset in range(1, 7):
            taken = pick + offset
            if taken > TEAMS * ROUNDS:
                break
            candidates.append(
                {
                    "player": player(taken),
                    "player_id": f"00-{taken:07d}",
                    "position": position(taken),
                    "team": "ATL",
                    "adp": float(taken),
                    "adp_stdev": 4.0,
                    "p_available": round(0.05 + 0.02 * offset, 3),
                    "approximation_note": None,
                }
            )
        for late in (TEAMS * ROUNDS - 3, TEAMS * ROUNDS - 2):
            candidates.append(
                {
                    "player": player(late),
                    "player_id": f"00-{late:07d}",
                    "position": position(late),
                    "team": "ATL",
                    "adp": float(pick) + 4.0,
                    "adp_stdev": 6.0,
                    "p_available": 0.8,
                    "approximation_note": None,
                }
            )
        blocks.append(
            {
                "pick": pick,
                "survival_measured_at": nxt if nxt is not None else pick,
                "is_last_pick": nxt is None,
                "candidates": candidates,
            }
        )
    return {
        "method_id": f"{survival_mod.METHOD_ID}__{PROFILE_ID}",
        "primary_results": {
            "teams": TEAMS,
            "draft_slot": "unknown",
            "adp_snapshot_date": "2026-08-29",
            "opportunity_cost_method": survival_mod.OPPORTUNITY_COST_METHOD,
            "by_slot": [{"slot": SLOT, "held_picks": held, "picks": blocks}],
        },
    }


@pytest.fixture
def draft_night(tmp_path, monkeypatch):
    """A draft night in a temporary directory: paste on disk, archive redirected."""
    paste = tmp_path / "picks.txt"
    paste.write_text(board_text())

    snapshots = tmp_path / "snapshots"
    monkeypatch.setattr(snapshot, "SNAPSHOT_DIR", snapshots)
    monkeypatch.setattr(draft_pick, "SNAPSHOT_DIR", snapshots)
    monkeypatch.setattr(
        cli.player_ids, "load_player_ids", lambda *a, **k: crosswalk()
    )

    methods = tmp_path / "artifacts" / "2026-draft" / "methods"
    methods.mkdir(parents=True)
    (methods / f"{survival_mod.METHOD_ID}__{PROFILE_ID}.json").write_text(
        json.dumps(survival_artifact())
    )
    return {"paste": paste, "snapshots": snapshots, "artifacts": tmp_path / "artifacts"}


def record(paste, *, extra=()):
    return runner.invoke(
        cli.app,
        ["draft-record", "--profile", PROFILE_ID, "--slot", str(SLOT),
         "--picks", str(paste), "--season", str(SEASON),
         "--date", DRAFT_DAY.isoformat(), *extra],
    )


# -- the rehearsal ---------------------------------------------------------


def test_a_dry_run_reports_the_board_and_writes_nothing(draft_night):
    result = record(draft_night["paste"], extra=["--dry-run"])
    assert result.exit_code == 0, result.output
    assert f"parsed {TEAMS * ROUNDS} picks, {ROUNDS} rounds" in result.output
    assert "round.pick=168" in result.output
    assert f"seat {SLOT} holds" in result.output
    assert "168/168 names resolved" in result.output
    assert "dry run: nothing written" in result.output
    assert not draft_night["snapshots"].exists()


def test_a_dry_run_names_the_players_the_crosswalk_cannot_resolve(
    draft_night, monkeypatch
):
    """An unmatched name pairs with nothing later, and scores as available
    whether or not he was. Worth knowing before the record is frozen."""
    missing = frozenset({player(7), player(19)})
    monkeypatch.setattr(
        cli.player_ids, "load_player_ids", lambda *a, **k: crosswalk(missing)
    )
    result = record(draft_night["paste"], extra=["--dry-run"])
    assert result.exit_code == 0, result.output
    assert "166/168 names resolved" in result.output
    assert player(7) in result.output
    assert "pairs with nothing in draft-review" in result.output


def test_a_paste_the_parser_refuses_exits_non_zero_and_writes_nothing(draft_night):
    short = draft_night["paste"].parent / "short.txt"
    short.write_text("\n".join(board_text().splitlines()[:-8]) + "\n")
    result = record(short, extra=["--dry-run"])
    assert result.exit_code == 1
    assert "draft log rejected" in result.output
    assert not draft_night["snapshots"].exists()


def test_the_whole_path_runs_from_a_paste_to_a_paired_audit(draft_night, tmp_path):
    """paste -> record -> draft_pick -> review, through the CLI."""
    assert record(draft_night["paste"]).exit_code == 0

    frozen = draft_night["snapshots"] / DRAFT_DAY.isoformat()
    assert (frozen / f"draft_{PROFILE_ID}_{SEASON}.json").exists()
    assert (frozen / "manifest.json").exists()

    log = draft_pick.build(draft_night["snapshots"], crosswalk=crosswalk())
    assert log.height == TEAMS * ROUNDS
    assert log["player_id"].null_count() == 0
    assert log.filter(pl.col("is_drafter")).height == ROUNDS

    profile = {"id": PROFILE_ID, "label": "fixture", "teams": TEAMS, "real": True}
    results = draft_record.compute(
        log, profile, "2026-draft", root=draft_night["artifacts"]
    )
    assert results["draft_slot"] == SLOT
    assert results["n"] == ROUNDS
    assert [p["pick"] for p in results["picks"]] == survival_mod.held_picks(
        TEAMS, SLOT, rounds=ROUNDS
    )


def test_every_quote_pairs_even_where_the_spellings_differ(draft_night):
    """The board says "Kenneth Walker III" and the price feed says "Kenneth
    Walker". Nothing may be lost to that."""
    assert record(draft_night["paste"]).exit_code == 0
    log = draft_pick.build(draft_night["snapshots"], crosswalk=crosswalk())
    profile = {"id": PROFILE_ID, "label": "fixture", "teams": TEAMS, "real": True}
    results = draft_record.compute(
        log, profile, "2026-draft", root=draft_night["artifacts"]
    )

    pairing = results["pairing"]
    assert pairing["calls"] > 0
    assert pairing["unmatched"] == 0
    assert pairing["matched_by_id"] == pairing["calls"]

    # The disagreement is really in the fixture: the paste carries the suffix,
    # the quote does not, and they still resolved to the same player.
    assert " III" in draft_night["paste"].read_text()
    quoted = {c["player"] for row in results["picks"] for c in row["survival_calls"]}
    assert player(8) in quoted and not any(n.endswith(" III") for n in quoted)
    suffixed = [
        c for row in results["picks"] for c in row["survival_calls"]
        if c["player"] in {player(n) for n in SUFFIXED}
    ]
    assert suffixed
    assert all(c["matched_by"] == "id" for c in suffixed)


def test_the_calls_are_right_by_construction(draft_night):
    """The six quoted at each pick went between it and the next; the two late
    ones did not. A pairing that failed open would call all eight available."""
    assert record(draft_night["paste"]).exit_code == 0
    log = draft_pick.build(draft_night["snapshots"], crosswalk=crosswalk())
    profile = {"id": PROFILE_ID, "label": "fixture", "teams": TEAMS, "real": True}
    results = draft_record.compute(
        log, profile, "2026-draft", root=draft_night["artifacts"]
    )

    block = results["picks"][0]
    gone = [c for c in block["survival_calls"] if not c["was_available"]]
    stayed = [c for c in block["survival_calls"] if c["was_available"]]
    assert len(gone) == 6
    assert len(stayed) == 2


def test_the_calibration_table_has_more_than_one_bucket(draft_night):
    """Every call landing in one bin is the signature of the pairing failing,
    not of the approximation being good."""
    assert record(draft_night["paste"]).exit_code == 0
    log = draft_pick.build(draft_night["snapshots"], crosswalk=crosswalk())
    profile = {"id": PROFILE_ID, "label": "fixture", "teams": TEAMS, "real": True}
    results = draft_record.compute(
        log, profile, "2026-draft", root=draft_night["artifacts"]
    )
    buckets = results["survival_calibration"]
    assert len(buckets) >= 2
    assert sum(b["n"] for b in buckets) == results["pairing"]["calls"]


def test_the_record_cannot_be_taken_twice_on_one_date(draft_night):
    """S84's overwrite guard, which is why --dry-run exists at all."""
    assert record(draft_night["paste"]).exit_code == 0
    second = record(draft_night["paste"])
    assert second.exit_code == 1
    assert "overwrite" in second.output.lower() or "exists" in second.output.lower()
