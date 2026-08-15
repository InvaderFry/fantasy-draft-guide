"""The S84 preseason bundle: when it is due, and what the archive holds (S84, S86).

S84 has two capture programs, not one. The daily program archives the prices
whose movement expires. The second is a single instruction:

    Also capture, once, before Week 1:
      preseason depth charts, preseason injury designations,
      final preseason ADP for every format, projection snapshots

ADP and projections are already captured daily, so their final preseason value
is the last daily run before Week 1. The nflverse half is captured nowhere: those
files live in `data/raw/` (gitignored, re-downloadable) and upstream rewrites
each of them all season. `player_season._preseason_depth_chart` is the reason
that matters -- it dates a chart by its own `dt` and takes the last one at or
before the decision date, which 2012-2024 cannot answer at all because the
legacy format's first chart is regular-season week 1. 2026 can answer it now.

This module holds the policy, deliberately separated from the fetching: which
days are capture days is a decision, and a decision belongs somewhere it can be
tested without a network.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from pipeline import snapshot
from pipeline.config import decision_date
from pipeline.ingest import nflverse

# Between the mandatory captures. Daily would cost ~3 MB a day in a repository
# whose draft sheets are meant to open from a phone; weekly plus the two dates
# that must exist is roughly four captures for a preseason.
CADENCE_DAYS = 7


def bundle_filenames(season: int, *, include_static: bool = True) -> list[str]:
    """The archive filenames one bundle capture writes, in capture order."""
    names = [
        nflverse.DATASETS[n].local_name(season) for n in nflverse.PRESEASON_DATASETS
    ]
    if include_static:
        names += [nflverse.DATASETS[n].local_name(None) for n in nflverse.STATIC_DATASETS]
    return names


def capture_due(
    today: dt.date,
    *,
    last_capture: dt.date | None,
    decision: dt.date,
    opener: dt.date | None,
) -> str | None:
    """Why today is a capture day, or None if it is not.

    Order matters. The decision date is the one capture that must exist -- S6.1
    pins every 2026 feature to it, so a bundle taken three days later describes a
    roster the draft could not have seen. The day before Week 1 is S84's literal
    instruction. Everything between them is cadence, and cadence is the part that
    can be missed without losing anything that was not also captured last week.
    """
    if opener is not None and today >= opener:
        return None
    if today == decision:
        return "decision date"
    if opener is not None and today == opener - dt.timedelta(days=1):
        return "last day before week 1"
    if last_capture is None:
        return "no bundle captured yet"
    if today - last_capture >= dt.timedelta(days=CADENCE_DAYS):
        return f"{(today - last_capture).days} days since the last capture"
    return None


def window_closed(today: dt.date, opener: dt.date | None) -> bool:
    """True once the season has started and there is no preseason left to capture."""
    return opener is not None and today >= opener


def season_opener(season: int, *, allow_fetch: bool = False) -> dt.date | None:
    """First regular-season kickoff, from the nfldata game calendar (S85.1).

    Derived rather than typed in, for the same reason every other date in this
    repository is: a hard-coded 2026-09-09 is correct for exactly one season and
    silently wrong afterwards.

    Three sources, in order: the raw calendar if a build has been run here, the
    archive's own manifests if a capture has recorded the date, then the network
    if the caller allows it. The middle one is what keeps `validate` both offline
    and accurate on a machine that has never run `research ingest`. Returns None
    when none of the three can answer, which is reported rather than raised.
    """
    from pipeline.features import sources as raw_sources

    path = raw_sources.SCHEDULE_FILE
    if path.exists():
        opener = _opener_from_schedule(path.read_bytes(), season)
        if opener is not None:
            return opener
    recorded = _opener_from_archive(season)
    if recorded is not None:
        return recorded
    if allow_fetch:
        return _opener_from_schedule(nflverse.fetch_schedules().data, season)
    return None


def _opener_from_archive(season: int) -> dt.date | None:
    """The week-1 date a previous bundle capture wrote into its manifest."""
    for _date, manifest in reversed(_manifests()):
        for entry in manifest.get("files", {}).values():
            if entry.get("season") == season and entry.get("week1_start"):
                try:
                    return dt.date.fromisoformat(entry["week1_start"])
                except ValueError:
                    continue
    return None


def _opener_from_schedule(data: bytes, season: int) -> dt.date | None:
    games = pl.read_csv(
        data,
        columns=["season", "game_type", "week", "gameday"],
        schema_overrides={"gameday": pl.String},
    ).filter(
        (pl.col("season") == season) & (pl.col("game_type") == "REG") & (pl.col("week") == 1)
    )
    if games.height == 0:
        return None
    return games.select(pl.col("gameday").str.to_date(strict=False).min()).item()


@dataclass
class BundleStatus:
    """What the archive holds for one season's preseason bundle."""

    season: int
    decision: dt.date
    opener: dt.date | None
    captures: dict[str, list[dt.date]] = field(default_factory=dict)

    @property
    def last_capture(self) -> dt.date | None:
        dates = [d for ds in self.captures.values() for d in ds]
        return max(dates) if dates else None

    @property
    def missing(self) -> list[str]:
        return [name for name, dates in self.captures.items() if not dates]

    @property
    def has_decision_date_capture(self) -> bool:
        """A capture on the decision date itself, for any bundle file.

        Not "a capture exists": the whole point of the date is that S6.1 dates
        2026's features to it.
        """
        return any(self.decision in dates for dates in self.captures.values())


