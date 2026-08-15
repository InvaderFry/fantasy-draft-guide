"""The refresh must not publish a board worse than the one it replaces (S83, S84).

The failure this guards is not a crash. Every step works: `run-research` exits 0
on a blocked module because for a dated edition a shut gate is a finding; the
methods directory is gitignored so a runner has nothing to fall back on; the
sheet renders a section with no artifact behind it as BLOCKED because a blank
space on a draft sheet is worse; the workflow commits whatever changed. Together
they publish 26 unusable pages over 26 good ones at 11:00 UTC, and nothing goes
red until somebody opens one at a draft table.

So these pin the two halves of "worse": a section that says BLOCKED where content
is due, and a board that quietly thinned.
"""

import json

import pytest
import typer

from pipeline import cli
from research import method as method_mod
from research import refresh

# The two shapes the sheet renders, verbatim in structure from `sheet.render`.
BLOCKED_TIERS = (
    '<section><h3>TIERS <span class="spec">S19.3</span></h3>'
    '<p class="missing"><strong>BLOCKED.</strong></p><ul class="missing">'
    "<li>no projection source</li></ul></section>"
)
FILLED_TIERS = (
    '<section><h3>TIERS <span class="spec">S19.3</span></h3>'
    "<table><tr><td>Bijan Robinson</td></tr></table></section>"
)
NOT_BUILT_TARGETS = (
    '<section><h3>TARGETS <span class="spec">S27</span></h3>'
    '<p class="missing"><strong>NOT BUILT.</strong> Targets is a research section '
    "(S27) requiring graded evidence.</p></section>"
)


# -- what counts as blocked --------------------------------------------------


def test_a_blocked_section_that_should_carry_content_is_caught():
    assert refresh.blocked_sections(BLOCKED_TIERS + NOT_BUILT_TARGETS) == ["TIERS"]


def test_not_built_is_not_a_failure():
    """TARGETS, DARTS and FALSE FRIENDS are honestly not built (S79) and say so on
    the page. Treating that as a degraded board would red the job every morning
    from now until the September build."""
    assert refresh.blocked_sections(FILLED_TIERS + NOT_BUILT_TARGETS) == []


def test_the_index_is_not_scanned(tmp_path):
    """It is a chooser and carries no sections; a page with nothing to block
    cannot be missing anything."""
    sheets = tmp_path / "e" / "sheets"
    sheets.mkdir(parents=True)
    (sheets / "index.html").write_text(BLOCKED_TIERS)
    (sheets / "half_ppr_12.html").write_text(FILLED_TIERS)
    assert refresh.blocked_pages("e", tmp_path) == {}


def test_every_sheet_is_scanned_not_just_one(tmp_path):
    """The survival block differs by seat, and so can what is missing from it."""
    sheets = tmp_path / "e" / "sheets"
    sheets.mkdir(parents=True)
    (sheets / "a.html").write_text(FILLED_TIERS)
    (sheets / "b.html").write_text(BLOCKED_TIERS)
    assert refresh.blocked_pages("e", tmp_path) == {"b.html": ["TIERS"]}


# -- what counts as thinner --------------------------------------------------


def metrics(priced: int = 183, board: int = 493, wr: int = 169) -> dict:
    return {
        "half_ppr_12": {
            "board_rows": board,
            "priced": priced,
            "survival_slots": 12,
            "players_priced": 185,
            "players_with_spread": 185,
            "tier_players": {"WR": wr},
        }
    }


def baseline(**kwargs) -> dict:
    return {"edition": "e", "checked": "2026-08-15", "profiles": metrics(**kwargs)}


def test_a_board_that_lost_a_fifth_of_its_prices_is_a_downgrade():
    """The half the floors cannot see: 183 priced arriving as 60 still renders a
    full-looking page, because the tier list is capped at twelve a position."""
    found = refresh.regressions(metrics(priced=60), baseline())
    assert any("priced fell 183 -> 60" in r for r in found)


def test_ordinary_drift_is_not_a_downgrade():
    """Retirements, a player leaving the board, the market quoting two fewer. The
    threshold is decision-relevant, not a tripwire on noise."""
    assert refresh.regressions(metrics(priced=174), baseline()) == []


def test_a_wider_board_is_never_a_downgrade():
    assert refresh.regressions(metrics(priced=220, board=600), baseline()) == []


def test_a_count_that_vanished_is_reported_as_nothing_produced():
    now = metrics()
    now["half_ppr_12"]["survival_slots"] = None
    found = refresh.regressions(now, baseline())
    assert any("survival_slots" in r and "nothing" in r for r in found)


def test_a_position_that_collapsed_is_caught_even_when_the_totals_hold():
    found = refresh.regressions(metrics(wr=20), baseline())
    assert any("tier_players.WR" in r for r in found)


def test_a_league_the_baseline_never_saw_is_skipped_not_failed():
    """Adding a league is not a regression, and neither is dropping one."""
    now = metrics()
    now["ppr_12"] = {"board_rows": 493, "priced": 211}
    assert refresh.regressions(now, baseline()) == []


def test_no_baseline_means_floors_only():
    assert refresh.regressions(metrics(priced=1), None) == []


def test_a_corrupt_baseline_does_not_red_the_refresh_forever(tmp_path):
    (tmp_path / "e").mkdir()
    (tmp_path / "e" / refresh.STATE_FILENAME).write_text("{not json")
    assert refresh.read_state("e", tmp_path) is None


# -- the gate, end to end ----------------------------------------------------


