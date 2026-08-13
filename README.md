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
| ADP archival | §84 | daily GitHub Actions capture — the only item whose value expires — followed by a second job that re-renders the sheets from it |
| nflverse ingest | §10A | 2012–2025 weekly stats, snaps, rosters, depth charts, injuries, play-by-play, schedules |
| ID normalization | §12 | `gsis_id` canonical, crosswalk + labelled name matching |
| Canonical tables | §13 | `player_week`, `player_season` (+ outcomes), `team_season`, `adp_history`, `projection_snapshot` |
| Snapshots | §65 | dated, hashed, immutable |
| Tests | §51 | data, leakage, grading-config and snapshot checks |

| Research modules | §88 Week 2 | §25 team scoring regression, §21.1 dead-zone bucket rates — both DESCRIPTIVE |
| Projection ingest | §11 | FantasyPros API adapter (key-gated) with the manual-CSV fallback, `projection_snapshot` table |
| Tiers and VOR | §19.3, §19.4 | replacement level per profile, adjacent-gap tier breaks — running on real projections |
| Survival probability | §31.2, §19.4 | P(available) at held picks, normal approximation, every slot |
| Draft-day sheet | §83, §88 Week 3 | one printable page per league profile **and per draft slot**, plus an index |

**Not** built here: evidence grading, the draft simulator, and any publication
layer. Those are §79 Steps 4+.

## Quick start

