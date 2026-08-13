# Fantasy Draft Research Guide — data foundation

Implementation of the data layer described in
[`static_fantasy_football_research_guide_plan_r2.md`](static_fantasy_football_research_guide_plan_r2.md).
Section references throughout the code (`S13`, `S6.1`, ...) point at that spec.

## What this chunk is

The spec routes any build starting within three weeks of a draft to its §88
compressed timeline: **data only, no site, no research**. This repository is
§88 Week 1, laid out inside the §9 architecture so none of it is rewritten in
the offseason.

Built here:

| Piece | Spec | Notes |
|---|---|---|
| Config gates | §14, §6.1, §46, §15, §3.1, §68 | league profiles, decision dates, sources, outcomes, evidence rules, question registry |
| ADP archival | §84 | daily GitHub Actions capture — the only item whose value expires |
| nflverse ingest | §10A | 2012–2025 weekly stats, snaps, rosters, depth charts, injuries, play-by-play, schedules |
| ID normalization | §12 | `gsis_id` canonical, crosswalk + labelled name matching |
| Canonical tables | §13 | `player_week`, `player_season` (+ outcomes), `team_season`, `adp_history` |
| Snapshots | §65 | dated, hashed, immutable |
| Tests | §51 | data, leakage, grading-config and snapshot checks |

**Not** built here: research modules, evidence grading, the draft simulator,
the draft-day sheet, and any publication layer. Those are §88 Weeks 2–3 and
§79 Steps 4+.

## Quick start

```bash
make setup                      # uv sync
make ingest SEASONS=2023-2024   # nflverse raw data (start small)
make ids                        # player_ids.parquet
make tables SEASONS=2023-2024   # canonical tables
make validate                   # hashes, schema, leakage, data checks
make test
```

`make ingest` pulls play-by-play too — it is the largest piece (~19 MB per
season) and both `team_season` and `player_week` are built from it, so the
quick-start needs it on disk. The builders scan it lazily and reduce it
immediately, so the memory cost is small even though the disk cost is not.

The full research window is `SEASONS=2012-2025` (~300 MB of raw play-by-play).
It produces:

| Table | Rows (2012–2025) |
|---|---|
| `player_week` | 133,624 — 93,114 active, 7,985 ir, 15,186 inactive, 17,339 dnp |
| `player_season` / `player_season_outcomes` | 9,934 each |
| `team_season` | 448 — the population §25 runs on |
| `adp_history` | grows daily from the archival job |

### One thing to check before pooling seasons

`games_missed_injury` is only as good as the roster file behind it. nflverse
carries reserve-list transaction codes (`R01` Reserve/Injured, `R04` PUP and so
on) from **2020**; before that the file records that a player was on a reserve
list but not why, and 2012–2015 do not mark game-day inactives at all. The
share of missed games classified injury-related therefore steps from ~17% in
2017 to ~60% in 2021, which is a change in the source and not in the sport:

| Seasons | Injury-classified share of `games_missed` |
|---|---|
| 2012–2019 | 14–25% — reserve reason unavailable upstream |
| 2020 | 38% — codes exist, but Reserve/COVID-19 and Reserve/Opt-out are absences that are not injuries |
| 2021–2025 | ~60% |

`make validate` prints this breakdown every run. An availability model (§15.1)
fitted across the whole window would read the 2020 step as a finding; fit it on
2021+ or carry a coverage term.

Another upstream wrinkle: nflverse moved weekly player stats to a new
release (`stats_player`) with renamed columns, and **only the new location
carries 2025**. The adapter tries locations in order and the builder normalizes
the renamed columns, so a 14-season build spans both shapes. Depth charts
changed format too — the 2025+ files carry real publication timestamps, which
is why preseason depth-chart rank is available for 2025 and null for earlier
seasons, where the first chart is dated week 1 and postdates the draft.

## Three rules the code enforces rather than documents

**The population is the roster, not the stat sheet (§13, §15.1).**
`player_week` starts from weekly rosters and left-joins production onto them,
so a week on IR is a row with `active_status = ir` rather than a missing row.
Building it the other way round makes `active_status` a constant — every row
describes a player who played — and leaves `games_missed_injury` counting only
the injury-report designations that players on reserve stop receiving. Season
availability is then a count of `player_week`'s own labels rather than a second,
independent pass over the roster and injury files.

**As-of discipline (§6.1).** Every feature row carries `as_of`, `source_as_of`
and `value_type`. `assert_knowable(frame, season)` raises `LeakageError` if any
row postdates that season's decision date, so a 2023 season aggregate is a
legal 2024 feature and an illegal 2023 one. Outcome columns live in their own
tables flagged `is_outcome` and are never joined into a feature frame. This
could not be retrofitted later, which is why it is present in the first table.

**Snapshot immutability (§84).** `pipeline/snapshot.py` refuses to overwrite an
existing capture and refuses to write an empty payload. A day of intra-summer
ADP movement that is not captured is gone permanently, so a silent no-op has to
look like a failure.

## Before running research

`config/league_profiles.yaml` ships two **placeholder** profiles, both
`real: false`. Every downstream conclusion is conditional on scoring, team
count and draft slot, so `require_real_profiles()` blocks research entry points
until the leagues actually being drafted are encoded. Fill in the `TODO`
values — teams, scoring, starters, draft date, draft slot — and set
`real: true`.

## Data sources and attribution

* **nflverse** (`nflverse-data`), CC-BY-4.0 — statistics, rosters, depth
  charts, injuries, play-by-play, ID crosswalk. Component datasets carry their
  own terms; participation data (2023+, FTN Data, CC-BY-SA) is deliberately not
  ingested.
* **nflverse/nfldata** — schedules and bye weeks.
* **Fantasy Football Calculator** — ADP, free for personal and commercial use
  with attribution requested.

Full registry with purposes and licenses: `config/sources.yaml` (§46). This is
a technical registry, not legal advice.

### Open source question (§31.1)

Whether FFC publishes an underlying pick distribution or only a mean ADP is
**unresolved**, and §19.4, §31.2, §31.3, §36.2 and the draft-day sheet all
depend on the answer. The adapter stores each raw payload unmodified and
records which distribution fields it contained, so the first archived capture
answers it. Record the answer in `research/questions.yaml`.

Note that fantasyfootballcalculator.com, Sleeper and the FantasyPros API are
**not reachable** from the Claude Code sandbox this was developed in (403 at
CONNECT). That is why the capture runs on a GitHub Actions runner.

## Layout

```
config/      league profiles, decision dates, sources, outcomes, evidence rules
data/raw/    downloaded source files (gitignored, reproducible)
data/snapshots/  dated immutable captures with hashes (COMMITTED — the archive)
data/processed/  canonical parquet tables (gitignored, rebuildable)
pipeline/    ingest, normalize, features, scoring, snapshot, cli
research/    questions.yaml; research modules land here in the offseason build
tests/       data, leakage, config and snapshot tests
```
