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
