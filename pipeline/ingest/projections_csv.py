"""Manual projection import (S11 option 1B, S38.1).

The user generates no proprietary projections and the FantasyPros API is
key-gated (and blocked from the sandbox), so the fallback path is a provider
CSV export dropped into ``data/raw/projections/`` with its column mapping
declared under ``projection_providers`` in config/sources.yaml.

One row per provider per player. Providers are never averaged into a single
row: cross-provider dispersion is the only available proxy for projection
uncertainty and averaging destroys it (S38.1).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from pathlib import Path
from typing import Any

from pipeline.config import CONFIG_DIR, PROJECT_ROOT, load_yaml
from pipeline.ingest.base import Fetched

SOURCE_NAME = "projection_csv"


def configured_providers() -> dict[str, Any]:
    return load_yaml(CONFIG_DIR / "sources.yaml").get("projection_providers") or {}


class ProjectionCsvAdapter:
    """Reads configured provider exports off disk into the snapshot."""

    source_name = SOURCE_NAME

    def __init__(self, providers: dict[str, Any] | None = None) -> None:
        self.providers = providers if providers is not None else configured_providers()

    def fetch(self) -> list[Fetched]:
        out: list[Fetched] = []
        for key, spec in self.providers.items():
            path = Path(spec["file"])
            if not path.is_absolute():
                path = PROJECT_ROOT / path
            if not path.exists():
                raise FileNotFoundError(
                    f"projection provider '{key}' points at {path}, which does not exist. "
                    "Drop the export in data/raw/projections/ or remove the provider from "
                    "config/sources.yaml."
                )
            data = path.read_bytes()
            out.append(
                Fetched(
                    filename=f"projections_{key}{path.suffix}",
                    data=data,
                    url=f"file://{path}",
                    source=SOURCE_NAME,
                    license=spec.get("license", "provider_specific"),
                    notes=f"manual import, provider_id={spec.get('provider_id', key)}",
                    extra={"provider_id": spec.get("provider_id", key), "provider_key": key},
                )
            )
        return out


def parse(
    payload_bytes: bytes,
    spec: dict[str, Any],
    *,
    snapshot_date: dt.date,
    season: int,
) -> list[dict[str, Any]]:
    """Normalize one provider export into projection_snapshot rows (S13)."""
    text = payload_bytes.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    stat_map: dict[str, str] = spec.get("stat_map") or {}
    provider_id = spec.get("provider_id", "unknown")

    rows: list[dict[str, Any]] = []
    for raw in reader:
        row: dict[str, Any] = {
            "season": season,
            "snapshot_date": snapshot_date,
            "as_of": snapshot_date,
            "source": SOURCE_NAME,
            "provider_id": provider_id,
            "source_player_name": raw.get(spec.get("name_col", "player_name")),
            "team": raw.get(spec.get("team_col", "team")),
            "position": raw.get(spec.get("position_col", "position")),
            "value_type": "observed",
        }
        for src_col, dest_col in stat_map.items():
            row[dest_col] = _as_float(raw.get(src_col))
        rows.append(row)
    return rows


def _as_float(v: Any) -> float | None:
    if v in (None, "", "-"):
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None
