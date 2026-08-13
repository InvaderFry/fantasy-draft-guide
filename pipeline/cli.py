"""Command-line entry points (S47, stages 1-6).

    research snapshot      capture the sources whose value expires (S84)
    research ingest        pull nflverse history (S10A)
    research normalize-ids build the player ID crosswalk (S12)
    research build-tables  build the canonical tables (S13)
    research validate      re-hash snapshots and run the data/leakage checks

Later stages -- research modules, evidence grading, artifact export, the site --
are not implemented in this chunk. See S88 Weeks 2-3 and S79 Steps 4+.
"""

from __future__ import annotations

import datetime as dt
import sys
from typing import Annotated

import typer

from pipeline import config, snapshot
from pipeline.ingest import ffc_adp, nflverse, projections_csv

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

    for name in wanted:
        if name == "ffc":
            payloads = ffc_adp.FFCAdapter(year=year).fetch()
        elif name == "projections":
            adapter = projections_csv.ProjectionCsvAdapter()
            if not adapter.providers:
                typer.echo("projections: no providers configured in config/sources.yaml, skipping")
                continue
            payloads = adapter.fetch()
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
        typer.echo(
            "no payloads written. Treating as failure: a capture day cannot be recovered (S84).",
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo(f"snapshot {snap_date.isoformat()}: {written} file(s)")


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

    if config.real_profiles():
        typer.echo(f"league profiles: {len(config.real_profiles())} marked real")
    else:
        typer.echo(
            "league profiles: none marked `real: true` -- research entry points are blocked "
            "until the real leagues are encoded (S14)",
        )

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
