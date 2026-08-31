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

import re
from pathlib import Path

import pytest
import yaml

from pipeline.cli import MARKET_DEPENDENT_MODULES, research_modules

WORKFLOW = Path(__file__).resolve().parent.parent / ".github/workflows/adp-archive.yml"
LIVE_EDITION = "2026-draft"


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def _run_script(job: str) -> str:
    return "\n".join(s.get("run", "") for s in _workflow()["jobs"][job]["steps"])


def _step_names(job: str) -> list[str]:
    return [s.get("name") or s.get("uses", "") for s in _workflow()["jobs"][job]["steps"]]


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


def test_the_live_boards_artifacts_are_all_committed():
    """`sheet.write()` renders whatever is in the edition's methods directory.

    This used to assert two negations in .gitignore, because the carried-forward
    artifacts were the only ones exempt from an ignore rule over the directory.
    The whole directory is committed now -- S76's audit reads the board after the
    draft and the live edition is regenerated in place, so an artifact that lives
    only inside a runner is one the audit can never see (research/freeze.py).

    Asserted as "no rule matches the directory" rather than as "the two negations
    are present", because that is the property actually relied on: every artifact
    the refresh writes has to reach the checkout, not just the two it does not
    regenerate.
    """
    root = WORKFLOW.parent.parent.parent
    ignore = (root / ".gitignore").read_text()
    rules = [
        ln.strip()
        for ln in ignore.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    assert not [r for r in rules if f"artifacts/{LIVE_EDITION}" in r]

    methods = root / "artifacts" / LIVE_EDITION / "methods"
    for module in set(research_modules()) - set(MARKET_DEPENDENT_MODULES):
        assert (methods / f"{module}.json").exists()


def test_the_refresh_writes_the_live_edition_and_not_a_dated_one():
    """A dated edition would move the phone URL every morning.

    Every `--edition` in the job, not a count of them: the job gains steps, and a
    count is a test that fails for the wrong reason when it does.
    """
    editions = set(re.findall(r"--edition (\S+)", _run_script("sheets")))
    assert editions == {LIVE_EDITION}


def _needs(job: dict) -> list[str]:
    needs = job.get("needs") or []
    return [needs] if isinstance(needs, str) else list(needs)


def test_a_failed_refresh_cannot_cost_a_capture_day():
    """S84: an uncaptured day of ADP movement is gone permanently, and a sheet
    can always be re-rendered. So they are separate jobs, and the capture is the
    one that goes first.

    Asserted as a property rather than as a literal `needs` value: the workflow
    gains jobs -- S38.1's second provider was the first -- and a test comparing
    the whole list fails for the wrong reason every time one lands.
    """
    jobs = _workflow()["jobs"]
    assert "sheets" in jobs and "capture" in jobs
    assert "capture" in _needs(jobs["sheets"])
    # Nothing that could cost a capture day runs before the capture. The season
    # gate is the one permitted predecessor: it decides whether today is a
    # capture day at all, exits 0 either way, and touches neither a source nor
    # the board -- so there is no day for it to lose.
    assert _needs(jobs["capture"]) in ([], ["window"])
    assert "artifacts" not in _run_script("capture")


def test_the_season_gate_cannot_cost_a_capture_day():
    """The one job allowed to run before the capture, held to why it is allowed.

    It reads a date and writes a job output. If it ever grew a step that fetched
    a source, rendered a board, or could red the run, it would be a step that
    stands between the archive and a day it cannot buy back.
    """
    jobs = _workflow()["jobs"]
    if "window" not in jobs:
        return
    script = _run_script("window")
    assert "season-window" in script
    for forbidden in ("snapshot", "run-research", "research sheet", "ingest"):
        assert forbidden not in script, forbidden


def test_the_second_provider_cannot_cost_a_capture_day_either():
    """S38.1's provider is worth having and it is not worth a day of ADP.

    Three properties, and all three are load-bearing: it runs AFTER the capture
    has committed, it cannot fail the run, and it is not a step inside `capture`
    -- where an adapter raising would abort the job before the FFC payloads were
    written, which is S84's one unrecoverable failure.
    """
    jobs = _workflow()["jobs"]
    assert "sleeper" in jobs
    # After the capture, whatever else the workflow has grown in front of it.
    assert "capture" in _needs(jobs["sleeper"])
    assert "sheets" not in _needs(jobs["sleeper"])
    assert jobs["sleeper"]["continue-on-error"] is True
    assert "sleeper" not in _run_script("capture")


def test_the_refresh_sees_the_second_provider():
    """A board that holds two providers and sheets rendered before the second one
    landed would report `measurable: false` while the archive says otherwise."""
    assert "sleeper" in _needs(_workflow()["jobs"]["sheets"])


def test_an_unchanged_sheet_does_not_fail_the_refresh():
    """The opposite of the capture job, deliberately. A day with no new snapshot
    is a lost day and must fail; a day whose board did not move is not."""
    script = _run_script("sheets")
    assert "sheets unchanged" in script
    assert "exit 0" in script


def test_the_archive_comes_back_after_a_late_publish():
    """FFC publishes once a day, and not always before 11:00 UTC.

    `snapshot` now declines to file a window that closed before the capture date
    -- correctly, since that is the previous day's price -- so a single morning
    run would turn a late publish into a lost day. A second pass is what makes
    the refusal safe.
    """
    schedule = _workflow()[True]["schedule"]  # PyYAML reads bare `on:` as True
    hours = sorted(int(entry["cron"].split()[1]) for entry in schedule)
    assert len(hours) > 1, "one run a day cannot recover from a late publish"
    assert hours[0] == 11


def test_an_already_captured_day_does_not_fail_the_archive():
    """The mirror of the sheets rule, and the 2026-08-14 regression.

    A day already in hand is not a day lost. The capture step decides which of
    the two it is and exits accordingly; the commit step must not overrule it by
    failing on a clean tree.
    """
    script = _run_script("capture")
    assert "nothing new to commit" in script
    assert "::error::no snapshot files were produced" not in script


def test_the_gate_stands_between_the_render_and_the_commit():
    """The ordering IS the mechanism (S83).

    `run-research` exits 0 on a blocked module -- for a dated edition a shut gate
    is a finding -- so a morning the projection capture fails renders 26 sheets
    reading BLOCKED and commits them over the good ones with nothing going red.
    A failed step skips the commit, and that is the only thing standing between a
    rotated API key and an unusable board at a draft table.
    """
    script = _run_script("sheets")
    assert f"refresh-check --edition {LIVE_EDITION}" in script

    names = _step_names("sheets")
    render = next(i for i, n in enumerate(names) if "Refresh the draft board" in n)
    gate = next(i for i, n in enumerate(names) if "degraded" in n)
    commit = next(i for i, n in enumerate(names) if "Commit" in n)
    assert render < gate < commit


def test_the_committed_board_is_not_blocked():
    """The live sheets, as they stand in the repository right now.

    Turns "the board somebody will open at the draft is broken" into a red test on
    every push, rather than a red scheduled run at 11:00 UTC that nobody reads
    until the draft.
    """
    from research import refresh

    sheets = WORKFLOW.parent.parent.parent / "artifacts" / LIVE_EDITION / "sheets"
    pages = sorted(p for p in sheets.glob("*.html") if p.name != "index.html")
    if not pages:
        pytest.skip("no live sheets committed")
    broken = {p.name: refresh.blocked_sections(p.read_text()) for p in pages}
    assert not {k: v for k, v in broken.items() if v}
