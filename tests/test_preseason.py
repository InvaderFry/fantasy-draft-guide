"""The S84 preseason bundle: what makes a day a capture day (S84, S86, S6.1).

S84's second capture program is one sentence -- "also capture, once, before Week
1" -- and the whole of it is a decision about dates. That decision is what these
pin, because it is the part that cannot be checked by looking at the output: a
bundle taken on September 12 looks exactly like a bundle taken on August 29, and
only one of them describes the roster the draft saw.

nflverse is reachable from the sandbox this repo is developed in (unlike FFC and
FantasyPros), but nothing here goes to the network: a test that depends on what
the source is serving today fails for reasons that have nothing to do with the
code.
"""

import datetime as dt
import io
import json

import polars as pl
import pytest
import typer

from pipeline import cli, preseason, snapshot
from pipeline.ingest import nflverse
from pipeline.ingest.base import Fetched, FetchError

DECISION = dt.date(2026, 8, 29)
OPENER = dt.date(2026, 9, 9)


@pytest.fixture
def archive(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "SNAPSHOT_DIR", tmp_path)
    return tmp_path


# -- the policy ---------------------------------------------------------------


def due(today: dt.date, last: dt.date | None) -> str | None:
    return preseason.capture_due(today, last_capture=last, decision=DECISION, opener=OPENER)


def test_the_decision_date_is_always_a_capture_day():
    """Even one day after a capture. S6.1 dates every 2026 feature to this day."""
    assert due(DECISION, dt.date(2026, 8, 28)) == "decision date"


def test_the_day_before_week_one_is_always_a_capture_day():
    """S84's literal instruction: capture once, before Week 1."""
    assert due(OPENER - dt.timedelta(days=1), OPENER - dt.timedelta(days=2))


def test_cadence_is_weekly_between_the_two_dates_that_matter():
    assert due(dt.date(2026, 8, 21), dt.date(2026, 8, 15)) is None      # 6 days
    assert due(dt.date(2026, 8, 22), dt.date(2026, 8, 15)) is not None  # 7


def test_an_empty_archive_captures_immediately():
    assert due(dt.date(2026, 8, 15), None) == "no bundle captured yet"


def test_nothing_is_due_once_the_season_has_started():
    """Not a skipped chore: from Week 1 the files describe a season in progress.

    Upstream has begun rewriting them, so a capture taken now archives something
    other than the preseason and would read as the preseason state forever after.
    """
    assert due(OPENER, None) is None
    assert due(OPENER + dt.timedelta(days=30), None) is None
    assert preseason.window_closed(OPENER, OPENER)
    assert not preseason.window_closed(OPENER - dt.timedelta(days=1), OPENER)


def test_an_unknown_opener_does_not_close_the_window():
    """A calendar that cannot be read is not evidence the season has started."""
    assert preseason.capture_due(
        dt.date(2026, 9, 20), last_capture=None, decision=DECISION, opener=None
    )


# -- deriving the opener ------------------------------------------------------


SCHEDULE = (
    "season,game_type,week,gameday\n"
    "2026,PRE,1,2026-08-13\n"      # preseason week 1 is not the opener
    "2026,REG,1,2026-09-13\n"
    "2026,REG,1,2026-09-09\n"      # the earliest REG week-1 kickoff is
    "2026,REG,2,2026-09-16\n"
    "2027,REG,1,2027-09-08\n"
)


def test_the_opener_comes_from_the_game_calendar(tmp_path, monkeypatch):
    from pipeline.features import sources as raw_sources

    path = tmp_path / "games.csv"
    path.write_text(SCHEDULE)
    monkeypatch.setattr(raw_sources, "SCHEDULE_FILE", path)
    assert preseason.season_opener(2026) == OPENER
    assert preseason.season_opener(2027) == dt.date(2027, 9, 8)


def test_the_opener_falls_back_to_what_a_capture_recorded(archive, monkeypatch):
    """`validate` runs offline on a machine that has never run `research ingest`.

    The date is already in the archive: every bundle capture writes it into the
    manifest, so the report does not go blank for want of a 2 MB CSV.
    """
    from pipeline.features import sources as raw_sources

    monkeypatch.setattr(raw_sources, "SCHEDULE_FILE", archive / "absent.csv")
    write_capture(archive, dt.date(2026, 8, 15), {"depth_charts_2026.parquet": b"x"})
    assert preseason.season_opener(2026) == OPENER


