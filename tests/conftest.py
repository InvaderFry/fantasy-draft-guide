import datetime as dt

import polars as pl
import pytest


@pytest.fixture
def feature_frame() -> pl.DataFrame:
    """A minimal well-formed feature frame."""
    return pl.DataFrame(
        {
            "season": [2023, 2023],
            "player_id": ["00-0000001", "00-0000002"],
            "targets": [90, 40],
            "as_of": [dt.date(2024, 1, 7), dt.date(2024, 1, 7)],
            "source_as_of": [dt.date(2024, 1, 7), dt.date(2024, 1, 7)],
            "value_type": ["derived", "derived"],
        }
    )


@pytest.fixture
def synthetic_season(tmp_path, monkeypatch):
    """A complete raw directory for one small season, with no network.

    The table builders read through `pipeline.features.sources`, so pointing
    that module at a temporary directory is enough to run the real
    `build.build_all` end to end. Everything the integration tests in
    test_tables.py skip when data/processed is empty is therefore exercised on
    every CI run.
    """
    from pipeline.features import player_season, player_week, sources
    from tests import fixtures

    raw = fixtures.write_raw(tmp_path / "nflverse")
    monkeypatch.setattr(sources, "NFLVERSE_DIR", raw)
    monkeypatch.setattr(sources, "SCHEDULE_FILE", raw / "games.csv")
    sources.schedule.cache_clear()
    sources.week_end_dates.cache_clear()

    crosswalk = fixtures.crosswalk()
    monkeypatch.setattr(player_week, "load_player_ids", lambda *a, **k: crosswalk)
    monkeypatch.setattr(player_season, "load_player_ids", lambda *a, **k: crosswalk)

    yield fixtures

    sources.schedule.cache_clear()
    sources.week_end_dates.cache_clear()
