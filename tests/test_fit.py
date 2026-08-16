"""S83's one-page rule, and the gate that measures it.

The rule has broken twice and both times it broke silently: the page rendered,
every section carried content, `refresh-check` passed and the commit landed. So
these pin the three things that make the gate worth having -- it fails on a sheet
that grew, it does not report success when it could not measure, and it does not
invent a headroom number from a board it is not looking at.

One case drives a real browser over the sheets committed in this repository, and
skips when there is not one. That is the case that turns a broken board into a
red test on a human push rather than a discovery at the draft table.
"""

import pytest
import typer

from pipeline import cli
from research import fit
from research import method as method_mod
from research import sheet as sheet_mod

PROFILE = {"id": "half_ppr_12", "label": "L", "teams": 12, "real": True}

FILLED = (
    '<section><h3>TIERS <span class="spec">S19.3</span></h3>'
    "<table><tr><td>Bijan Robinson</td></tr></table></section>"
)
BLOCKED = (
    '<section><h3>TIERS <span class="spec">S19.3</span></h3>'
    '<p class="missing"><strong>BLOCKED.</strong></p><ul class="missing">'
    "<li>no projection source</li></ul></section>"
)


# -- finding something to measure with ---------------------------------------


def test_chrome_bin_wins_over_path(monkeypatch, tmp_path):
    binary = tmp_path / "my-chrome"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    monkeypatch.setenv("CHROME_BIN", str(binary))
    assert fit.find_browser() == str(binary)


def test_a_chrome_bin_that_is_not_there_is_an_error_not_a_fallback(monkeypatch, tmp_path):
    """Falling through would measure with a different browser than the one the
    workflow asked for, and report the result as if it were the one requested."""
    monkeypatch.setenv("CHROME_BIN", str(tmp_path / "nope"))
    with pytest.raises(fit.FitError, match="not an executable"):
        fit.find_browser()


def test_no_browser_is_not_a_pass(monkeypatch):
    monkeypatch.delenv("CHROME_BIN", raising=False)
    monkeypatch.setattr(fit.shutil, "which", lambda _: None)
    with pytest.raises(fit.FitError, match="an unmeasured page is not a page that fits"):
        fit.require_browser()


# -- what gets measured ------------------------------------------------------


def rendered(tmp_path, names: list[str]) -> None:
    sheets = tmp_path / "e" / "sheets"
    sheets.mkdir(parents=True)
    for name in names:
        (sheets / name).write_text(FILLED)


def test_the_chooser_is_not_measured(tmp_path):
    """index.html is a phone page: no @page rule, meant to scroll, never printed.
    Measuring it would red the gate for a page nobody prints."""
    rendered(tmp_path, ["index.html", "half_ppr_12.html"])
    assert [p.name for p in fit.printable_sheets("e", tmp_path)] == ["half_ppr_12.html"]


def test_every_sheet_is_measured_not_one(monkeypatch, tmp_path):
    """The survival block is a different height at every seat, which is exactly
    how a sweep of one slot said 13 was fine when two sheets broke at it."""
    rendered(tmp_path, [f"s{n:02d}.html" for n in range(1, 27)])
    monkeypatch.setattr(fit, "page_count", lambda p, b, **kw: 2 if p.name == "s09.html" else 1)
    counts = fit.measure("e", "browser", root=tmp_path)
    assert len(counts) == 26
    assert counts["s09.html"] == 2


def test_a_sheet_that_grew_to_two_pages_is_the_finding(monkeypatch, tmp_path):
    rendered(tmp_path, ["a.html", "b.html"])
    monkeypatch.setattr(fit, "page_count", lambda p, b, **kw: 2 if p.name == "b.html" else 1)
    report = fit.check("e", root=tmp_path, browser="browser", with_headroom=False)
    assert report["overflowing"] == ["b.html"]


# -- headroom ----------------------------------------------------------------


def probe_at(monkeypatch, breaks_at: int) -> None:
    """A page that goes to two pages once the tier list reaches `breaks_at`."""
    monkeypatch.setattr(
        sheet_mod,
        "render",
        lambda edition, **kw: f"{FILLED}<!--{kw.get('max_tier_players') or 0}-->",
    )
    monkeypatch.setattr(
        fit,
        "page_count",
        lambda p, b, html=None: 2 if int(html.split("<!--")[1].split("-->")[0]) >= breaks_at else 1,
    )