def test_a_season_the_calendar_does_not_carry_returns_none(tmp_path, monkeypatch):
    from pipeline.features import sources as raw_sources

    path = tmp_path / "games.csv"
    path.write_text(SCHEDULE)
    monkeypatch.setattr(raw_sources, "SCHEDULE_FILE", path)
    assert preseason.season_opener(2030) is None


# -- the adapter, on a day the source has not published everything ------------


def test_an_unpublished_asset_does_not_cost_the_ones_that_are_published(monkeypatch):
    """On 2026-08-15 nflverse served three of the four and 404'd injuries.

    Raising there would lose the depth chart, which is the file that expires,
    over a file that does not exist yet.
    """
    def fake_get(url, **kwargs):
        if "injuries" in url:
            raise FetchError(f"404 for {url}")
        return b"parquet-bytes"

    monkeypatch.setattr(nflverse, "http_get", fake_get)
    adapter = nflverse.NflverseAdapter(
        seasons=[2026], datasets=list(nflverse.PRESEASON_DATASETS)
    )
    payloads = adapter.fetch(skip_missing=True)

    assert [f.filename for f in payloads] == [
        "depth_charts_2026.parquet",
        "roster_2026.parquet",
        "roster_weekly_2026.parquet",
    ]
    assert [u.dataset for u in adapter.unavailable] == ["injuries"]
    assert "404" in adapter.unavailable[0].error

    with pytest.raises(FetchError):
        adapter.fetch()  # the default is still to raise -- nflverse-static relies on it


# -- the capture, end to end through the CLI ----------------------------------


def write_capture(root, date: dt.date, files: dict[str, bytes]) -> None:
    """A dated capture written the way `Snapshot.write` writes one."""
    snap = snapshot.Snapshot(date)
    for name, data in files.items():
        snap.write(
            name,
            data,
            source="nflverse",
            extra={"season": 2026, "week1_start": OPENER.isoformat()},
        )


def serve(monkeypatch, payloads: list[Fetched], unavailable=()) -> None:
    class FakeAdapter:
        def __init__(self, seasons, datasets=None) -> None:
            self.unavailable = list(unavailable)

        def fetch(self, *, skip_missing: bool = False) -> list[Fetched]:
            return payloads

    monkeypatch.setattr(cli.nflverse, "NflverseAdapter", FakeAdapter)
    monkeypatch.setattr(preseason, "season_opener", lambda season, **kw: OPENER)


def bundle(date: dt.date, force: bool = False) -> None:
    cli.snapshot_(
        sources="nflverse-preseason", date=date.isoformat(), season=2026, force=force
    )


def chart(name: str = "depth_charts_2026.parquet", data: bytes = b"chart") -> Fetched:
    return Fetched(filename=name, data=data, url="http://nflverse/dc", source="nflverse")


def test_a_capture_day_writes_the_bundle(archive, monkeypatch):
    serve(monkeypatch, [chart()])
    bundle(dt.date(2026, 8, 15))
    assert (archive / "2026-08-15" / "depth_charts_2026.parquet").exists()

    entry = json.loads((archive / "2026-08-15" / "manifest.json").read_text())["files"]
    assert entry["depth_charts_2026.parquet"]["capture_reason"] == "no bundle captured yet"
    assert entry["depth_charts_2026.parquet"]["week1_start"] == OPENER.isoformat()


def test_a_day_the_cadence_skips_is_not_a_failure(archive, monkeypatch, capsys):
    """The job runs daily; most days it should do nothing and exit 0.

    S84's other program treats a day with nothing written as a lost day. This one
    must not, or a daily cron reds itself six mornings in seven.
    """
    serve(monkeypatch, [chart()])
    bundle(dt.date(2026, 8, 15))
    bundle(dt.date(2026, 8, 16))  # exits 0
    assert not (archive / "2026-08-16").exists()
    assert "not due" in capsys.readouterr().out


def test_force_captures_on_a_day_the_cadence_skips(archive, monkeypatch):
    serve(monkeypatch, [chart()])
    bundle(dt.date(2026, 8, 15))
    serve(monkeypatch, [chart(data=b"chart-moved")])
    bundle(dt.date(2026, 8, 16), force=True)
    assert (archive / "2026-08-16" / "depth_charts_2026.parquet").exists()


def test_bytes_the_source_has_not_republished_are_not_filed_again(archive, monkeypatch):
    """S84's `unchanged` rule, which the bundle inherits rather than reimplements."""
    serve(monkeypatch, [chart()])
    bundle(dt.date(2026, 8, 15))
    bundle(dt.date(2026, 8, 29))  # decision date: due, but the file has not moved
    assert not (archive / "2026-08-29" / "depth_charts_2026.parquet").exists()


