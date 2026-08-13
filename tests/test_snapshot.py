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
