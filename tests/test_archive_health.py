"""The S84 archive's shape, and the alarm for one that has stopped.

Two states, handled oppositely, and the difference is whether anything can still
be done about them.

A HOLE is permanent. 2026-08-14 was lost to a dispatch that filed a window before
it closed, and no run recovers it -- S84's whole premise is that a day of
intra-summer movement is not purchasable retroactively. So it is reported and
never failed on; a job that reds forever on an unfixable fact is a job nobody
reads, which is the pattern `preseason-status` and the refresh gate both avoid.

A STALL is live. The next capture can still be taken, and every day it is not is
gone. That is what fails -- and it fails outside the archive workflow, because
the failure being caught is that workflow not running: on a schedule of its own
(.github/workflows/archive-monitor.yml) and, for a human's push, here.
"""

import datetime as dt
import json

import pytest
import typer

from pipeline import archive, cli
from pipeline.config import SNAPSHOT_DIR, adp_capture_formats

AUGUST = dt.date(2026, 8, 15)
OPENER = dt.date(2026, 9, 9)


def write_archive(root, dates, *, fmt="half-ppr", teams=12, season=2026) -> None:
    """An archive holding one FFC payload per date, as the capture job leaves it."""
    for date in dates:
        day = root / date.isoformat()
        day.mkdir(parents=True, exist_ok=True)
        name = f"ffc_adp_{fmt}_{teams}team_{season}.json"
        (day / name).write_text("{}")
        (day / "manifest.json").write_text(json.dumps({"files": {name: {}}}))


# -- S84's cadence -----------------------------------------------------------


def test_the_cadence_is_the_spec_s_own():
    """S84: "daily during July-August, weekly otherwise". July and August are when
    the movement being archived exists to be lost."""
    assert archive.cadence(dt.date(2026, 7, 1)) == archive.DAILY
    assert archive.cadence(dt.date(2026, 8, 31)) == archive.DAILY
    assert archive.cadence(dt.date(2026, 9, 1)) == archive.WEEKLY
    assert archive.cadence(dt.date(2026, 6, 30)) == archive.WEEKLY


def test_the_tolerance_leaves_room_for_a_job_that_has_not_run_yet():
    """The daily capture runs at 11:00 and 14:00 UTC. A check at 09:00 sees
    yesterday's capture as the newest one and is right to."""
    assert archive.tolerance(AUGUST) == 2


def test_the_tolerance_is_read_from_the_days_the_archive_was_quiet():
    """Not from the month the check runs in. Read from `today` alone, the
    deadline triples on September 1 while the newest capture is still August's,
    and the reverse hole opens on July 1."""
    assert archive.tolerance(dt.date(2026, 9, 1), dt.date(2026, 8, 29)) == 2
    assert archive.tolerance(dt.date(2026, 7, 5), dt.date(2026, 6, 28)) == 2
    assert archive.tolerance(dt.date(2026, 9, 20), dt.date(2026, 9, 14)) == 14


# -- the shape of the series -------------------------------------------------


def test_a_missing_day_inside_the_series_is_found():
    dates = [dt.date(2026, 8, 13), dt.date(2026, 8, 15)]
    assert archive.gaps(dates) == [dt.date(2026, 8, 14)]


def test_days_before_the_first_capture_are_not_holes():
    """The archive started on 2026-08-13. Every July day before it is a day nobody
    captured, which S84 records as the archive's start, not as a gap in it."""
    dates = [dt.date(2026, 8, 13), dt.date(2026, 8, 14)]
    assert archive.gaps(dates) == []


def test_a_series_spanning_september_is_not_charged_for_september():
    """Out of season S84 asks for weekly, so the daily days do not count against
    an archive that is keeping to the cadence it is actually held to."""
    dates = [dt.date(2026, 8, 30), dt.date(2026, 9, 20)]
    assert archive.gaps(dates) == [dt.date(2026, 8, 31)]


def test_the_longest_run_is_reported():
    missing = [dt.date(2026, 8, 3), dt.date(2026, 8, 7), dt.date(2026, 8, 8), dt.date(2026, 8, 9)]
    assert archive.longest_run(missing) == 3
    assert archive.longest_run([]) == 0


