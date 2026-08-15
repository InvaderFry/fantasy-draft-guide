"""The shape of the S84 archive: what it holds, what it lost, and whether it stopped.

S84's archive is the one asset here whose value expires, and until now nothing
measured it. What existed checked the bytes rather than the series:
`snapshot.verify_all()` re-hashes what is present and cannot see what is absent,
and the capture job exits non-zero on a lost day -- which reds only on a day it
runs.

Two questions, and they want opposite handling.

**What shape is the series?** It matters more since S31.3: price movement is
measured against "the capture a lookback earlier", so where the holes are decides
what the sheet prints. A hole is also permanent -- 2026-08-14 is gone, and no run
recovers it -- so it is reported as a fact. Failing on it forever is the
red-job-nobody-reads pattern this repository has already declined twice.

**Has it stopped?** A schedule that stops firing produces no runs and so no red;
GitHub disables cron workflows in a repository that goes quiet, and this one
stays awake only because the archive itself commits daily, which is circular. A
stall is live and actionable -- the next capture can still be taken -- so a stall
is what fails.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from pipeline import preseason
from pipeline.config import SNAPSHOT_DIR, adp_capture_formats
from pipeline.features.adp_history import snapshot_files

# S84's own words: "frequency: daily during July-August, weekly otherwise". The
# months are the spec's, not a choice made here -- July and August are when
# intra-summer price movement exists to be lost.
DAILY_MONTHS = (7, 8)
DAILY = 1
WEEKLY = 7

# Slack beyond the cadence before a quiet archive is called stalled. One full
# period: the daily job runs at 11:00 and 14:00 UTC, so a check at 09:00 sees
# yesterday's capture as the newest one and is right to.
SLACK_PERIODS = 1


def cadence(day: dt.date) -> int:
    """Days S84 expects between captures at this time of year."""
    return DAILY if day.month in DAILY_MONTHS else WEEKLY


def strictest_cadence(start: dt.date, end: dt.date) -> int:
    """The tightest cadence S84 asks for anywhere between two dates.

    A silence is measured against the cadence it was silent through, not against
    the month the check happens to run in. Reading it from `today` alone breaks
    at both ends of the daily window: on September 1 the tolerance triples while
    the newest capture is still an August one, so an archive that stopped on
    August 29 reads healthy until September 8 -- the day before Week 1, the last
    week the captures are for. The July 1 boundary fails the same way in reverse.
    """
    if end < start:
        start, end = end, start
    month = dt.date(start.year, start.month, 1)
    while month <= end:
        if month.month in DAILY_MONTHS:
            return DAILY
        month = (month.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return WEEKLY


def tolerance(day: dt.date, newest: dt.date | None = None) -> int:
    """How old the newest capture may be before the archive is stalled.

    With no capture named this is the cadence of `day` itself, which is what a
    caller asking "what does S84 allow today?" means.

    One consequence is deliberate: between September 1 and Week 1 a series whose
    last capture was in August keeps August's two-day deadline, even though the
    spec's letter allows weekly by then. The capture job runs daily year-round,
    `ArchiveHealth.watching` already retires the alarm at the opener, and those
    last days are the ones the draft is about to read.
    """
    return strictest_cadence(newest or day, day) * (1 + SLACK_PERIODS)


def series(season: int, root: Path | None = None) -> dict[tuple[str, int], list[dt.date]]:
    """Capture dates per (format, teams), for one season's live captures.

    Scoped to the season on purpose. The 2026-08-13 capture backfilled 2018-2025
    on the same day it took 2026, so counting every payload would report a
    healthy daily archive assembled entirely out of history -- the same
    interleaving `config.draft_season` exists to prevent on the board.
    """
    out: dict[tuple[str, int], set[dt.date]] = {}
    for date, _path, meta in snapshot_files(root if root is not None else SNAPSHOT_DIR):
        if int(meta["year"]) != season:
            continue
        out.setdefault((meta["format"], int(meta["teams"])), set()).add(date)
    return {key: sorted(dates) for key, dates in sorted(out.items())}


def gaps(dates: list[dt.date]) -> list[dt.date]:
    """Days between the first and newest capture that hold none.

    Measured at the cadence of each missing day itself, so a series that spans
    the end of August is not reported as having lost every day of September.
    """
    if len(dates) < 2:
        return []
    held = set(dates)
    missing = []
    day = dates[0] + dt.timedelta(days=1)
    while day < dates[-1]:
        if day not in held and cadence(day) == DAILY:
            missing.append(day)
        day += dt.timedelta(days=1)
    return missing


def longest_run(missing: list[dt.date]) -> int:
    """The longest unbroken stretch of missing days."""
    if not missing:
        return 0
    best = run = 1
    for prev, day in zip(missing, missing[1:], strict=False):
        run = run + 1 if day - prev == dt.timedelta(days=1) else 1
        best = max(best, run)
    return best


@dataclass
class FormatHealth:
    """One capture format's series."""

    fmt: str
    teams: int
    dates: list[dt.date] = field(default_factory=list)

    @property
    def captures(self) -> int:
        return len(self.dates)

    @property
    def first(self) -> dt.date | None:
        return self.dates[0] if self.dates else None

    @property
    def newest(self) -> dt.date | None:
        return self.dates[-1] if self.dates else None

    @property
    def missing(self) -> list[dt.date]:
        return gaps(self.dates)

    def age(self, today: dt.date) -> int | None:
        return None if self.newest is None else (today - self.newest).days

    def movement_span(self) -> int | None:
        """The span S31.3 currently has to measure a price move over."""
        if len(self.dates) < 2:
            return None
        return (self.dates[-1] - self.dates[0]).days

    def label(self) -> str:
        return f"{self.fmt}/{self.teams}team"


@dataclass
class ArchiveHealth:
    season: int
    today: dt.date
    opener: dt.date | None
    formats: list[FormatHealth]

    @property
    def watching(self) -> bool:
        """Whether a stall is still worth failing over.

        Only before Week 1. After it the draft has happened and an archive that
        stops costs nothing, while an alarm that reds from September onward is an
        alarm nobody reads in July. An unknown opener keeps watching -- the same
        rule `preseason.capture_due` applies, because not knowing the window has
        closed is not evidence that it has.
        """
        return not preseason.window_closed(self.today, self.opener)

    @property
    def stalled(self) -> list[str]:
        """Formats whose newest capture is older than S84's cadence allows."""
        if not self.watching:
            return []
        out = []
        for f in self.formats:
            age = f.age(self.today)
            if age is None:
                out.append(f"{f.label()}: no capture at all for {self.season}")
                continue
            limit = tolerance(self.today, f.newest)
            if age > limit:
                out.append(
                    f"{f.label()}: newest capture {f.newest} is {age} days old, "
                    f"and S84 allows {limit} across the days it went quiet"
                )
        return out


def health(
    season: int, *, today: dt.date | None = None, root: Path | None = None
) -> ArchiveHealth:
    """Read the archive -- no network -- and report the series per format."""
    today = today or dt.datetime.now(dt.UTC).date()
    captured = series(season, root)
    formats = [
        FormatHealth(
            fmt=spec["format"],
            teams=int(spec["teams"]),
            dates=captured.get((spec["format"], int(spec["teams"])), []),
        )
        for spec in adp_capture_formats()
    ]
    return ArchiveHealth(
        season=season,
        today=today,
        opener=preseason.season_opener(season),
        formats=formats,
    )