```bash
make setup                      # uv sync
make ingest SEASONS=2023-2024   # nflverse raw data (start small)
make ids                        # player_ids.parquet
make tables SEASONS=2023-2024   # canonical tables
make research                   # S16 method artifacts
make sheet                      # the S83 draft-day sheets, one per league per slot
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

**§19.3 tiers — running.** Both gates are open, and the module produces boards
for both leagues from n=486 projected players: replacement level with flex
allocation, the value metric and adjacent-gap tier breaks. §31.2 survival runs
alongside it for all 12 slots of each league (n=1,724 half-PPR, n=1,880 PPR).
How the gates opened, and what it cost to open them, is under *Draft day* below.

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

## The draft-day sheet (§83)

```bash
make research      # writes artifacts/<edition>/methods/*.json
make sheet         # writes artifacts/<edition>/sheets/*.html, one per league per slot
make sheet SLOT=7  # rewrites one seat, for a draft-hour refresh
```

§78 makes the sheet an acceptance criterion and §88 makes it the deliverable
that survives if the schedule collapses: "a one-page output backed by three
sound analyses is worth more on draft day than a forty-chapter site that is not
finished." It is one self-contained printable page — inline CSS, no external
assets, because it is used at a draft table and §8 requires the output to work
offline.

It is a **formatter, not a calculator**. It reads `artifacts/<edition>/methods/*.json`
and computes nothing, so it can only ever be as complete as the research behind
it. §83's seven sections and what each carries today:

| Section | Spec | State |
|---|---|---|
| TIERS | §19.3 | **filled in** — tier, player, team, VOR by position |
| TARGETS | §27 | not built — needs graded evidence (§79) |
| AVOIDS | §28 | not built — needs graded evidence (§79) |
| REGRESSION | §25 | **filled in** — teams flagged, with the expected move |
| DARTS | §29 | not built (§79) |
| SURVIVAL | §31.2 | **filled in** — who is on the board at each held pick, and P(back at the next) |
| FALSE FRIENDS | §34 | not built — needs the matching engine (§32) |

A section with nothing behind it prints `NOT BUILT` or `BLOCKED` with the
reason. It does not go blank: a blank space on a draft sheet is a space
somebody fills in from memory at pick 43, which is the failure the sheet exists
to prevent. §83 also forbids evidence grades, confidence intervals and sample
sizes on the sheet — `assert_sheet_constraints` scans the rendered page for them
and raises, so the rule survives a section added by someone who did not read it.

Three of the seven sections now carry content, and the page is generated once per
league **and once per draft slot** — 26 sheets plus an `index.html` chooser,
because the draft order is drawn about an hour before the draft and that is not
an hour to be running a build in. See *Draft day* below.

Each of the 26 has been rendered to PDF and verified to print on a single page.
That is not decoration: the one-page rule broke the moment TIERS and SURVIVAL
started carrying real content, and it broke silently.

## Draft day: the order is drawn an hour before, so nothing is built then

Both gates are closed. `config/league_profiles.yaml` marks both leagues
`real: true`, and the FantasyPros API is capturing projections, so §19.3 tiers
and §31.2 survival run on real data and the §83 sheet carries them as content
rather than as `BLOCKED` notices.

The gates closed without the two values §14 was waiting for. **The draft dates
are not set, and the draft position is drawn about an hour before the draft
starts.** Both are recorded as `unknown`, which the build treats as an answer:

| Value | State | What it costs |
|---|---|---|
| `draft_date` | `unknown` | Nothing today. No code reads it; the sheet prices off the most recent archived ADP capture, which is the right rule when the draft is imminent and the only one available when the date is not known. §36.2's simulator will want a real one. |
| `draft_slot` | `unknown` | Nothing. §31.2 computes survival for every slot, and §83 renders a sheet for each. |

`unknown` is accepted and the literal `TODO` is not — `pipeline.config.
validate_profile()` raises on it. The distinction is the point: a placeholder
that flows through as "undrawn" is an unanswered question rendering as twelve
confident sheets.

### What to do at the draft

**`artifacts/2026-draft/sheets/index.html`** — that is the whole procedure. Open
it, tap the seat you drew, print or read that page.

```
artifacts/2026-draft/sheets/
  index.html                  <- open this
  half_ppr_12__slot01.html    <- ...it links to these
  half_ppr_12__slot02.html
  ...
  half_ppr_12.html            <- slot-agnostic: tiers + regression, for the week before
  ppr_12__slot01.html
  ...
```

Nothing is rebuilt, nothing needs a network, and nothing needs the laptop to be
working — §8 requires the output to work offline, and an hour before a draft is
exactly when a build step fails. `artifacts/` is committed, so the sheets open
from a phone through GitHub with no local checkout at all.

`2026-draft` is a **fixed edition, regenerated in place**, so that address never
changes and can be bookmarked. The dated editions (`2026.08.13-r1`, ...) are the
archival scheme and are untouched by the refresh.

**The sheets refresh themselves.** The archive workflow's second job rebuilds the
market-dependent research and re-renders all 26 sheets after every capture, so
what is committed is never more than a day behind the board. Nothing needs to be
run by hand at any point, which matters precisely because the draft dates are
unknown — there is no date on which anyone would remember to.

If a machine *is* to hand and you want one seat re-rendered immediately:

```bash
make sheet SLOT=7            # rewrites that seat only, leaves the other 11 alone
```

Set `draft_slot: 7` in the profile instead if the order is drawn well in advance:
the league then gets a single `half_ppr_12.html` and no per-slot fan-out.

### The date to look at on the sheet

Every page carries two dates, and they are not the same thing:

> Edition 2026-draft · generated 2026-08-14 · priced from the ADP capture of **2026-08-14**.

`generated` is written by the run that writes the page, so it stays reassuringly
current even if the pipeline broke upstream and the board underneath is weeks
old. **The capture date is the one worth reading.** When the two differ the sheet
says `— not today's`, and `index.html` prints a red banner saying the daily
refresh has not run. A sheet that says `ADP not priced` was rendered with no
survival artifact behind it at all.

### Checking it is still one page

§83's one-page rule is the constraint the sheet is most likely to break as
sections fill in, and it did break: with TIERS and SURVIVAL finally carrying
content, 24 players a position rendered 1,078px against a 989px budget and
printed on two. `MAX_TIER_PLAYERS` in `research/sheet.py` is now 16, measured
rather than guessed. Re-measure before raising it:

```bash
for f in artifacts/2026-draft/sheets/*.html; do
  chromium --headless --no-pdf-header-footer --print-to-pdf="/tmp/$(basename $f).pdf" "file://$PWD/$f"
done   # every PDF must be 1 page
```

## The projection source (§11)

`FANTASYPROS_API_KEY` is configured as a repository secret and the daily archive
job captures QB/RB/WR/TE projections alongside ADP. The key travels in a header
and is never written into a snapshot URL or manifest — this repository is public,
and a key in a manifest is a key in the git history.

`research validate` names which of §11's paths is live, so the question does not
require reading a CI log. Note it reads the key from the environment, so an unset
key on a laptop says nothing about the repository secret.

The archive workflow takes a `sources` input, because a dated capture is
immutable (§84): re-running `ffc` on a day whose ADP has already landed fails the
overwrite guard — correctly — and aborts the run before `projections` gets its
turn. `sources: projections` captures one without the other.

### What the payload actually looked like

The adapter was written blind (`api.fantasypros.com` answers 403 at CONNECT from
the development sandbox) and every shape-dependent value was put in
`config/sources.yaml` rather than in Python for exactly that reason. The guess was
wrong in four ways, and all four were YAML edits:

* **Stats are nested** under a `stats` object; identity stays flat on the row.
  The mapping read stat columns off the row and would have found none.
* `rec` is `rec_rec`; `fpts` is `points` (with `points_ppr` and `points_half`
  alongside it).
* **Targets and games played are not published at all**, so both mappings were
  removed rather than pointed at a near-miss column. Nothing keyed on projected
  opportunity share can be built from this provider's board.
* `fumbles` is fumbles *lost* under a shorter name — the provider prices it at
  −2 in its own published half-PPR total.

That last one is also the check that the whole mapping is right rather than
merely non-empty: scoring the mapped frame under a half-PPR profile reproduces
FantasyPros' own `points_half` to the cent. A stat map can be wrong in a way that
still computes, and a tier board built on it looks exactly like a correct one.

## Before running research

`config/league_profiles.yaml` holds the two leagues actually being drafted —
12-team half-PPR and 12-team full PPR — with scoring, starters and team counts
correct, and both marked real.

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
* **FantasyPros** — projections, key-gated and configured; first capture
  2026-08-13 (§11).

Full registry with purposes and licenses: `config/sources.yaml` (§46). This is
a technical registry, not legal advice.

### Source questions

**§31.1 — resolved, and it constrains survival.** FFC publishes a mean, a
standard deviation, a high, a low and a draft count per player, and **no
percentiles and no per-pick histogram**. So P(available at pick N) cannot be
computed empirically, and §31.2 uses §19.4's labelled fallback
`1 − Φ((next_pick − adp) / adp_stdev)`. Every survival artifact records
`opportunity_cost_method: normal_approximation` so pages built on it can be
found and regenerated if a real distribution ever arrives, and rows where the
normal puts probability on picks that have never happened carry a note saying
so.

**`fantasypros_projection_shape` — resolved 2026-08-13, and the guess was wrong
in four ways.** Stats are nested under `stats`; `rec` is `rec_rec`; `fpts` is
`points`; targets and games played are not published at all. All four were YAML
edits, which is what putting the shape in configuration bought. Details under
*What the payload actually looked like* above.

Note that fantasyfootballcalculator.com, Sleeper and the FantasyPros API are
**not reachable** from the Claude Code sandbox this was developed in (403 at
CONNECT). That is why both captures run on a GitHub Actions runner.

## Layout

```
config/      league profiles, decision dates, sources, outcomes, evidence rules
data/raw/    downloaded source files (gitignored, reproducible)
data/snapshots/  dated immutable captures with hashes (COMMITTED — the archive)
data/processed/  canonical parquet tables (gitignored, rebuildable)
pipeline/    ingest, normalize, features, scoring, snapshot, cli
research/    questions.yaml, method contract, foundations/ teams/ running_back/, sheet.py
artifacts/   dated editions: methods/*.json (S16) and sheets/*.html (S83),
             plus 2026-draft/ -- the live board, refreshed daily in place
tests/       data, leakage, config and snapshot tests
```
