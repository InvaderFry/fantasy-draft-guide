"""The daily draft-board refresh (S83, S84).

The archive job captures ADP every morning; a second job rebuilds the sheets from
it, because the draft dates are unknown and there is therefore no day on which
somebody would think to rebuild them by hand.

Everything here guards the same failure: the refresh silently stopping. It runs
unattended at 11:00 UTC and its output is a page somebody reads once, at a draft
table, under time pressure. A refresh that quietly stopped in August looks exactly
like one that ran this morning -- which is why the sheet carries the capture date
and why these assertions exist at all.
"""

from pathlib import Path

import yaml

from pipeline.cli import MARKET_DEPENDENT_MODULES, research_modules

WORKFLOW = Path(__file__).resolve().parent.parent / ".github/workflows/adp-archive.yml"
LIVE_EDITION = "2026-draft"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def _run_script(job: str) -> str:
    return "\n".join(s.get("run", "") for s in _workflow()["jobs"][job]["steps"])


def test_every_module_the_refresh_asks_for_exists():
    """`run-research --modules` raises BadParameter on an unknown id.

    A renamed METHOD_ID would therefore break the scheduled run rather than the
    test suite, and a scheduled run breaks where nobody is looking.
    """
    known = research_modules()
    for module in MARKET_DEPENDENT_MODULES:
        assert module in known, f"{module} is not a runnable module"
    script = _run_script("sheets")
    for module in MARKET_DEPENDENT_MODULES:
        assert module in script, f"the refresh job does not run {module}"


def test_the_refresh_does_not_rerun_the_season_modules():
    """S25 and S21.1 describe completed seasons and do not move between drafts.

    Re-running them daily would mean pulling the whole play-by-play archive into
    CI to recompute an identical number. Their artifacts are carried forward
    instead, which is why .gitignore keeps them.
    """
    script = _run_script("sheets")
    season_modules = set(research_modules()) - set(MARKET_DEPENDENT_MODULES)
    assert season_modules
    for module in season_modules:
        assert module not in script


def test_the_carried_forward_artifacts_are_committed():
    """If they were ignored, REGRESSION would print BLOCKED every morning.

    `sheet.write()` renders whatever is in the edition's methods directory. An
    artifact the refresh does not regenerate has to survive in the checkout, and
    the only thing making that true is the negation in .gitignore.
    """
    ignore = (WORKFLOW.parent.parent.parent / ".gitignore").read_text()
    season_modules = set(research_modules()) - set(MARKET_DEPENDENT_MODULES)
    for module in season_modules:
        assert f"!artifacts/{LIVE_EDITION}/methods/{module}.json" in ignore


def test_the_refresh_writes_the_live_edition_and_not_a_dated_one():
    """A dated edition would move the phone URL every morning."""
    script = _run_script("sheets")
    assert script.count(f"--edition {LIVE_EDITION}") == 2  # run-research and sheet


def test_a_failed_refresh_cannot_cost_a_capture_day():
    """S84: an uncaptured day of ADP movement is gone permanently, and a sheet
    can always be re-rendered. So they are separate jobs, and the capture is the
    one that goes first."""
    jobs = _workflow()["jobs"]
    assert "sheets" in jobs and "capture" in jobs
    assert jobs["sheets"]["needs"] == "capture"
    assert "artifacts" not in _run_script("capture")


def test_an_unchanged_sheet_does_not_fail_the_refresh():
    """The opposite of the capture job, deliberately. A day with no new snapshot
    is a lost day and must fail; a day whose board did not move is not."""
    script = _run_script("sheets")
    assert "sheets unchanged" in script
    assert "exit 0" in script
