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

| Research modules | §88 Week 2 | §25 team scoring regression, §21.1 dead-zone bucket rates — both DESCRIPTIVE |

**Not** built here: evidence grading, the draft simulator, the draft-day sheet,
and any publication layer. Those are §88 Week 3 and §79 Steps 4+. §19.3 tiers is
scaffolded and blocked; see below.

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

## What the two Week 2 analyses found

Both are **DESCRIPTIVE** (§2.2). No evidence grades — §88 forbids them here and
the grading engine is §79 Step 4. Run with `make research`; each writes an §16
artifact to `artifacts/<edition>/methods/`.

**§25 team scoring / TD regression — kill rule triggered, and the finding stands
anyway.** Over 416 consecutive-season pairs, an opportunity model (plays,
yards/play, red-zone trips, pass rate — deliberately *not* red-zone TD rate,
which would absorb the residual) explains 78% of offensive touchdowns. Its
residual explains **0.29%** of next-season TD totals, against the registry's 2%
threshold, so the kill rule fires and is recorded as fired. But the same data
shows the regression the decision actually rests on: Spearman **−0.52** between a
team's offensive-TD z-score and its next-season change, teams at z ≥ +2 losing
**17.7** touchdowns (n=10, all ten moved toward the mean). All nine §25 metrics
regress, Spearman −0.50 to −0.62. The kill rule measures whether last year's luck
predicts next year's *total*, which is mostly next year's opportunity — arguably
the wrong quantity. Amending it after seeing the result is what §80 prohibits, so
it is left triggered and the amendment is a decision for the registry.

**§21.1 RB dead zone — the zone is there, and it opens earlier than the spec
guessed.** Half-PPR 12-team, 2018–2025, 1,285 drafted player-seasons. Top-12 hit
rate for running backs against the receivers priced alongside them:

| ADP | RB | WR | difference | risk ratio |
|---|---:|---:|---:|---:|
| 1–12 | 0.63 | 0.81 | −17.1 pp | 0.79 |
| 13–24 | 0.51 | 0.61 | −9.7 pp | 0.84 |
| 25–36 | 0.23 | 0.40 | −17.4 pp | 0.57 |
| **37–48** | **0.12** | **0.42** | **−30.5 pp** | **0.28** |
| 49–60 | 0.09 | 0.23 | −13.1 pp | 0.42 |
| 61–72 | 0.15 | 0.22 | −6.8 pp | 0.69 |

The spec hypothesised ADP 37–72; the gap opens around pick 25 and is widest at
37–48. Two limits worth carrying: n in the 37–72 band is **93**, not the 175 §5.1
estimated, because FFC serves historical ADP only from **2018** and not from 2007
as §10B states — 2015 and 2017 come back empty. And ~3% of drafted players carry
no ID match and leave the denominator, skewing fringe, so each rate is a slight
upper bound. The artifact carries both.

**§19.3 tiers — blocked twice.** No league profile is marked `real: true`, *and*
there is no projection source, so §19.3's `projected_points − replacement_points`
has no left-hand side. Filling in a league profile alone will not unblock it.

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

`config/league_profiles.yaml` holds the two leagues actually being drafted —
12-team half-PPR and 12-team full PPR — with scoring and starters already
correct. Both are still `real: false`, because two values are unknown:
`draft_date` and `draft_slot`. Fill those in and flip the flag; nothing else
needs to move.

One profile per league, not one per scoring system. Replacement level is
`teams × starters`, so two leagues with the same scoring and different team
counts have different tier breaks, and draft date and slot are per-league inputs
to survival probability (§31.2) and the simulator (§36.2). §83 generates the
draft-day sheet per profile for the same reason.

Both formats are already in `adp_capture`, so their price history is being
archived daily. A test derives each profile's ADP format from its scoring and
asserts the capture list covers it — over *every* profile, not just the real
ones, since discovering a gap on draft week means the missing days are already
gone (§84).

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