def edition(tmp_path, *, tiers_page: str, artifacts: bool = True) -> None:
    """A rendered edition on disk, as `research sheet` leaves one.

    The whole set, not one page: twelve seats, the slot-agnostic sheet and the
    chooser. The gate now counts them, for the same reason it reads them.
    """
    sheets = tmp_path / "2026-draft" / "sheets"
    sheets.mkdir(parents=True)
    (sheets / "index.html").write_text("<a href='half_ppr_12__slot01.html'>1</a>")
    (sheets / "half_ppr_12.html").write_text(tiers_page + NOT_BUILT_TARGETS)
    for seat in range(1, 13):
        (sheets / f"half_ppr_12__slot{seat:02d}.html").write_text(tiers_page + NOT_BUILT_TARGETS)
    methods = tmp_path / "2026-draft" / "methods"
    methods.mkdir(parents=True)
    if artifacts:
        (methods / "tiers_and_replacement_level__half_ppr_12.json").write_text(
            json.dumps(
                {
                    "primary_results": {
                        "adp_coverage": {"board_rows": 493, "priced": 183,
                                         "priced_share": 0.37,
                                         "adp_snapshot_date": "2026-08-15"},
                        "positions": {"WR": {"players": [{}] * 169}},
                    }
                }
            )
        )
        (methods / "survival_probability__half_ppr_12.json").write_text(
            json.dumps(
                {
                    "primary_results": {
                        "by_slot": [{}] * 12,
                        "players_priced": 185,
                        "players_with_spread": 185,
                    }
                }
            )
        )


@pytest.fixture
def one_league(monkeypatch, tmp_path):
    monkeypatch.setattr(method_mod, "ARTIFACT_DIR", tmp_path)
    monkeypatch.setattr(
        cli.config,
        "real_profiles",
        lambda: [{"id": "half_ppr_12", "label": "L", "teams": 12, "real": True}],
    )
    return tmp_path


def run(edition_name: str = "2026-draft", write: bool = True) -> int:
    try:
        cli.refresh_check(edition=edition_name, write=write)
    except typer.Exit as exc:
        return exc.exit_code
    return 0


def test_a_healthy_board_passes_and_records_itself(one_league):
    edition(one_league, tiers_page=FILLED_TIERS)
    assert run() == 0
    state = json.loads((one_league / "2026-draft" / refresh.STATE_FILENAME).read_text())
    assert state["profiles"]["half_ppr_12"]["priced"] == 183


def test_a_blocked_board_is_refused(one_league):
    """The morning a projection key rotates."""
    edition(one_league, tiers_page=BLOCKED_TIERS, artifacts=False)
    assert run() == 1


def test_a_refused_board_does_not_become_the_new_baseline(one_league):
    """Otherwise one bad morning is the standard every later morning is measured
    against, and the degradation is permanent and invisible."""
    edition(one_league, tiers_page=FILLED_TIERS)
    assert run() == 0
    good = (one_league / "2026-draft" / refresh.STATE_FILENAME).read_text()

    (one_league / "2026-draft" / "sheets" / "half_ppr_12.html").write_text(BLOCKED_TIERS)
    assert run() == 1

    assert (one_league / "2026-draft" / refresh.STATE_FILENAME).read_text() == good


def test_a_thinner_board_is_refused_against_yesterday(one_league):
    edition(one_league, tiers_page=FILLED_TIERS)
    assert run() == 0

    art = one_league / "2026-draft" / "methods" / "tiers_and_replacement_level__half_ppr_12.json"
    payload = json.loads(art.read_text())
    payload["primary_results"]["adp_coverage"]["priced"] = 40
    art.write_text(json.dumps(payload))
    assert run() == 1


def test_no_write_leaves_the_baseline_alone(one_league):
    edition(one_league, tiers_page=FILLED_TIERS)
    assert run(write=False) == 0
    assert not (one_league / "2026-draft" / refresh.STATE_FILENAME).exists()


# -- a board that was never rendered -----------------------------------------


def test_the_full_set_of_pages_is_what_the_gate_expects():
    """Every seat, the slot-agnostic sheet, the chooser. Read from `sheet`'s own
    helpers so the two cannot drift."""
    profiles = [{"id": "half_ppr_12", "label": "L", "teams": 12, "real": True}]
    assert refresh.expected_sheets(profiles) == sorted(
        ["index.html", "half_ppr_12.html"]
        + [f"half_ppr_12__slot{s:02d}.html" for s in range(1, 13)]
    )


def test_a_league_whose_order_is_drawn_needs_one_page(one_league):
    """A configured slot means one sheet named for the league, and no seats."""
    profiles = [{"id": "half_ppr_12", "label": "L", "teams": 12, "real": True, "draft_slot": 7}]
    assert refresh.expected_sheets(profiles) == ["half_ppr_12.html", "index.html"]


def test_a_missing_seat_sheet_is_refused(one_league):
    edition(one_league, tiers_page=FILLED_TIERS)
    (one_league / "2026-draft" / "sheets" / "half_ppr_12__slot07.html").unlink()
    assert run() == 1
    assert not (one_league / "2026-draft" / refresh.STATE_FILENAME).exists()


def test_a_missing_chooser_is_refused(one_league):
    """S83's index is how a seat is found at the table; an edition without one is
    not a published board."""
    edition(one_league, tiers_page=FILLED_TIERS)
    (one_league / "2026-draft" / "sheets" / "index.html").unlink()
    assert run() == 1


def test_an_edition_that_rendered_nothing_is_refused(one_league, capsys):
    """The gate's own blind spot: nothing rendered means nothing blocked, and the
    counts come from the artifacts rather than the pages, so a morning that
    produced no board at all passed both halves and recorded itself as the board
    to beat."""
    (one_league / "2026-draft" / "methods").mkdir(parents=True)
    assert run() == 1
    assert not (one_league / "2026-draft" / refresh.STATE_FILENAME).exists()
    assert "missing" in capsys.readouterr().err
