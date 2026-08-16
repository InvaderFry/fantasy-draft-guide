"""What `research snapshot` treats as a lost day (S84).

The archive job's exit code is the only thing watching the one item in the spec
whose value expires, so it has to mean something precise. On 2026-08-14 it meant
too little: a dispatch at 00:08 UTC captured FFC before it had published, the
previous day's numbers were filed under 08-14, and the 11:00 run that fetched the
real ones was rejected by the overwrite guard -- reported identically to a
harmless second run. These pin the four outcomes apart.

FFC is unreachable from the sandbox this repo is developed in (see
`pipeline/ingest/ffc_adp.py`), so the adapter is stood in for.
"""

import datetime as dt
import json

import pytest
import typer

from pipeline import cli, snapshot
from pipeline.ingest.base import Fetched

CAPTURE_DATE = dt.date(2026, 8, 14)
FILENAME = "ffc_adp_ppr_12team_2026.json"


def payload(*, adp: float, window_end: dt.date) -> bytes:
    return json.dumps(
        {
            "meta": {
                "start_date": "2026-08-06",
                "end_date": window_end.isoformat(),
                "total_drafts": 6160,
            },
            "players": [{"player_id": 1, "name": "Bijan Robinson", "adp": adp}],
        },
        sort_keys=True,
    ).encode()


def fetched(data: bytes, *, window_end: dt.date | None, filename: str = FILENAME) -> Fetched:
    return Fetched(
        filename=filename,
        data=data,
        url="http://ffc/ppr",
        source="fantasy_football_calculator",
        extra={"window_end": window_end.isoformat() if window_end else None},
    )


@pytest.fixture
def archive(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "SNAPSHOT_DIR", tmp_path)
    return tmp_path


def serve(monkeypatch, payloads: list[Fetched]) -> None:
    """Stand in for the FFC adapter with a fixed set of payloads."""

    class FakeAdapter:
        def __init__(self, year: int, formats=None) -> None:
            self.year = year

        def fetch(self) -> list[Fetched]:
            return payloads

    monkeypatch.setattr(cli.ffc_adp, "FFCAdapter", FakeAdapter)


def capture(sources: str = "ffc") -> None:
    cli.snapshot_(sources=sources, date=CAPTURE_DATE.isoformat(), season=0)


def test_a_window_that_closed_before_the_capture_date_is_not_written(
    archive, monkeypatch, capsys
):
    """The 00:08 UTC dispatch, which is what cost 2026-08-14.

    FFC was still serving the 08-13 window. Writing it would date yesterday's
    price as today's and, worse, take the slot the real capture needs.
    """
    serve(monkeypatch, [fetched(payload(adp=3.4, window_end=dt.date(2026, 8, 13)),
                                window_end=dt.date(2026, 8, 13))])
    capture()  # exits 0: nothing was lost, there is simply nothing to file yet
    assert not (archive / CAPTURE_DATE.isoformat()).exists()
    assert "not yet published" in capsys.readouterr().err


def test_the_capture_lands_once_the_window_reaches_the_date(archive, monkeypatch):
    serve(monkeypatch, [fetched(payload(adp=3.4, window_end=CAPTURE_DATE),
                                window_end=CAPTURE_DATE)])
    capture()
    assert (archive / CAPTURE_DATE.isoformat() / FILENAME).exists()


def test_a_historical_backfill_is_not_held_back_by_its_window(archive, monkeypatch):
    """A completed season's window closes at its final preseason week.

    Requiring window_end >= the capture date would make every backfill unwritable,
    so the check applies only to the season being captured live.
    """
    old = fetched(
        payload(adp=3.4, window_end=dt.date(2025, 9, 1)),
        window_end=dt.date(2025, 9, 1),
        filename="ffc_adp_ppr_12team_2025.json",
    )
    serve(monkeypatch, [old])
    cli.snapshot_(sources="ffc", date=CAPTURE_DATE.isoformat(), season=2025)
    assert (archive / CAPTURE_DATE.isoformat() / old.filename).exists()


def test_a_stale_capture_cannot_quietly_hold_the_slot(archive, monkeypatch, capsys):
    """The regression this file exists for.

    The date already holds a capture, and the source now serves something else.
    That is not a re-run: the stored snapshot describes a day it is not dated
    for, and it is standing between the archive and the real numbers.
    """
    yesterday = payload(adp=3.4, window_end=dt.date(2026, 8, 13))
    snapshot.Snapshot(CAPTURE_DATE).write(
        FILENAME, yesterday, source="ffc", extra={"window_end": "2026-08-13"}
    )

    serve(monkeypatch, [fetched(payload(adp=3.9, window_end=CAPTURE_DATE),
                                window_end=CAPTURE_DATE)])
    with pytest.raises(typer.Exit) as exc:
        capture()
    assert exc.value.exit_code == 1
    err = capsys.readouterr().err
    assert FILENAME in err
    assert "hold a capture taken before that date" in err
    # and the stored capture is left alone -- resolving it is a deliberate act
    assert (archive / CAPTURE_DATE.isoformat() / FILENAME).read_bytes() == yesterday


def test_a_second_run_of_a_day_already_in_hand_is_not_a_failure(archive, monkeypatch, capsys):
    """What the 11:00 run should have reported, and what the 14:00 pass relies on."""
    today = payload(adp=3.9, window_end=CAPTURE_DATE)
    snapshot.Snapshot(CAPTURE_DATE).write(FILENAME, today, source="ffc")
    serve(monkeypatch, [fetched(today, window_end=CAPTURE_DATE)])
    capture()  # exits 0
    assert "already captured" in capsys.readouterr().err