def test_a_backfilled_season_is_not_counted_as_a_live_capture(tmp_path):
    """One 2026-08-13 capture backfilled 2018-2025 alongside 2026. Counted
    together they report a healthy daily archive assembled out of history."""
    write_archive(tmp_path, [dt.date(2026, 8, 13)], season=2026)
    write_archive(tmp_path, [dt.date(2026, 8, 13)], season=2025)
    assert archive.series(2026, tmp_path) == {("half-ppr", 12): [dt.date(2026, 8, 13)]}


# -- the stall ---------------------------------------------------------------


def state(tmp_path, dates, *, today, opener=OPENER, monkeypatch=None):
    for spec in adp_capture_formats():
        write_archive(tmp_path, dates, fmt=spec["format"], teams=int(spec["teams"]))
    monkeypatch.setattr(archive.preseason, "season_opener", lambda *a, **k: opener)
    return archive.health(2026, today=today, root=tmp_path)


def test_a_hole_is_not_a_stall(tmp_path, monkeypatch):
    """2026-08-14 is missing and the archive is healthy. Both are true."""
    health = state(
        tmp_path,
        [dt.date(2026, 8, 13), AUGUST],
        today=AUGUST,
        monkeypatch=monkeypatch,
    )
    assert health.formats[0].missing == [dt.date(2026, 8, 14)]
    assert health.stalled == []


def test_yesterday_is_not_a_stall(tmp_path, monkeypatch):
    health = state(tmp_path, [AUGUST], today=AUGUST + dt.timedelta(days=1),
                   monkeypatch=monkeypatch)
    assert health.stalled == []


def test_three_days_of_silence_in_august_is_a_stall(tmp_path, monkeypatch):
    health = state(tmp_path, [AUGUST], today=AUGUST + dt.timedelta(days=3),
                   monkeypatch=monkeypatch)
    assert len(health.stalled) == len(adp_capture_formats())
    assert "3 days old" in health.stalled[0]


def test_an_archive_that_stopped_in_august_is_a_stall_in_september(tmp_path, monkeypatch):
    """The week the alarm exists for. S84's cadence loosens on September 1, and
    with the deadline read from the calendar rather than from the series, a
    capture last taken on August 29 stayed healthy until September 8 -- one day
    before the 2026 opener, with every day of it unrecoverable."""
    stopped = dt.date(2026, 8, 29)
    for today in (dt.date(2026, 9, 1), dt.date(2026, 9, 8)):
        health = state(tmp_path, [stopped], today=today, monkeypatch=monkeypatch)
        assert health.watching
        assert health.stalled, today


def test_a_september_archive_still_capturing_is_not_a_stall(tmp_path, monkeypatch):
    """The other half: the deadline tightens because the series went quiet in
    August, not because September is being held to August's cadence."""
    health = state(tmp_path, [dt.date(2026, 9, 4)], today=dt.date(2026, 9, 5),
                   monkeypatch=monkeypatch)
    assert health.stalled == []


def test_a_format_with_no_capture_at_all_is_a_stall(tmp_path, monkeypatch):
    """The capture list is a superset of the real profiles (S84). A format that
    stopped being served is a format whose price history stops here."""
    write_archive(tmp_path, [AUGUST], fmt="half-ppr", teams=12)
    monkeypatch.setattr(archive.preseason, "season_opener", lambda *a, **k: OPENER)
    health = archive.health(2026, today=AUGUST, root=tmp_path)
    assert any("no capture at all" in s for s in health.stalled)


def test_the_alarm_stops_once_week_one_opens(tmp_path, monkeypatch):
    """The draft has happened; a quiet archive costs nothing. An alarm that reds
    from September onward is one nobody reads in July."""
    health = state(tmp_path, [AUGUST], today=dt.date(2026, 9, 20), monkeypatch=monkeypatch)
    assert not health.watching
    assert health.stalled == []


def test_an_unknown_opener_keeps_watching(tmp_path, monkeypatch):
    """Not knowing the window has closed is not evidence that it has -- the same
    rule `preseason.capture_due` applies to the bundle."""
    health = state(tmp_path, [AUGUST], today=dt.date(2026, 9, 20), opener=None,
                   monkeypatch=monkeypatch)
    assert health.watching
    assert health.stalled


