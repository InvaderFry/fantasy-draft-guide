"""Command-line entry points (S47, stages 1-6).

    research snapshot      capture the sources whose value expires (S84)
    research ingest        pull nflverse history (S10A)
    research normalize-ids build the player ID crosswalk (S12)
    research build-tables  build the canonical tables (S13)
    research run-research  run the S16 method modules (S47 stage 8)
    research sheet         render the S83 draft-day sheets
    research preseason-status  report the S84 preseason bundle (S84, S86)
    research refresh-check     refuse to publish a degraded draft board (S83)
    research archive-status    report the S84 archive's series; fail on a stall
    research validate      re-hash snapshots and run the data/leakage checks

Later stages -- evidence grading and the site -- are not implemented in this
chunk. See S79 Steps 4+.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Annotated, Any

import polars as pl
import typer

from pipeline import config, preseason, snapshot
from pipeline.ingest import fantasypros, ffc_adp, nflverse, projections_csv
from pipeline.ingest.base import Fetched
from pipeline.normalize import player_ids
from pipeline.normalize.player_ids import match_external

app = typer.Typer(add_completion=False, help="Fantasy draft research guide pipeline.")


def _parse_seasons(spec: str) -> list[int]:
    """Accept '2012-2025' or '2023,2024' or '2024'."""
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.update(range(int(lo), int(hi) + 1))
        elif part:
            out.add(int(part))
    return sorted(out)


# Registered explicitly below: the function name is suffixed to avoid shadowing
# the `snapshot` module it calls into.
def snapshot_(
    sources: Annotated[
        str,
        typer.Option(
            "--sources",
            help="comma list: ffc,projections,nflverse-static,nflverse-preseason",
        ),
    ] = "ffc",
    date: Annotated[str, typer.Option(help="snapshot date, default today (UTC)")] = "",
    season: Annotated[int, typer.Option(help="season the capture describes")] = 0,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="capture the S84 preseason bundle even on a day its cadence says to skip",
        ),
    ] = False,
) -> None:
    """Capture raw sources into an immutable dated snapshot (S65, S84).

    Exits non-zero when a day of price movement was actually lost. A payload the
    source has not republished is not that, and neither is a second run of a day
    already in hand -- see `_classify`.
    """
    snap_date = dt.date.fromisoformat(date) if date else dt.datetime.now(dt.UTC).date()
    year = season or snap_date.year
    snap = snapshot.Snapshot(snap_date)
    wanted = [s.strip() for s in sources.split(",") if s.strip()]
    written = 0
    skipped: list[str] = []
    held: dict[str, list[str]] = {}  # category -> filenames not written

    for name in wanted:
        if name == "ffc":
            payloads = ffc_adp.FFCAdapter(year=year).fetch()
        elif name == "projections":
            payloads = _fetch_projections(year)
            if payloads is None:
                skipped.append("projections")
                continue
        elif name == "nflverse-static":
            payloads = nflverse.NflverseAdapter(
                seasons=[year], datasets=list(nflverse.STATIC_DATASETS)
            ).fetch()
        elif name == "nflverse-preseason":
            payloads = _preseason_bundle(year, snap_date=snap_date, force=force)
            if payloads is None:
                held.setdefault("not_due", []).append("nflverse-preseason")
                continue
        else:
            raise typer.BadParameter(f"unknown source '{name}'")

        for f in payloads:
            verdict = _classify(f, snap=snap, snap_date=snap_date, year=year)
            if verdict is not None:
                category, message = verdict
                held.setdefault(category, []).append(f.filename)
                typer.echo(message, err=True)
                continue
            try:
                path = snap.write(
                    f.filename,
                    f.data,
                    source=f.source,
                    url=f.url,
                    license=f.license,
                    notes=f.notes,
                    extra=f.extra,
                )
            except snapshot.SnapshotExistsError as exc:
                # `_classify` clears every collision the manifest knows about, so
                # reaching here means a file exists on disk that no manifest
                # entry describes. `verify` reports the same thing; it is not a
                # state to write through.
                held.setdefault("unmanifested", []).append(f.filename)
                typer.echo(f"unmanifested file in the way: {exc}", err=True)
                continue
            written += 1
            typer.echo(f"wrote {path} ({len(f.data):,} bytes) -- {f.notes or ''}")

    if written:
        typer.echo(f"snapshot {snap_date.isoformat()}: {written} file(s)")
        return

    _report_nothing_written(held, skipped=skipped, wanted=wanted, snap_date=snap_date)


# Categories that account for an unwritten payload without a day being lost. The
# capture either is already in hand or does not exist yet at the source.
BENIGN_HOLDS = ("already_captured", "superseded", "unchanged", "not_yet_published", "not_due")


def _classify(
    f: Fetched, *, snap: snapshot.Snapshot, snap_date: dt.date, year: int
) -> tuple[str, str] | None:
    """Decide whether a fetched payload should be written. None means write it.

    Returns (category, message) for a payload that must not be written. The order
    matters: a source that has not published yet is serving its previous day's
    bytes, and saying so is more use than reporting them as unchanged.
    """
    window_end = _as_date((f.extra or {}).get("window_end"))
    if year == snap_date.year and window_end is not None and window_end < snap_date:
        return (
            "not_yet_published",
            f"not yet published: {f.filename} covers a window closing {window_end}, "
            f"before {snap_date}. The source has not published for this date; filing "
            "it here would date the previous day's numbers as today's (S84).",
        )

    digest = snapshot.sha256(f.data)
    recorded = snap.recorded_entry(f.filename)
    if recorded is not None:
        if recorded.get("sha256") == digest:
            return (
                "already_captured",
                f"already captured: {snap.dir / f.filename} holds these exact bytes. "
                "Nothing to do -- this date is in hand.",
            )
        stored_window = _as_date(recorded.get("window_end"))
        if stored_window is not None and stored_window < snap_date:
            return (
                "misdated",
                f"{snap.dir / f.filename} covers a window closing {stored_window}, "
                f"but it is filed under {snap_date}.",
            )
        # Differing bytes are not by themselves a defect: FFC's average moves as
        # the day's drafts land, so an afternoon pass over a morning capture sees
        # a payload the stored one is simply older than. S84 keeps the first, and
        # keeping it consistently is what makes the series evenly spaced (S31.3).
        return (
            "superseded",
            f"already captured: {snap.dir / f.filename} holds this date. The source "
            "has republished since; the first capture stands (S84).",
        )

    prior = snapshot.previous_capture(f.filename, before=snap_date)
    if prior is not None and prior[1] == digest:
        return (
            "unchanged",
            f"unchanged since {prior[0]}: {f.filename} is byte-identical to that "
            "capture. The source has not republished, so there is no new day to file.",
        )
    return None


def _report_nothing_written(
    held: dict[str, list[str]],
    *,
    skipped: list[str],
    wanted: list[str],
    snap_date: dt.date,
) -> None:
    """Exit for a run that wrote nothing. Only a lost day is a failure."""
    misdated = held.get("misdated", []) + held.get("unmanifested", [])
    if misdated:
        # The fetch is not what is wrong here. Some earlier run filed a capture
        # under a date its contents do not describe -- which is how a stale
        # snapshot silently takes the slot a real one needed.
        typer.echo(
            f"{len(misdated)} file(s) dated {snap_date.isoformat()} hold a capture "
            "taken before that date:",
            err=True,
        )
        for name in misdated:
            typer.echo(f"  {name}", err=True)
        typer.echo(
            "A snapshot whose contents predate its own date corrupts the intra-summer "
            "series, and it blocks the real capture for that date. Resolve it "
            "deliberately before re-running (S84).",
            err=True,
        )
        raise typer.Exit(code=1)

    parts = []
    in_hand = len(held.get("already_captured", [])) + len(held.get("superseded", []))
    if in_hand:
        parts.append(f"{in_hand} already captured")
    if held.get("unchanged"):
        parts.append(f"{len(held['unchanged'])} unchanged at the source")
    if held.get("not_yet_published"):
        parts.append(f"{len(held['not_yet_published'])} not yet published for this date")
    if held.get("not_due"):
        parts.append(f"{len(held['not_due'])} not due today")
    if skipped:
        parts.append(f"skipped: {', '.join(skipped)}")

    # A source that skipped for a stated reason is not the same failure. S84's
    # rule is about ADP, whose missed day is unrecoverable; a projection with
    # no key configured is fetchable tomorrow and must not red the archive job,
    # which runs `snapshot` and then commits the ADP captured seconds earlier.
    if any(held.get(c) for c in BENIGN_HOLDS) or (skipped and set(skipped) == set(wanted)):
        typer.echo(f"snapshot {snap_date.isoformat()}: nothing new to capture ({'; '.join(parts)})")
        return

    typer.echo(
        "no payloads written. Treating as failure: a capture day cannot be recovered (S84).",
        err=True,
    )
    raise typer.Exit(code=1)


def _as_date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def _preseason_bundle(
    season: int, *, snap_date: dt.date, force: bool
) -> list[Fetched] | None:
    """S84's second capture: the preseason state, taken once before Week 1.

    Returns None when today is not a capture day, which is a report and not a
    failure -- the cadence exists so this job can run daily without adding 3 MB a
    day to the archive. `pipeline.preseason` holds the policy; this function only
    fetches what the policy asked for and says what the source did not serve.
    """
    status = preseason.bundle_status(season)
    opener = preseason.season_opener(season, allow_fetch=True)
    reason = preseason.capture_due(
        snap_date, last_capture=status.last_capture, decision=status.decision, opener=opener
    )
    if reason is None and force:
        reason = "forced"
    if reason is None:
        last = status.last_capture
        closed = preseason.window_closed(snap_date, opener)
        why = (
            f"the season opened {opener} and there is no preseason left to capture"
            if closed
            else f"last capture {last}, decision date {status.decision}, "
            f"week 1 {opener or 'unknown'}"
        )
        typer.echo(f"preseason bundle: not due on {snap_date} -- {why} (S84).")
        return None

    # The static tables are pinned once (S65), not re-filed weekly: they are 4.5 MB
    # and they do not describe the preseason, they identify the players in it.
    static = [
        name
        for name in nflverse.STATIC_DATASETS
        if not status.captures.get(nflverse.DATASETS[name].local_name(None))
    ]
    datasets = [*nflverse.PRESEASON_DATASETS, *static]
    typer.echo(
        f"preseason bundle: capturing {snap_date} ({reason}) -- {', '.join(datasets)} (S84)"
    )

    adapter = nflverse.NflverseAdapter(seasons=[season], datasets=datasets)
    payloads = adapter.fetch(skip_missing=True)
    for miss in adapter.unavailable:
        # Named, never silent. In August `injuries_{season}` is genuinely not
        # published yet -- that is an answer about the source (see
        # `nflverse_preseason_injuries_published` in research/questions.yaml), and
        # it must not cost the files that are available.
        typer.echo(f"preseason bundle: {miss.dataset} not served -- {miss.error}", err=True)

    return [
        dataclasses.replace(
            f,
            extra={
                **(f.extra or {}),
                "capture_reason": reason,
                "decision_date": status.decision.isoformat(),
                "week1_start": opener.isoformat() if opener else None,
            },
        )
        for f in payloads
    ]


def _fetch_projections(season: int) -> list | None:
    """S11's fallback order: FantasyPros API -> manual CSV -> skip.

    Returns None when no projection path is configured. That is a reportable
    skip rather than an error: S11 names the manual export as the supported
    fallback, and neither path being present is a state the repository is
    currently in by design (S19.3 reports it as a blocker).
    """
    try:
        return fantasypros.FantasyProsAdapter(season=season).fetch()
    except fantasypros.MissingKeyError as exc:
        typer.echo(f"projections: {exc}")

    adapter = projections_csv.ProjectionCsvAdapter()
    if not adapter.providers:
        typer.echo(
            "projections: no manual providers in config/sources.yaml either -- skipping. "
            "S19.3 tiers stay blocked until one of the two paths is configured (S11)."
        )
        return None
    return adapter.fetch()


app.command("snapshot")(snapshot_)


@app.command()
def ingest(
    seasons: Annotated[str, typer.Option(help="e.g. 2012-2025")] = "2012-2025",
    datasets: Annotated[str, typer.Option(help="comma list, default core+static")] = "",
    force: Annotated[bool, typer.Option(help="re-download files already present")] = False,
) -> None:
    """Pull nflverse history into data/raw/nflverse/ (S10A)."""
    names = [d.strip() for d in datasets.split(",") if d.strip()] or None
    adapter = nflverse.NflverseAdapter(seasons=_parse_seasons(seasons), datasets=names)
    paths = adapter.download(force=force)
    # The game calendar dates every row the builders write (S6.1), so it is part
    # of an ingest rather than a step someone has to remember.
    paths.append(nflverse.download_schedules(force=force))
    total = sum(p.stat().st_size for p in paths)
    typer.echo(f"ingested {len(paths)} file(s), {total / 1e6:.1f} MB in {adapter.raw_dir()}")


@app.command("normalize-ids")
def normalize_ids() -> None:
    """Build the player ID crosswalk (S12)."""
    from pipeline.normalize.player_ids import build_player_ids

    path = build_player_ids()
    typer.echo(f"wrote {path}")


@app.command("build-tables")
def build_tables(
    seasons: Annotated[str, typer.Option(help="e.g. 2012-2025")] = "2012-2025",
    tables: Annotated[str, typer.Option(help="comma list, default all")] = "",
) -> None:
    """Build the canonical analytical tables (S13)."""
    from pipeline.features import build

    wanted = [t.strip() for t in tables.split(",") if t.strip()] or None
    written = build.build_all(_parse_seasons(seasons), tables=wanted)
    for name, path in written.items():
        typer.echo(f"{name}: {path}")


# Which modules `run-research` knows about, keyed by the METHOD_ID that names
# their artifacts. A function rather than a module-level dict because the imports
# are deliberately lazy -- and a function the daily refresh workflow can be tested
# against, so a renamed METHOD_ID fails a test here rather than a scheduled run at
# 11:00 UTC that nobody reads until draft day.
MARKET_DEPENDENT_MODULES = ("tiers_and_replacement_level", "survival_probability")
"""The modules whose answers move with the market, and so refresh daily.

