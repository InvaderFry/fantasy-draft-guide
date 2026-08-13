"""Fantasy Football Calculator ADP adapter (S10B, S84, S31.1).

This adapter backs the one item in the spec whose value expires. Intra-summer
ADP movement -- how a player's price moved across July and August -- is not
purchasable retroactively, so a missed day is gone permanently.

Two design rules follow from that:

1. The raw JSON is persisted **unmodified** before anything parses it. S31.1
   (does FFC expose a pick distribution or only a mean?) is then answered from
   the archive rather than from a re-fetch that may return a different shape.
2. An empty or failed response raises. A silent no-op day looks like success
   and costs a day of price movement.

Note: fantasyfootballcalculator.com is not reachable from the Claude Code
sandbox (403 at CONNECT). The capture runs on a GitHub Actions runner --
see .github/workflows/adp-archive.yml.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from pipeline.config import adp_capture_formats, source
from pipeline.ingest.base import Fetched, http_get

SOURCE_NAME = "fantasy_football_calculator"

# Field names that would carry pick-distribution information (S31.1). The
# adapter reports which of these the payload actually contains; it does not
# assume any of them exist.
DISTRIBUTION_FIELDS = (
    "stdev",
    "adp_stdev",
    "high",
    "low",
    "times_drafted",
    "n_drafts",
    "pick_p10",
    "pick_p25",
    "pick_p50",
    "pick_p75",
    "pick_p90",
)


class FFCAdapter:
    """Fetches one payload per configured (format, teams) pair."""

    source_name = SOURCE_NAME

    def __init__(self, year: int, formats: list[dict[str, Any]] | None = None) -> None:
        self.year = year
        self.formats = formats if formats is not None else adp_capture_formats()
        self.meta = source(SOURCE_NAME)

    def url_for(self, fmt: str) -> str:
        return f"{self.meta['api_base']}/{fmt}"

    def fetch(self) -> list[Fetched]:
        out: list[Fetched] = []
        for spec in self.formats:
            fmt = spec["format"]
            teams = spec["teams"]
            url = self.url_for(fmt)
            params = {"teams": teams, "year": self.year, "position": "all"}
            data = http_get(url, params=params)
            payload = json.loads(data)
            players = payload.get("players") or []
            if not players:
                raise ValueError(
                    f"FFC returned no players for {fmt}/{teams}team/{self.year}. "
                    "Treating this as a failure rather than an empty capture (S84)."
                )
            present = distribution_fields_present(payload)
            w_start, w_end, drafts = window(payload)
            out.append(
                Fetched(
                    filename=f"ffc_adp_{fmt}_{teams}team_{self.year}.json",
                    data=data,  # unmodified bytes, exactly as served
                    url=f"{url}?teams={teams}&year={self.year}&position=all",
                    source=SOURCE_NAME,
                    license=self.meta.get("license"),
                    notes=(
                        f"{len(players)} players over {w_start}..{w_end} "
                        f"({drafts} drafts). S31.1 distribution fields present: "
                        f"{sorted(present) if present else 'NONE (mean only)'}"
                    ),
                    extra={
                        "format": fmt,
                        "teams": teams,
                        "year": self.year,
                        "player_count": len(players),
                        "distribution_fields_present": sorted(present),
                        "window_start": str(w_start) if w_start else None,
                        "window_end": str(w_end) if w_end else None,
                        "total_drafts": drafts,
                        "attribution": self.meta.get("attribution"),
                    },
                )
            )
        return out


def distribution_fields_present(payload: dict[str, Any]) -> set[str]:
    """Which S31.1 distribution fields this payload actually carries."""
    players = payload.get("players") or []
    if not players:
        return set()
    keys: set[str] = set()
    for row in players[:50]:  # a field can be absent on an individual row
        keys.update(k for k, v in row.items() if v is not None)
    return {f for f in DISTRIBUTION_FIELDS if f in keys}


def window(payload: dict[str, Any]) -> tuple[dt.date | None, dt.date | None, int | None]:
    """The draft window an ADP figure summarizes (S31.3).

    FFC does not publish a daily value. It publishes a rolling average over a
    window whose length varies with how busy the format is: on 2026-08-13, PPR
    12-team covered 2026-08-06 to 08-13 (7 days, 6,160 drafts) while 2QB
    12-team covered 2026-07-14 to 08-13 (30 days, 3,577 drafts). Recency
    weighting that treated the 2QB figure as same-day fresh would be weighting
    a month-old average as if it were today's price.

    For a historical season the window is that season's final preseason week --
    2025 returns 2025-08-25 to 2025-09-01 -- which is what makes a retroactive
    fetch datable at all.
    """
    meta = payload.get("meta") or {}
    return (
        _as_date(meta.get("start_date")),
        _as_date(meta.get("end_date")),
        _as_int(meta.get("total_drafts")),
    )


def parse(
    payload_bytes: bytes,
    *,
    snapshot_date: dt.date,
    fmt: str,
    teams: int,
    year: int,
) -> list[dict[str, Any]]:
    """Parse a stored payload into adp_history rows (S13).

    Distribution columns are left null when the source does not publish them.
    Approximation happens at analysis time and must be labelled there (S31.2) --
    never silently in the table.
    """
    payload = json.loads(payload_bytes)
    window_start, window_end, total_drafts = window(payload)
    # The value became knowable when its window closed, not when we fetched it.
    # Without this a 2015 ADP pulled in 2026 would claim to be knowable in 2026,
    # and the leakage assertions would have no way to tell.
    as_of = window_end or snapshot_date
    rows: list[dict[str, Any]] = []
    for p in payload.get("players", []):
        rows.append(
            {
                "season": year,
                "snapshot_date": snapshot_date,  # the day we captured it
                "as_of": as_of,                  # the day the window closed
                "source_as_of": as_of,
                "window_start": window_start,
                "window_end": window_end,
                "total_drafts": total_drafts,
                "source": SOURCE_NAME,
                "format": fmt,
                "teams": teams,
                "source_player_id": _as_str(p.get("player_id")),
                "source_player_name": p.get("name"),
                "position": p.get("position"),
                "team": p.get("team"),
                "bye": _as_int(p.get("bye")),
                "adp": _as_float(p.get("adp")),
                "position_adp": None,  # derived downstream, not published per-row
                "sample_size_if_available": _as_int(p.get("times_drafted")),
                # S13 distribution fields -- null unless the source publishes them
                "adp_stdev": _as_float(p.get("stdev")),
                "pick_high": _as_float(p.get("high")),
                "pick_low": _as_float(p.get("low")),
                "pick_p10": None,
                "pick_p25": None,
                "pick_p50": None,
                "pick_p75": None,
                "pick_p90": None,
                "n_drafts": _as_int(p.get("times_drafted")),
                "value_type": "observed",
            }
        )
    return rows


def _as_float(v: Any) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _as_int(v: Any) -> int | None:
    f = _as_float(v)
    return int(f) if f is not None else None


def _as_str(v: Any) -> str | None:
    return str(v) if v not in (None, "") else None


def _as_date(v: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(v) if v else None
    except (TypeError, ValueError):
        return None