def test_a_second_capture_on_the_same_date_does_not_overwrite(archive, monkeypatch):
    serve(monkeypatch, [chart()])
    bundle(dt.date(2026, 8, 15))
    first = (archive / "2026-08-15" / "depth_charts_2026.parquet").read_bytes()
    serve(monkeypatch, [chart(data=b"chart-moved")])
    bundle(dt.date(2026, 8, 15), force=True)
    assert (archive / "2026-08-15" / "depth_charts_2026.parquet").read_bytes() == first


# -- what the archive holds, and whether it is any use ------------------------


def charts_parquet(dates: list[str]) -> bytes:
    buf = io.BytesIO()
    pl.DataFrame({"dt": dates, "pos_rank": [1] * len(dates)}).write_parquet(buf)
    return buf.getvalue()


PRESEASON_CHART = charts_parquet(["2026-08-10", "2026-08-27"])


def test_bundle_status_reports_the_files_and_the_gaps(archive):
    write_capture(archive, dt.date(2026, 8, 15), {"depth_charts_2026.parquet": b"x"})
    write_capture(archive, dt.date(2026, 8, 29), {"roster_2026.parquet": b"y"})
    status = preseason.bundle_status(2026)

    assert status.captures["depth_charts_2026.parquet"] == [dt.date(2026, 8, 15)]
    assert status.last_capture == dt.date(2026, 8, 29)
    assert status.has_decision_date_capture
    assert "injuries_2026.parquet" in status.missing


def test_a_chart_captured_after_the_decision_date_is_reported_as_unusable(archive):
    """The failure that looks exactly like a success.

    The file is there, it hashes, it parses -- and every chart in it postdates the
    date S86 needs, so `depth_chart_rank_preseason` stays null for the season the
    capture was taken for.
    """
    write_capture(
        archive,
        dt.date(2026, 9, 5),
        {"depth_charts_2026.parquet": charts_parquet(["2026-09-01", "2026-09-04"])},
    )
    coverage = preseason.depth_chart_coverage(2026)
    assert coverage is not None and not coverage.usable

    messages = dict(cli._preseason_report(dt.date(2026, 9, 5)))
    assert any(not ok for ok in messages.values())


def test_a_chart_published_before_the_decision_date_is_usable(archive):
    write_capture(
        archive,
        dt.date(2026, 8, 29),
        {"depth_charts_2026.parquet": charts_parquet(["2026-08-10", "2026-08-27"])},
    )
    coverage = preseason.depth_chart_coverage(2026)
    assert coverage is not None and coverage.usable
    assert coverage.latest_chart == dt.date(2026, 8, 27)


# -- the gate -----------------------------------------------------------------


def status_exit(today: dt.date) -> int:
    try:
        cli.preseason_status(date=today.isoformat())
    except typer.Exit as exc:
        return exc.exit_code
    return 0


def test_the_gate_is_quiet_before_the_decision_date(archive):
    write_capture(archive, dt.date(2026, 8, 15), {"depth_charts_2026.parquet": PRESEASON_CHART})
    assert status_exit(dt.date(2026, 8, 20)) == 0


def test_the_gate_fails_while_the_gap_is_still_fixable(archive):
    """A capture on August 30 still describes very nearly the August 29 roster."""
    write_capture(archive, dt.date(2026, 8, 15), {"depth_charts_2026.parquet": PRESEASON_CHART})
    assert status_exit(dt.date(2026, 9, 1)) == 1


def test_the_gate_stops_failing_once_nothing_can_be_done(archive):
    """A permanently red job is a job nobody reads.

    From Week 1 the preseason state is gone and no run recovers it, so it is
    recorded as a gap rather than demanded forever.
    """
    write_capture(archive, dt.date(2026, 8, 15), {"depth_charts_2026.parquet": PRESEASON_CHART})
    assert status_exit(dt.date(2026, 9, 20)) == 0


def test_a_capture_that_does_not_read_as_parquet_is_a_finding(archive):
    """The hash proves the bytes are the fetched bytes, not that they are a chart."""
    write_capture(archive, dt.date(2026, 8, 29), {"depth_charts_2026.parquet": b"not-parquet"})
    coverage = preseason.depth_chart_coverage(2026)
    assert coverage is not None and not coverage.usable and coverage.error
    assert status_exit(dt.date(2026, 8, 29)) == 1