# -- the off-season, and the morning after it ---------------------------------
#
# The capture programs sleep from Week 1 to mid-July, so `watching` is bounded at
# both ends now. The lower bound is the one that bites: without it the alarm
# wakes on resume day, finds no capture yet for the season that has not started
# capturing, and reds every July.


def test_the_alarm_sleeps_through_the_off_season(tmp_path, monkeypatch):
    """February has nothing to alarm about. The programs are not running, and an
    alarm for a job that is deliberately not running is noise with a red tick."""
    for spec in adp_capture_formats():
        write_archive(tmp_path, [AUGUST], fmt=spec["format"], teams=int(spec["teams"]))
    monkeypatch.setattr(archive.preseason, "season_opener", lambda *a, **k: None)
    health = archive.health(2027, today=dt.date(2027, 2, 1), root=tmp_path)
    assert not health.watching
    assert health.stalled == []


def test_resume_day_is_not_a_stall(tmp_path, monkeypatch):
    """The false alarm this bound exists to prevent.

    July 15 is the first morning the programs run again, and the season's series
    is empty because nothing has captured it yet -- which reads identically to an
    archive that stopped. Reading only the opener would red here every year, on
    the one morning the archive is working exactly as designed.
    """
    monkeypatch.setattr(archive.preseason, "season_opener", lambda *a, **k: None)
    health = archive.health(2027, today=dt.date(2027, 7, 15), root=tmp_path)
    assert health.formats and all(f.captures == 0 for f in health.formats)
    assert health.stalled == []


def test_a_window_that_opened_and_never_captured_is_still_caught(tmp_path, monkeypatch):
    """The grace period is the window's own cadence, not an amnesty. A resume
    that silently never captured is exactly the failure the alarm is for."""
    monkeypatch.setattr(archive.preseason, "season_opener", lambda *a, **k: None)
    health = archive.health(2027, today=dt.date(2027, 7, 25), root=tmp_path)
    assert health.watching
    assert any("no capture at all" in s for s in health.stalled)


def test_the_alarm_returns_once_the_season_s_captures_have_started(tmp_path, monkeypatch):
    """Sleeping through the off-season must not sleep through a real stall in
    August, which is the week the alarm exists for."""
    for spec in adp_capture_formats():
        write_archive(
            tmp_path, [dt.date(2027, 7, 20)], fmt=spec["format"],
            teams=int(spec["teams"]), season=2027,
        )
    monkeypatch.setattr(archive.preseason, "season_opener", lambda *a, **k: dt.date(2027, 9, 9))
    health = archive.health(2027, today=dt.date(2027, 8, 10), root=tmp_path)
    assert health.watching
    assert health.stalled


# -- the command -------------------------------------------------------------


def run(date: dt.date) -> int:
    try:
        cli.archive_status(date=date.isoformat())
    except typer.Exit as exc:
        return exc.exit_code
    return 0


def test_the_command_reports_a_hole_and_exits_zero(capsys):
    """Against the real committed archive."""
    if not SNAPSHOT_DIR.exists():
        pytest.skip("no archive committed")
    assert run(AUGUST) == 0
    assert "missing 1 day(s): 2026-08-14" in capsys.readouterr().out


def test_the_committed_archive_is_not_stalled():
    """The alarm itself, on every push.

    The archive's own commits carry [skip ci], so this reds on a human push
    rather than on the robot's -- which is the point: a schedule that stopped
    firing produces no runs and therefore no red anywhere else.
    """
    if not SNAPSHOT_DIR.exists():
        pytest.skip("no archive committed")
    today = dt.datetime.now(dt.UTC).date()
    health = archive.health(today.year, today=today)
    assert not health.stalled, (
        "the ADP archive has stopped -- S84's captures are the only item whose "
        f"value expires: {health.stalled}. If this is a branch that predates the "
        "last few captures, rebase on main before believing it: the archive lands "
        "on main daily and a stale branch carries a stale copy of it."
    )