def test_a_payload_the_source_has_not_republished_is_not_refiled(archive, monkeypatch, capsys):
    """Belt to the window check's braces, for a source that publishes no window.

    FantasyPros projections carry no window, so byte-identity with the last
    capture is the only signal that nothing new was published.
    """
    projections = b'{"players": [{"name": "Bijan Robinson", "points": 291.4}]}'
    name = "fantasypros_projections_rb_2026.json"
    snapshot.Snapshot(dt.date(2026, 8, 13)).write(name, projections, source="fantasypros")

    serve(monkeypatch, [fetched(projections, window_end=None, filename=name)])
    capture()  # exits 0
    assert not (archive / CAPTURE_DATE.isoformat()).exists()
    assert "unchanged since 2026-08-13" in capsys.readouterr().err


def test_a_source_that_yields_nothing_at_all_still_fails(archive, monkeypatch, capsys):
    """Unchanged from before: this is the case S84's rule was written for."""
    serve(monkeypatch, [])
    with pytest.raises(typer.Exit) as exc:
        capture()
    assert exc.value.exit_code == 1
    assert "a capture day cannot be recovered" in capsys.readouterr().err


def test_an_intraday_republish_does_not_red_the_archive(archive, monkeypatch, capsys):
    """The 14:00 pass, on a normal day.

    FFC's average moves as the day's drafts land, so the second run sees bytes
    the morning capture does not match. The stored capture is correctly dated and
    S84 keeps it -- crying mis-dated here would fail the job every afternoon.
    """
    morning = payload(adp=3.9, window_end=CAPTURE_DATE)
    snapshot.Snapshot(CAPTURE_DATE).write(
        FILENAME, morning, source="ffc", extra={"window_end": CAPTURE_DATE.isoformat()}
    )
    serve(monkeypatch, [fetched(payload(adp=3.7, window_end=CAPTURE_DATE),
                                window_end=CAPTURE_DATE)])
    capture()  # exits 0
    assert (archive / CAPTURE_DATE.isoformat() / FILENAME).read_bytes() == morning
    assert "the first capture stands" in capsys.readouterr().err


# -- S38.1's second provider (`--sources projections-manual`) ----------------


def test_a_manual_capture_with_no_declared_provider_is_a_skip_not_a_failure(
    archive, monkeypatch, capsys
):
    """S38.1's capture must never cost a day of ADP.

    `snapshot` is run by the archive job, which commits the ADP captured seconds
    earlier -- so anything that exits non-zero from there is a reason a captured
    day never lands. An undeclared second provider is a state the repository is
    in by design until someone produces an export.
    """
    monkeypatch.setattr(cli.projections_csv, "configured_providers", dict)
    capture(sources="projections-manual")  # exits 0
    out = capsys.readouterr().out
    assert "no providers declared" in out
    assert "nothing new to capture" in out


def test_the_manual_path_is_reached_even_when_the_api_key_is_set(archive, monkeypatch):
    """The defect this source name exists for.

    S11's `_fetch_projections` is a FALLBACK ORDER -- FantasyPros first, manual
    CSV only on MissingKeyError. The key is a repository secret, so on the runner
    the manual adapter was unreachable and the board could only ever carry one
    provider. S38.1 needs a second opinion, not a substitute one.
    """
    export = b"player_name,position,team,rush_yds\nBijan Robinson,RB,ATL,1290\n"

    class FakeCsvAdapter:
        providers = {"other_2026": {"provider_id": "other"}}

        def fetch(self) -> list[Fetched]:
            return [fetched(export, window_end=None, filename="projections_other_2026.csv")]

    monkeypatch.setattr(cli.projections_csv, "ProjectionCsvAdapter", FakeCsvAdapter)
    # The API key being present is exactly the condition that used to hide it.
    monkeypatch.setenv("FANTASYPROS_API_KEY", "set-and-irrelevant-here")

    capture(sources="projections-manual")
    assert (archive / CAPTURE_DATE.isoformat() / "projections_other_2026.csv").exists()


def test_a_manual_export_is_not_refiled_on_a_second_run(archive, monkeypatch, capsys):
    """A one-time capture whose command is safe to re-run.

    The export is a file on disk, so re-running the capture serves identical
    bytes. `_classify`'s `unchanged` rule makes that a benign no-op rather than a
    second dated copy of one opinion, which would double the provider's weight in
    any dispersion measure taken across providers (S38.1).
    """
    export = b"player_name,position,team,rush_yds\nBijan Robinson,RB,ATL,1290\n"
    name = "projections_other_2026.csv"
    snapshot.Snapshot(dt.date(2026, 8, 13)).write(name, export, source="projection_csv")

    class FakeCsvAdapter:
        providers = {"other_2026": {"provider_id": "other"}}

        def fetch(self) -> list[Fetched]:
            return [fetched(export, window_end=None, filename=name)]

    monkeypatch.setattr(cli.projections_csv, "ProjectionCsvAdapter", FakeCsvAdapter)
    capture(sources="projections-manual")  # exits 0
    assert not (archive / CAPTURE_DATE.isoformat()).exists()
    assert "unchanged since 2026-08-13" in capsys.readouterr().err