def bundle_status(season: int, *, include_static: bool = True) -> BundleStatus:
    """Read the archive -- no network -- and report what the bundle holds."""
    status = BundleStatus(
        season=season,
        decision=decision_date(season),
        opener=season_opener(season),
        captures={name: [] for name in bundle_filenames(season, include_static=include_static)},
    )
    for date, manifest in _manifests():
        files = manifest.get("files", {})
        for name in status.captures:
            if name in files:
                status.captures[name].append(date)
    for dates in status.captures.values():
        dates.sort()
    return status


def _manifests() -> list[tuple[dt.date, dict]]:
    """Every dated manifest in the archive, oldest first."""
    root: Path = snapshot.SNAPSHOT_DIR
    out: list[tuple[dt.date, dict]] = []
    if not root.exists():
        return out
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        try:
            date = dt.date.fromisoformat(d.name)
        except ValueError:
            continue
        path = d / "manifest.json"
        if not path.exists():
            continue
        with path.open() as fh:
            out.append((date, json.load(fh)))
    return out


@dataclass(frozen=True)
class ChartCoverage:
    """Whether a captured depth chart is any use to S86 at the decision date."""

    captured: dt.date
    rows: int
    latest_chart: dt.date | None
    error: str | None = None

    @property
    def usable(self) -> bool:
        return self.error is None and self.latest_chart is not None


def depth_chart_coverage(season: int) -> ChartCoverage | None:
    """The latest chart at or before the decision date, inside the captured file.

    A capture that succeeded and holds only post-decision charts is worth nothing
    to S86 and looks exactly like a success -- the same failure mode as a
    projection stat map that maps cleanly onto the wrong columns. So the check is
    on the contents, not on the file existing.
    """
    filename = nflverse.DATASETS["depth_charts"].local_name(season)
    dates = bundle_status(season).captures.get(filename) or []
    if not dates:
        return None
    captured = max(dates)
    path = snapshot.snapshot_dir(captured) / filename
    try:
        charts = pl.read_parquet(path)
    except Exception as exc:  # noqa: BLE001 - any unreadable capture is the same finding
        # The manifest hash proves the bytes are the bytes that were fetched. It
        # does not prove they are a depth chart, and a capture nothing can open is
        # a capture of nothing.
        return ChartCoverage(captured=captured, rows=0, latest_chart=None, error=str(exc))
    if "dt" not in charts.columns or charts.height == 0:
        # Legacy format: keyed by week, earliest chart is regular-season week 1,
        # so nothing in it is knowable at an August decision date (S6.1).
        return ChartCoverage(captured=captured, rows=charts.height, latest_chart=None)
    knowable = charts.with_columns(
        pl.col("dt").cast(pl.String).str.slice(0, 10).str.to_date(strict=False).alias("_d")
    ).filter(pl.col("_d") <= pl.lit(decision_date(season)))
    if knowable.height == 0:
        return ChartCoverage(captured=captured, rows=charts.height, latest_chart=None)
    return ChartCoverage(
        captured=captured,
        rows=knowable.height,
        latest_chart=knowable.select(pl.col("_d").max()).item(),
    )
