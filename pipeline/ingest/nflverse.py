"""nflverse historical data adapter (S10A).

nflverse is the statistical backbone: player weekly stats, snap counts,
rosters, depth charts, injuries, draft picks, the players table, and
play-by-play for the team-level and red-zone aggregates S13 requires.

Datasets are pulled by direct release-asset URL. ``nflreadpy`` is a convenience
wrapper and is used when installed, but correctness does not depend on it --
these URLs are the verified path:

    https://github.com/nflverse/nflverse-data/releases/download/<tag>/<file>

Deliberately NOT ingested here:
  * participation data -- 2023+ comes from FTN Data under CC-BY-SA and is not
    needed by any Week 2 analysis;
  * routes run -- no reliable free decade-long history exists (S10A). Use
    target share, snap share and air-yard share instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pipeline.config import RAW_DIR, source
from pipeline.ingest.base import Fetched, http_get

SOURCE_NAME = "nflverse"
ASSET_BASE = "https://github.com/nflverse/nflverse-data/releases/download"
SCHEDULES_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"

# Earliest season with the modern data shape; S10A calls 2012+ a reasonable
# default research window.
FIRST_SEASON = 2012


@dataclass(frozen=True)
class Dataset:
    """One nflverse release asset family."""

    name: str
    tag: str
    filename: str          # may contain {season}
    per_season: bool
    purpose: str

    def url(self, season: int | None = None) -> str:
        filename = self.filename.format(season=season) if self.per_season else self.filename
        return f"{ASSET_BASE}/{self.tag}/{filename}"

    def local_name(self, season: int | None = None) -> str:
        return self.filename.format(season=season) if self.per_season else self.filename


DATASETS: dict[str, Dataset] = {
    "player_stats": Dataset(
        "player_stats", "player_stats", "player_stats_{season}.parquet", True,
        "weekly fantasy production, targets, carries, air yards",
    ),
    "pbp": Dataset(
        "pbp", "pbp", "play_by_play_{season}.parquet", True,
        "team_season aggregates, red-zone and goal-line opportunity",
    ),
    "snap_counts": Dataset(
        "snap_counts", "snap_counts", "snap_counts_{season}.parquet", True,
        "offensive snaps and snap share",
    ),
    "rosters": Dataset(
        "rosters", "rosters", "roster_{season}.parquet", True,
        "team, position, age, experience",
    ),
    "weekly_rosters": Dataset(
        "weekly_rosters", "weekly_rosters", "roster_weekly_{season}.parquet", True,
        "in-season roster status by week",
    ),
    "depth_charts": Dataset(
        "depth_charts", "depth_charts", "depth_charts_{season}.parquet", True,
        "preseason depth chart rank as a role signal (S86)",
    ),
    "injuries": Dataset(
        "injuries", "injuries", "injuries_{season}.parquet", True,
        "weekly injury designations; inactive_reason for S15.1",
    ),
    "draft_picks": Dataset(
        "draft_picks", "draft_picks", "draft_picks.parquet", False,
        "draft capital as a role proxy",
    ),
    "players": Dataset(
        "players", "players", "players.parquet", False,
        "player ID crosswalk and biographical data (S12)",
    ),
    "combine": Dataset(
        "combine", "combine", "combine.parquet", False,
        "combine metrics",
    ),
}

# Datasets needed before the S88 Week 2 analyses can run. pbp is the expensive
# one (~19MB/season) and is streamed and aggregated rather than retained.
CORE_DATASETS = (
    "player_stats",
    "snap_counts",
    "rosters",
    "weekly_rosters",
    "depth_charts",
    "injuries",
)
STATIC_DATASETS = ("players", "draft_picks", "combine")


class NflverseAdapter:
    """Fetches nflverse release assets for a season range."""

    source_name = SOURCE_NAME

    def __init__(self, seasons: list[int], datasets: list[str] | None = None) -> None:
        bad = [s for s in seasons if s < FIRST_SEASON]
        if bad:
            raise ValueError(
                f"seasons before {FIRST_SEASON} are outside the research window: {bad}"
            )
        self.seasons = sorted(seasons)
        names = datasets or [*CORE_DATASETS, *STATIC_DATASETS]
        unknown = [n for n in names if n not in DATASETS]
        if unknown:
            raise ValueError(f"unknown nflverse datasets: {unknown}. Known: {sorted(DATASETS)}")
        self.datasets = [DATASETS[n] for n in names]
        self.meta = source(SOURCE_NAME)

    def raw_dir(self) -> Path:
        return RAW_DIR / "nflverse"

    def targets(self) -> list[tuple[Dataset, int | None]]:
        out: list[tuple[Dataset, int | None]] = []
        for ds in self.datasets:
            if ds.per_season:
                out.extend((ds, season) for season in self.seasons)
            else:
                out.append((ds, None))
        return out

    def download(self, *, force: bool = False) -> list[Path]:
        """Fetch to data/raw/nflverse/, skipping files already present.

        Raw nflverse files are reproducible from these URLs and are gitignored;
        the snapshot manifest records their hashes so an edition can prove which
        bytes produced its numbers (S48, S65).
        """
        out_dir = self.raw_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for ds, season in self.targets():
            path = out_dir / ds.local_name(season)
            if path.exists() and not force:
                written.append(path)
                continue
            data = http_get(ds.url(season), timeout=120)
            path.write_bytes(data)
            written.append(path)
        return written

    def fetch(self) -> list[Fetched]:
        """Adapter protocol: return payloads for snapshotting.

        Used for the small static tables (players, draft_picks, combine) that
        an edition should pin. Per-season bulk files go through `download`.
        """
        out: list[Fetched] = []
        for ds, season in self.targets():
            data = http_get(ds.url(season), timeout=120)
            out.append(
                Fetched(
                    filename=ds.local_name(season),
                    data=data,
                    url=ds.url(season),
                    source=SOURCE_NAME,
                    license=self.meta.get("license"),
                    notes=ds.purpose,
                    extra={"dataset": ds.name, "season": season,
                           "attribution": self.meta.get("attribution")},
                )
            )
        return out


def fetch_schedules() -> Fetched:
    """Schedules / bye weeks from nflverse/nfldata (S85.1)."""
    meta = source("nfldata_schedules")
    data = http_get(SCHEDULES_URL, timeout=120)
    return Fetched(
        filename="games.csv",
        data=data,
        url=SCHEDULES_URL,
        source="nfldata_schedules",
        license=meta.get("license"),
        notes="schedules and bye weeks",
    )