def test_headroom_is_the_rows_left_before_it_breaks(monkeypatch):
    probe_at(monkeypatch, sheet_mod.MAX_TIER_PLAYERS + 2)
    assert fit.headroom("e", PROFILE, 7, "browser", artifacts={}) == 1


def test_headroom_zero_means_the_next_long_name_breaks_it(monkeypatch):
    """Zero is not a failure. It is the constant being exactly right, and the one
    number worth reading before anybody adds a column."""
    probe_at(monkeypatch, sheet_mod.MAX_TIER_PLAYERS + 1)
    assert fit.headroom("e", PROFILE, 7, "browser", artifacts={}) == 0


def test_headroom_stops_asking_at_the_cap(monkeypatch):
    probe_at(monkeypatch, 999)
    assert fit.headroom("e", PROFILE, 7, "browser", cap=3, artifacts={}) == 3


def test_headroom_is_unmeasured_rather_than_generous_on_a_blocked_rerender(monkeypatch):
    """The probe re-renders from the artifacts on disk. A checkout with the sheets
    but not the artifacts behind them re-renders a BLOCKED page -- which is short,
    so it fits at any depth and would report generous headroom for a board it is
    not measuring. Page counts are unaffected: those read the committed files."""
    monkeypatch.setattr(sheet_mod, "render", lambda edition, **kw: BLOCKED)
    monkeypatch.setattr(fit, "page_count", lambda p, b, html=None: 1)
    assert fit.headroom("e", PROFILE, 7, "browser", artifacts={}) is None


# -- the command -------------------------------------------------------------


def run(**kwargs) -> int:
    try:
        defaults = {"edition": "e", "headroom": False, "allow_missing_browser": False}
        cli.fit_check(**{**defaults, **kwargs})
    except typer.Exit as exc:
        return exc.exit_code
    return 0


@pytest.fixture
def edition(monkeypatch, tmp_path):
    monkeypatch.setattr(method_mod, "ARTIFACT_DIR", tmp_path)
    return tmp_path


def test_the_command_passes_a_board_that_fits(monkeypatch, edition):
    rendered(edition, ["a.html", "b.html"])
    monkeypatch.setattr(fit, "require_browser", lambda: "browser")
    monkeypatch.setattr(fit, "page_count", lambda p, b, **kw: 1)
    assert run() == 0


def test_the_command_reds_on_a_board_that_no_longer_fits(monkeypatch, edition):
    rendered(edition, ["a.html", "b.html"])
    monkeypatch.setattr(fit, "require_browser", lambda: "browser")
    monkeypatch.setattr(fit, "page_count", lambda p, b, **kw: 2)
    assert run() == 1


def test_an_edition_with_nothing_rendered_is_not_a_clean_bill_of_health(monkeypatch, edition):
    """Nothing measured is the healthiest possible state for a check that reads
    what is there. `refresh-check` owns absence; this refuses to report a pass."""
    (edition / "e" / "sheets").mkdir(parents=True)
    monkeypatch.setattr(fit, "require_browser", lambda: "browser")
    assert run() == 1


def test_the_command_reds_when_it_cannot_measure(monkeypatch, edition):
    rendered(edition, ["a.html"])
    monkeypatch.delenv("CHROME_BIN", raising=False)
    monkeypatch.setattr(fit.shutil, "which", lambda _: None)
    assert run() == 1


def test_a_laptop_without_a_browser_can_be_told_to_skip(monkeypatch, edition):
    rendered(edition, ["a.html"])
    monkeypatch.delenv("CHROME_BIN", raising=False)
    monkeypatch.setattr(fit.shutil, "which", lambda _: None)
    assert run(allow_missing_browser=True) == 0


# -- the sheets in this repository, measured for real ------------------------


def _browser_or_none() -> str | None:
    """Evaluated at collection, so it must not raise. A CHROME_BIN pointing at
    nothing is a real error for the command and must not take the suite down
    before a single test runs."""
    try:
        return fit.find_browser()
    except fit.FitError:
        return None


@pytest.mark.skipif(_browser_or_none() is None, reason="no browser to measure with")
def test_the_committed_draft_sheets_still_print_on_one_page():
    """S83's rule against the board that is actually committed right now.

    Page counts only -- no headroom probing, which triples the renders and
    belongs in the daily refresh rather than in `pytest -q`.
    """
    counts = fit.measure("2026-draft", fit.require_browser())
    assert counts, "no rendered sheets in artifacts/2026-draft"
    assert [name for name, pages in counts.items() if pages > 1] == []