S25 team regression and S21.1 dead-zone rates describe completed seasons: their
inputs do not change between drafts, and re-running them daily would mean pulling
the whole play-by-play archive into CI to recompute an identical number.
"""


def research_modules() -> dict[str, Any]:
    from research.foundations import survival, tiers
    from research.running_back import dead_zone
    from research.teams import team_scoring_regression

    return {
        team_scoring_regression.METHOD_ID: team_scoring_regression.run,
        dead_zone.METHOD_ID: dead_zone.run,
        # Gated by S14 and S11. A gate that is shut is a finding, not a failure:
        # these report why and the run continues.
        tiers.METHOD_ID: tiers.run,
        survival.METHOD_ID: survival.run,
    }


@app.command("run-research")
def run_research(
    modules: Annotated[str, typer.Option(help="comma list, default all runnable")] = "",
    edition: Annotated[str, typer.Option(help="artifact edition, default today")] = "",
) -> None:
    """Run the S88 Week 2 research modules and export their S16 artifacts (S47 stage 8).

    Blocked modules report why and do not stop the run: S19.3 is blocked by
    design until a real league profile and a projection source exist, and a
    blocked module is a finding rather than a failure.
    """
    from research import method as method_mod
    from research.foundations import survival, tiers

    modules_by_id = research_modules()
    gated = (tiers.BlockedError, survival.BlockedError)

    wanted = [m.strip() for m in modules.split(",") if m.strip()] or list(modules_by_id)
    unknown = [m for m in wanted if m not in modules_by_id]
    if unknown:
        raise typer.BadParameter(f"unknown module(s) {unknown}")

    edition_name = edition or method_mod.default_edition()
    failures = 0
    for name in wanted:
        try:
            outcome = modules_by_id[name]()
        except gated as exc:
            typer.echo(f"{name}: BLOCKED\n  {exc}")
            continue
        except Exception as exc:  # noqa: BLE001 - one module must not stop the rest
            failures += 1
            typer.echo(f"{name}: FAILED -- {exc}", err=True)
            continue
        # A per-profile module returns one result per league profile (S83
        # generates the sheet per profile for the same reason).
        for _results, artifact in outcome if isinstance(outcome, list) else [outcome]:
            path = artifact.write(edition_name)
            typer.echo(f"{artifact.method_id}: n={artifact.sample_size} -> {path}")

    if failures:
        raise typer.Exit(code=1)


@app.command()
def sheet(
    edition: Annotated[str, typer.Option(help="artifact edition, default today")] = "",
    slot: Annotated[
        int, typer.Option(help="render only this draft slot, e.g. after the order is drawn")
    ] = 0,
    profile: Annotated[str, typer.Option(help="render only this league profile id")] = "",
) -> None:
    """Render the S83 draft-day sheet, one per real league profile and draft slot.

    S78 makes this an acceptance criterion and S88 makes it the deliverable that
    survives if the schedule collapses. It formats the S16 artifacts and computes
    nothing, so it is only ever as complete as the research behind it -- sections
    with no artifact say so on the page.

    With the draft order undrawn every slot is rendered, plus an index.html
    chooser, so the draw costs a file open rather than a build. `--slot` is the
    draft-hour path for regenerating one seat against a fresher ADP capture, on a
    machine that happens to be available; it is not required, and the
    pre-rendered pages are the deliverable.
    """
    from research import method as method_mod
    from research import sheet as sheet_mod

    edition_name = edition or method_mod.default_edition()
    paths = sheet_mod.write(
        edition_name, slot=slot or None, profile_id=profile or None
    )
    for path in paths:
        typer.echo(f"wrote {path}")
    typer.echo(f"{len(paths)} file(s)")


def _projection_status() -> str:
    """Which of S11's paths is live, in one line.

    S19.3's blocker message says a projection source is missing; it does not say
    which of the two was expected to supply it, and answering that from a CI log
    took longer than it should have. `validate` is where somebody already looks.
    """
    from pipeline.ingest import fantasypros

    if fantasypros.api_key():
        if config.fantasypros_config().get("api_base"):
            return (
                "FANTASYPROS_API_KEY is set -- S11 option 1, the FantasyPros API "
                "adapter, is the live path"
            )
        return (
            "FANTASYPROS_API_KEY is set but config/sources.yaml fantasypros_api has no "
            "`api_base`, so no request can be made (S11)"
        )
    providers = config.projection_providers()
    if providers:
        return (
            f"no API key; {len(providers)} manual provider(s) declared under "
            f"`projection_providers` -- S11 option 1B is the live path: {sorted(providers)}"
        )
    return (
        "NO SOURCE. Neither FANTASYPROS_API_KEY nor a `projection_providers` entry in "
        "config/sources.yaml, so S19.3 tiers stay blocked (S11). Note the key is read "
        "from the environment, so an unset key here says nothing about the repository "
        "secret the archive workflow uses."
    )


@app.command("draft-record")
def draft_record(
    profile: Annotated[str, typer.Option(help="league profile id, e.g. half_ppr_12")],
    slot: Annotated[int, typer.Option(help="the seat actually drawn")],
    picks: Annotated[str, typer.Option(help="file holding the pasted draft results")],
    season: Annotated[int, typer.Option(help="season drafted, default current year")] = 0,
    date: Annotated[str, typer.Option(help="draft date, ISO, default today")] = "",
    rounds: Annotated[int, typer.Option(help="expected rounds, default inferred")] = 0,
    partial: Annotated[bool, typer.Option(help="accept a draft that really ended early")] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="parse and report; write nothing")
    ] = False,
) -> None:
    """Freeze one league's draft as an immutable record (S76).

    The paste is stored verbatim through the S84 snapshot machinery, so it is
    hashed, verified by `research validate`, and can never be quietly edited
    afterwards -- a recommendation audit whose inputs are editable audits
    nothing.

    `--slot` is the seat that was actually drawn. It is the one value nothing
    else in the repository holds: the sheets were rendered for all twelve
    because the order is drawn an hour before the draft (S31.2, S83), and once
    the draft is over there is no way back to which one was real.

    **Run `--dry-run` first.** S84 refuses a second record on the same date, so
    a paste frozen with a defect cannot be re-recorded that day -- and the
    defects that matter here are the quiet ones: a line the parser skipped, or a
    name the S12 crosswalk cannot resolve, which parses cleanly and then pairs
    with nothing when `draft-review` runs. `--dry-run` does the same parse and
    the same crosswalk match, reports both, and writes nothing.
    """
    from pipeline.ingest import draft_log

    profiles = {p["id"]: p for p in config.league_profiles()}
    if profile not in profiles:
        raise typer.BadParameter(
            f"no league profile {profile!r} in config/league_profiles.yaml (S14). "
            f"Known: {sorted(profiles)}"
        )
    league = profiles[profile]
    draft_day = dt.date.fromisoformat(date) if date else dt.date.today()
    year = season or draft_day.year

    text = Path(picks).read_text()
    try:
        body = draft_log.payload(
            text,
            profile_id=profile,
            season=year,
            teams=int(league["teams"]),
            draft_slot=slot,
            draft_date=draft_day,
            rounds=rounds or None,
            partial=partial,
        )
    except draft_log.DraftLogError as exc:
        typer.echo(f"draft log rejected: {exc}", err=True)
        raise typer.Exit(code=1) from None

    _report_draft_log(body, league, slot)
    if dry_run:
        typer.echo("dry run: nothing written. Re-run without --dry-run to freeze it.")
        return

    snap = snapshot.Snapshot(draft_day)
    try:
        written = snap.write(
            draft_log.filename(profile, year),
            (json.dumps(body, indent=2, sort_keys=True) + "\n").encode(),
            source=draft_log.SOURCE_NAME,
            license="own_data",
            notes=(
                f"{body['pick_count']} picks, {body['rounds']} rounds, "
                f"{body['teams']} teams, drafted from seat {slot}"
            ),
            extra={
                "profile_id": profile,
                "season": year,
                "draft_slot": slot,
                "partial": body["partial"],
            },
        )
    except snapshot.SnapshotExistsError as exc:
        typer.echo(f"{exc}", err=True)
        raise typer.Exit(code=1) from None

    typer.echo(f"wrote {written} -- {body['pick_count']} picks from seat {slot}")
    typer.echo(
        "next: `research build-tables --tables draft_pick` then `research draft-review`"
    )


def _report_draft_log(body: dict, league: dict, slot: int) -> None:
    """What the paste turned into, before anything is frozen.

    Three numbers decide whether the audit will work, and none of them is
    visible from a successful parse alone: how the lines were read, which picks
    this seat ends up holding, and how many names S12 could not resolve. An
    unresolved name is a quote that will pair with nothing when `draft-review`
    runs -- and an unpaired quote scores as "still available" whether or not he
    was, which reads as a well calibrated approximation rather than as a fault.
    """
    from pipeline.features import draft_pick
    from pipeline.ingest import draft_log
    from research.foundations import survival as survival_mod

    picks = draft_log.parse(
        body["raw_text"], teams=body["teams"], partial=bool(body["partial"])
    )
    shapes: dict[str, int] = {}
    for pick in picks:
        shapes[pick["parsed_as"]] = shapes.get(pick["parsed_as"], 0) + 1

    typer.echo(
        f"parsed {body['pick_count']} picks, {body['rounds']} rounds, "
        f"{body['teams']} teams"
        + (" (partial)" if body["partial"] else "")
    )
    typer.echo("  shapes: " + ", ".join(f"{k}={v}" for k, v in sorted(shapes.items())))

    held = survival_mod.held_picks(body["teams"], slot, rounds=body["rounds"])
    mine = [p["source_player_name"] for p in picks if p["slot"] == slot]
    typer.echo(f"  seat {slot} holds {held} -- {len(mine)} pick(s) recorded")

    frame = draft_pick.parse_payload(body, snapshot_date=dt.date.today())
    try:
        matched = match_external(pl.DataFrame(frame))
    except player_ids.CrosswalkError as exc:
        # Not fatal here. The parse is the half of the check that has to happen
        # before the record is frozen; the crosswalk can be built afterwards and
        # the log re-read, because the paste is stored verbatim.
        typer.echo(f"  S12 crosswalk: not checked -- {exc}")
        return
    unmatched = matched.filter(pl.col("gsis_id").is_null())
    typer.echo(
        f"  S12 crosswalk: {matched.height - unmatched.height}/{matched.height} names "
        f"resolved to an id"
    )
    for row in unmatched.head(12).iter_rows(named=True):
        typer.echo(
            f"    unmatched: {row['source_player_name']!r} "
            f"({row['position'] or '?'} {row['team'] or '?'}) at pick {row['overall_pick']}"
        )
    if unmatched.height > 12:
        typer.echo(f"    ... and {unmatched.height - 12} more")
    if unmatched.height:
        typer.echo(
            "  an unmatched name pairs with nothing in draft-review; check the "
            "spelling against the paste before freezing it (S12, S76)."
        )


@app.command("draft-review")
def draft_review(
    edition: Annotated[str, typer.Option(help="edition whose sheet was on the table")] = "",
) -> None:
    """Pair what the sheet said with what happened (S76).

    Reads the recorded draft and the survival artifact of the edition actually
    used, and writes one S16 artifact per league. Includes the check S31.1 said
    the market could not supply: P(available) as quoted, against whether he was.
    """
    from research import draft_record
    from research import method as method_mod

    edition_name = edition or method_mod.default_edition()
    try:
        outcomes = draft_record.run(edition=edition_name)
    except draft_record.BlockedError as exc:
        typer.echo(f"draft-review is blocked, not killed:\n  {exc}", err=True)
        raise typer.Exit(code=1) from None

    for results, artifact in outcomes:
        path = artifact.write(edition_name)
        typer.echo(f"{artifact.method_id}: {results['n']} held pick(s) -> {path}")


def _preseason_report(today: dt.date | None = None) -> list[tuple[str, bool]]:
    """What the S84 preseason bundle holds, and whether that is still acceptable.

    Reads the archive only -- no network -- so `make validate` works offline.

    Two boundaries, not one. Before the decision date an uncaptured bundle is work
    not yet due. Between the decision date and Week 1 it is an actionable alarm --
    a capture on August 30 describes very nearly the roster August 29 did, so the
    fix is to run it. From Week 1 it is neither: nflverse has begun rewriting these
    files for the season and no run recovers what they said in August, so it is
    reported as a permanent gap rather than as a job to redo forever.
    """
    today = today or dt.datetime.now(dt.UTC).date()
    # Same rule as `snapshot_`: the season being captured is the current year
    # unless a caller says otherwise.
    season = today.year
    try:
        status = preseason.bundle_status(season)
    except config.ConfigError as exc:
        return [(f"preseason bundle: {exc}", False)]

    held = [
        f"{name} {dates[-1]}" if dates else f"{name} NOT CAPTURED"
        for name, dates in status.captures.items()
    ]
    week1 = status.opener.isoformat() if status.opener else "unknown (no games.csv on disk)"
    out = [(f"preseason bundle ({season}): {', '.join(held)}; week 1 {week1} (S84)", True)]

    if status.has_decision_date_capture:
        out.append((f"preseason bundle: decision-date capture ({status.decision}) present", True))
    elif today <= status.decision:
        out.append(
            (
                f"preseason bundle: no decision-date capture yet -- due {status.decision}",
                True,
            )
        )
    elif preseason.window_closed(today, status.opener):
        out.append(
            (
                f"preseason bundle: the {season} decision date ({status.decision}) passed "
                f"with no capture on it, and week 1 opened {status.opener}. The preseason "
                f"state as the {season} draft saw it is gone; {season} enters next "
                "edition's research with the missing preseason context S84 exists to "
                "prevent. Recorded, not actionable.",
                True,
            )
        )
    else:
        out.append(
            (
                f"preseason bundle: NO capture on the {season} decision date "
                f"({status.decision}), and it is now {today}. S6.1 dates every {season} "
                "feature to that day. Capture now: a bundle taken this week still "
                "describes very nearly that roster, and once week 1 opens nothing does "
                "(S84).",
                False,
            )
        )

    coverage = preseason.depth_chart_coverage(season)
    if coverage is None:
        return out
    if coverage.usable:
        out.append(
            (
                f"preseason bundle: depth chart captured {coverage.captured} carries "
                f"{coverage.rows:,} rows published on or before {status.decision}, "
                f"latest {coverage.latest_chart} (S86)",
                True,
            )
        )
    elif coverage.error:
        out.append(
            (
                f"preseason bundle: the depth chart captured {coverage.captured} does "
                f"not read as parquet -- {coverage.error}. The manifest hash proves "
                "these are the bytes that were fetched, not that they are a depth "
                "chart (S84).",
                False,
            )
        )
    else:
        # Captured cleanly and worth nothing to S86 -- the same shape of defect as
        # a projection stat map that maps onto the wrong columns and still computes.
        out.append(
            (
                f"preseason bundle: the depth chart captured {coverage.captured} holds "
                f"no chart published on or before {status.decision}, so "
                "`depth_chart_rank_preseason` stays null for the season the capture "
                "was taken for (S86, S6.1).",
                False,
            )
        )
    return out


def _archive_report(today: dt.date | None = None) -> tuple[list[str], list[str]]:
    """The S84 archive's series, and whatever is stalled. (lines, problems).

    Same season rule as `snapshot_` and `_preseason_report`: the current year
    unless a caller says otherwise.
    """
    from pipeline import archive

    today = today or dt.datetime.now(dt.UTC).date()
    state = archive.health(today.year, today=today)
    lines = []
    stalled = state.stalled
    for f in state.formats:
        if not f.captures:
            lines.append(f"archive {f.label()}: NO capture for {state.season}")
            continue
        missing = f.missing
        # Named while there are few enough to name. A hole is permanent -- the
        # day is not purchasable retroactively (S84) -- so this is a fact about
        # the series, not a job to redo.
        holes = (
            ""
            if not missing
            else f", missing {len(missing)} day(s): "
            + (
                ", ".join(d.isoformat() for d in missing)
                if len(missing) <= 5
                else f"{missing[0]} .. {missing[-1]}, longest run "
                f"{archive.longest_run(missing)}"
            )
        )
        span = f.movement_span()
        lines.append(
            f"archive {f.label()}: {f.captures} capture(s) {f.first} to {f.newest}"
            f"{holes}; S31.3 span {span if span is not None else 'none yet'}"
        )
    if not state.watching:
        lines.append(
            f"archive: week 1 opened {state.opener}, so a quiet archive no longer "
            "costs a draft -- reported, not watched (S84)"
        )
    # One job captures every format, so they stall together. Six identical lines
    # in a CI log bury the one number that matters, which is how far behind.
    if len(stalled) == len(state.formats) and state.formats:
        ages = {f.age(today) for f in state.formats}
        # None means no capture at all, which already reads clearly per format.
        if len(ages) == 1 and None not in ages:
            age = ages.pop()
            newest = state.formats[0].newest
            stalled = [
                f"every format is stalled -- newest capture {newest}, "
                f"{age} days old, and S84 allows {archive.tolerance(today, newest)} "
                "across the days it went quiet"
            ]
    return lines, stalled


@app.command("archive-status")
def archive_status(
    date: Annotated[str, typer.Option(help="evaluate as of this date, default today (UTC)")] = "",
) -> None:
    """Report the S84 archive's series, and fail on one that has stopped.

    A hole and a stall are different states. A hole is permanent and is reported;
    a stall can still be fixed by the next capture, and days of intra-summer
    price movement are not purchasable retroactively, so a stall exits non-zero.

    Deliberately not run by the archive workflow: the failure this catches is
    that workflow not running at all, and a gate inside a job that never fires
    cannot fire either. It runs on its own daily schedule
    (.github/workflows/archive-monitor.yml), which opens an issue when it reds,
    and in the test suite on every push -- the schedule because the repository
    can go quiet, the test because a stalled archive should also stop a human
    pushing anything else.
    """
    today = dt.date.fromisoformat(date) if date else dt.datetime.now(dt.UTC).date()
    lines, stalled = _archive_report(today)
    for line in lines:
        typer.echo(line)
    if not stalled:
        return
    for problem in stalled:
        typer.echo(f"archive: {problem}", err=True)
    typer.echo(
        "the archive has stopped. S84's capture is the one item whose value expires -- "
        "every day not captured before the draft is gone permanently. Check the ADP "
        "archive workflow is still scheduled and still running. (On a checkout that "
        "predates the last few captures, rebase first: the archive lands on main "
        "daily and a stale branch carries a stale copy of it.)",
        err=True,
    )
    raise typer.Exit(code=1)


@app.command("refresh-check")
def refresh_check(
    edition: Annotated[str, typer.Option(help="edition just rendered, e.g. 2026-draft")] = "",
    write: Annotated[
        bool, typer.Option(help="record this run as the board to be no worse than")
    ] = True,
) -> None:
    """Refuse to publish a draft board worse than the one it replaces (S83).

    Runs between the render and the commit. A blocked module exits 0 from
    `run-research` on purpose -- for a dated edition it is a finding -- so the
    knowledge that the LIVE board must not be blocked lives here, in the one
    command allowed to red the job whose damage it prevents.

    When this fails, nothing is committed and yesterday's complete sheets stay
    where they are, already carrying the banner that says they are not today's.
    """
    from research import method as method_mod
    from research import refresh
    from research import sheet as sheet_mod

    edition_name = edition or method_mod.default_edition()
    # One root, looked up at call time and threaded through, so the whole command
    # can be driven over a temporary edition in a test.
    root = method_mod.ARTIFACT_DIR
    profiles = [p for p in config.real_profiles() if config.validate_profile(p)]
    arts = sheet_mod.load_artifacts(edition_name, root)

    problems: list[str] = []

    blocked = refresh.blocked_pages(edition_name, root)
    if blocked:
        # Aggregated: every sheet fails the same way when an artifact is missing,
        # and twenty-six identical lines in a CI log bury the one thing that
        # differs. Section, count, and a name to open.
        by_section: dict[str, list[str]] = {}
        for filename, sections in sorted(blocked.items()):
            for name in sections:
                by_section.setdefault(name, []).append(filename)
        for name, files in by_section.items():
            problems.append(
                f"{name} is BLOCKED on {len(files)} sheet(s), e.g. {files[0]}"
            )
    if not blocked:
        checked = [
            p
            for p in refresh.sheets_dir(edition_name, root).glob("*.html")
            if p.name != "index.html"
        ]
        typer.echo(f"refresh: {len(checked)} sheet(s) checked, none blocked where content is due")

    metrics = refresh.edition_metrics(arts, profiles)
    for pid, entry in metrics.items():
        typer.echo(
            f"refresh: {pid} -- {entry.get('priced')} priced of {entry.get('board_rows')} "
            f"on the board, {entry.get('survival_slots')} slot(s), "
            f"capture {entry.get('adp_snapshot_date')}"
        )

    baseline = refresh.read_state(edition_name, root)
    if baseline is None:
        typer.echo("refresh: no previous good run to compare against -- floors only")
    else:
        problems.extend(refresh.regressions(metrics, baseline))

    if problems:
        for problem in problems:
            typer.echo(f"refresh: {problem}", err=True)
        typer.echo(
            f"refusing to publish edition {edition_name}: the board this refresh produced is "
            "worse than the one it would replace. Nothing has been committed, so the sheets "
            "already published stand -- and they say on their own face that they are not "
            "today's (S83). Fix the capture and let the next refresh land.",
            err=True,
        )
        raise typer.Exit(code=1)

    if write:
        path = refresh.write_state(
            edition_name,
            metrics,
            generated=dt.datetime.now(dt.UTC).date().isoformat(),
            root=root,
        )
        typer.echo(f"refresh: recorded as the board to beat -> {path}")


@app.command("preseason-status")
def preseason_status(
    date: Annotated[str, typer.Option(help="evaluate as of this date, default today (UTC)")] = "",
) -> None:
    """Report the S84 preseason bundle, and fail while the gap is still fixable.

    Separate from `validate` on purpose: this is the one check that should red a
    job, and the job it should red is the one that captures the bundle -- not the
    ADP archive, which runs `validate` between capturing a day of price movement
    and committing it.
    """
    today = dt.date.fromisoformat(date) if date else dt.datetime.now(dt.UTC).date()
    failed = False
    for message, ok in _preseason_report(today):
        typer.echo(message, err=not ok)
        failed = failed or not ok
    if failed:
        raise typer.Exit(code=1)


@app.command()
def validate() -> None:
    """Re-hash snapshots and run schema + leakage checks."""
    failures = 0

    problems = snapshot.verify_all()
    if problems:
        failures += 1
        for date, issues in problems.items():
            for issue in issues:
                typer.echo(f"snapshot {date}: {issue}", err=True)
    else:
        typer.echo("snapshots: all manifest hashes verify")

    try:
        config.decision_dates()
        config.sources()
        config.outcomes()
        typer.echo("config: loads clean")
    except config.ConfigError as exc:
        failures += 1
        typer.echo(f"config: {exc}", err=True)

    try:
        profiles = config.require_real_profiles()
        unknown = [p["id"] for p in profiles if config.draft_slot(p) is None]
        note = f", {len(unknown)} with the draft order undrawn" if unknown else ""
        typer.echo(f"league profiles: {len(profiles)} marked real{note} (S14)")
    except config.ConfigError as exc:
        failures += 1
        typer.echo(f"league profiles: {exc}", err=True)

    typer.echo(f"projections: {_projection_status()}")

    # Reported, never failed here. `validate` runs inside the ADP archive job
    # between the capture and the commit, so anything that exits non-zero from it
    # is a reason a captured day of price movement does not get committed (S84).
    # `research preseason-status` is the gate, and it runs in its own workflow.
    for message, _ok in _preseason_report():
        typer.echo(message)

    # Report-only here for the same reason as the bundle above: `validate` runs
    # inside the capture job between capturing a day of price movement and
    # committing it. `research archive-status` is where a stall fails.
    archive_lines, archive_stalled = _archive_report()
    for message in archive_lines:
        typer.echo(message)
    for problem in archive_stalled:
        typer.echo(f"archive: {problem} -- see `research archive-status`")

    from pipeline.features import checks

    for message, ok in checks.run_all():
        typer.echo(message, err=not ok)
        failures += 0 if ok else 1

    if failures:
        raise typer.Exit(code=1)


def main() -> None:  # pragma: no cover - console entry
    sys.exit(app())


if __name__ == "__main__":  # pragma: no cover
    app()
