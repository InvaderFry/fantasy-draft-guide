"""Snapshot immutability (S65, S84)."""

import datetime as dt

import pytest

from pipeline import snapshot


@pytest.fixture
def snap(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "SNAPSHOT_DIR", tmp_path)
    return snapshot.Snapshot(dt.date(2026, 8, 13))


def test_write_records_hash_and_metadata(snap):
    snap.write("a.json", b'{"x": 1}', source="ffc", url="http://x", license="CC")
    entry = snap.read_manifest()["files"]["a.json"]
    assert entry["sha256"] == snapshot.sha256(b'{"x": 1}')
    assert entry["source"] == "ffc"
    assert entry["bytes"] == 8
    assert entry["retrieved_at"]


def test_overwriting_a_capture_raises(snap):
    """S84: never overwrite a prior capture -- the day cannot be re-recorded."""
    snap.write("a.json", b'{"x": 1}', source="ffc")
    with pytest.raises(snapshot.SnapshotExistsError):
        snap.write("a.json", b'{"x": 2}', source="ffc")
    assert (snap.dir / "a.json").read_bytes() == b'{"x": 1}'


def test_empty_payload_is_a_failure_not_a_capture(snap):
    with pytest.raises(ValueError, match="empty"):
        snap.write("a.json", b"", source="ffc")


def test_verify_detects_tampering(snap):
    snap.write("a.json", b'{"x": 1}', source="ffc")
    assert snap.verify() == []
    (snap.dir / "a.json").write_bytes(b'{"x": 999}')
    problems = snap.verify()
    assert len(problems) == 1 and "sha256 mismatch" in problems[0]


def test_verify_detects_unmanifested_files(snap):
    snap.write("a.json", b'{"x": 1}', source="ffc")
    (snap.dir / "stray.json").write_bytes(b"{}")
    assert any("absent from the manifest" in p for p in snap.verify())


def test_previous_capture_finds_the_most_recent_earlier_date(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "SNAPSHOT_DIR", tmp_path)
    for day, payload in ((11, b'{"x": 1}'), (12, b'{"x": 2}')):
        snapshot.Snapshot(dt.date(2026, 8, day)).write("a.json", payload, source="ffc")
    found = snapshot.previous_capture("a.json", before=dt.date(2026, 8, 13))
    assert found == (dt.date(2026, 8, 12), snapshot.sha256(b'{"x": 2}'))


def test_previous_capture_ignores_the_date_it_is_asked_about(tmp_path, monkeypatch):
    """`before` is strict: a capture cannot be its own precedent."""
    monkeypatch.setattr(snapshot, "SNAPSHOT_DIR", tmp_path)
    snapshot.Snapshot(dt.date(2026, 8, 13)).write("a.json", b'{"x": 1}', source="ffc")
    assert snapshot.previous_capture("a.json", before=dt.date(2026, 8, 13)) is None


def test_previous_capture_is_per_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "SNAPSHOT_DIR", tmp_path)
    snapshot.Snapshot(dt.date(2026, 8, 12)).write("a.json", b'{"x": 1}', source="ffc")
    assert snapshot.previous_capture("b.json", before=dt.date(2026, 8, 13)) is None


def test_recorded_entry_reports_what_this_date_already_holds(snap):
    assert snap.recorded_entry("a.json") is None
    snap.write("a.json", b'{"x": 1}', source="ffc", extra={"window_end": "2026-08-13"})
    entry = snap.recorded_entry("a.json")
    assert entry["sha256"] == snapshot.sha256(b'{"x": 1}')
    # the extras travel with it: telling a stale capture from a superseded one
    # depends on the window the stored payload covered
    assert entry["window_end"] == "2026-08-13"
