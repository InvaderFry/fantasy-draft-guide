"""A default ingest must produce every file the builders read (S10A, S47).

`make ingest` followed by `make tables` is the documented quick-start. It only
works if the datasets downloaded by default cover the raw files the feature
builders open -- play-by-play was missing from that set, so the quick-start
failed on the first table that scanned it.
"""

import re
from pathlib import Path

from pipeline.ingest.nflverse import CORE_DATASETS, DATASETS, STATIC_DATASETS

SEASON = 2024
BUILDER_DIR = Path(__file__).resolve().parents[1] / "pipeline" / "features"

# sources.load("x.parquet") / sources.raw_path(f"x_{season}.parquet")
READ = re.compile(r'sources\.(?:load|raw_path)\(\s*f?"([^"]+\.parquet)"')


def _files_read_by_builders() -> set[str]:
    found: set[str] = set()
    for path in BUILDER_DIR.glob("*.py"):
        for name in READ.findall(path.read_text()):
            found.add(name.replace("{season}", str(SEASON)))
    return found


def _files_a_default_ingest_produces() -> set[str]:
    names = [*CORE_DATASETS, *STATIC_DATASETS]
    return {DATASETS[n].local_name(SEASON) for n in names}


def test_the_builders_read_something():
    """Guard the regex: a silent zero match would make the next test vacuous."""
    assert len(_files_read_by_builders()) >= 5


def test_a_default_ingest_covers_every_file_the_builders_read():
    missing = sorted(_files_read_by_builders() - _files_a_default_ingest_produces())
    assert not missing, (
        f"{missing} are read by pipeline/features but not downloaded by a default "
        "`research ingest`. Add the dataset to CORE_DATASETS."
    )


def test_play_by_play_is_downloaded_by_default():
    """Named explicitly: team_season is built entirely from it."""
    assert "pbp" in CORE_DATASETS
    assert DATASETS["pbp"].local_name(SEASON) == "play_by_play_2024.parquet"
