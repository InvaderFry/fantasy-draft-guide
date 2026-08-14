"""Command-line entry points (S47, stages 1-6).

    research snapshot      capture the sources whose value expires (S84)
    research ingest        pull nflverse history (S10A)
    research normalize-ids build the player ID crosswalk (S12)
    research build-tables  build the canonical tables (S13)
    research run-research  run the S16 method modules (S47 stage 8)
    research sheet         render the S83 draft-day sheets
    research validate      re-hash snapshots and run the data/leakage checks

Later stages -- evidence grading and the site -- are not implemented in this
chunk. See S79 Steps 4+.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path
from typing import Annotated, Any

import typer

from pipeline import config, snapshot
from pipeline.ingest import fantasypros, ffc_adp, nflverse, projections_csv

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
        str, typer.Option("--sources", help="comma list: ffc,projections,nflverse-static")
    ] = "ffc",
    date: Annotated[str, typer.Option(help="snapshot date, default today (UTC)")] = "",
    season: Annotated[int, typer.Option(help="season the capture describes")] = 0,
) -> None:
    """Capture raw sources into an immutable dated snapshot (S65, S84).

    Exits non-zero if any source yields nothing. A silent no-op day is a
    permanently lost day of price movement, not a successful run.
    """
    snap_date = dt.date.fromisoformat(date) if date else dt.datetime.now(dt.UTC).date()
    year = season or snap_date.year
    snap = snapshot.Snapshot(snap_date)
    wanted = [s.strip() for s in sources.split(",") if s.strip()]
    written = 0
    skipped: list[str] = []

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
        else:
            raise typer.BadParameter(f"unknown source '{name}'")

        for f in payloads:
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
                typer.echo(f"skip (already captured): {exc}", err=True)
                continue
            written += 1
            typer.echo(f"wrote {path} ({len(f.data):,} bytes) -- {f.notes or ''}")

    if written == 0:
        # A source that skipped for a stated reason is not the same failure. S84's
        # rule is about ADP, whose missed day is unrecoverable; a projection with
        # no key configured is fetchable tomorrow and must not red the archive job,
        # which runs `snapshot` and then commits the ADP captured seconds earlier.
        if skipped and set(skipped) == set(wanted):
            reason = ", ".join(skipped)
            typer.echo(f"snapshot {snap_date.isoformat()}: nothing to capture ({reason})")
            return
        typer.echo(
            "no payloads written. Treating as failure: a capture day cannot be recovered (S84).",
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo(f"snapshot {snap_date.isoformat()}: {written} file(s)")


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
