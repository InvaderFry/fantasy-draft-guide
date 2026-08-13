# Static Fantasy Football Draft Research Guide
## Implementation Handoff Specification

**Status:** Planning / implementation-ready  
**Primary output:** Static, versioned HTML research guide  
**Secondary outputs:** machine-readable research artifacts (JSON/Parquet), optional print/PDF export later  
**Audience:** Fantasy players who may need methodology explained, plus the maintainer/developer who wants exact formulas and reproducible research  
**Design inspiration:** The broad research structure of the 2022 Late-Round Fantasy Football Draft Guide, without copying proprietary text, rankings, tables, or player blurbs.

**Spec revision:** r2 — 2026-08-12. See §89 for the full revision log.

---

## Read before starting

Three constraints govern everything below. They were added in r2 because r1 implied a scope and a success rate that the available data and calendar cannot support.

**1. Decision value gates research, not the other way around.**
A signal can be statistically real, ADP-independent, and still change no pick you would ever make. Every research question must state, before any work begins, how a positive result would alter a draft decision. Questions that cannot answer this are not built. See §2.3 and §36.1.

**2. Statistical power gates chapters.**
Most position-and-ADP-band populations in this project are small — an ADP 37–72 running back cohort is roughly 150–200 player-seasons across 2012–2025, with 45–60 in a 2022–2025 holdout. Many of the chapters listed in §18 cannot detect any plausible effect at that sample size. Compute the minimum detectable effect **first**, mark the impossible ones `UNANSWERABLE`, and do not build them. See §5.1.

**3. The strategy-level test outranks every signal-level test.**
Player-level lift does not establish that a draft strategy is better. The governing evaluation is whether a board built from these signals beats a pure-ADP board across simulated drafts. See §36.2. If it does not, the research has not earned a place in the draft process regardless of how many Grade-A signals it produced.

**Calendar note.** The 2026 regular season opens September 9, 2026, so redraft leagues draft in roughly the two to three weeks following this revision. The full specification in this document is a multi-month build and will not produce a usable 2026 draft input in that window. §88 defines a compressed path that does, and defers the rest to the September–February offseason, when a genuine new holdout season (2026) also becomes available.

---

# 1. Product definition

Build a **static interactive research publication** that sits beside an existing fantasy-football drafting application.

The drafting application answers:

> **Who should I draft right now?**

This research guide should answer:

> **Why does a methodology exist, what data supports it, how strong is the evidence, what formulas were used, how has it performed historically, and which current players fit or fail the methodology?**

This is not primarily a draft tracker, simulator, or recommendation UI. It is a **research companion, methodology manual, evidence browser, and annual snapshot of draft beliefs**.

The guide must support:

- explanatory prose for a non-expert audience;
- exact technical specifications for reproducibility;
- descriptive research;
- predictive research;
- prescriptive draft conclusions;
- explicit separation among those three;
- current-season player applications;
- historical success and failure examples;
- evidence-quality labels;
- multiple editions within the same NFL season;
- full static-site output that can be archived permanently.

---

# 2. Core product principles

## 2.1 Explain before prescribing

Every chapter should begin with a plain-English explanation.

Only after the reader understands the idea should the guide expose:

1. research question;
2. population;
3. features;
4. outcome definition;
5. formulas;
6. results;
7. uncertainty;
8. robustness;
9. current-season application;
10. draft recommendation.

## 2.2 Descriptive, predictive, and prescriptive claims must look different

Every important conclusion must be labeled as one of:

### DESCRIPTIVE

A statement about what happened historically.

Example:

> Running backs selected in this ADP range had a lower high-end hit rate than wide receivers selected in the same range.

### PREDICTIVE

A statement that a feature had measurable out-of-sample predictive value.

Example:

> Receiving involvement improved the probability of a dead-zone RB producing a top-12 season in held-out seasons.

### PRESCRIPTIVE

A draft recommendation based on the evidence plus opportunity cost / roster construction.

Example:

> Prefer wide receiver in this range unless the available RB meets the receiving and age criteria.

### HYPOTHESIS / EXPLORATORY

An interesting association that is not yet strong enough to use as a major recommendation.

Example:

> Smaller backfield-role gaps appear associated with better late-round upside, but the test sample is still small.

Never silently convert a descriptive correlation into a prescriptive rule.

## 2.3 Decision value precedes statistical significance

A third failure mode sits alongside the descriptive/prescriptive confusion: a finding can be real and inert.

If a signal fires only on players already priced correctly, or only at picks the drafter never holds, or only on players who would have been selected on projection alone, then confirming it changes nothing. Statistical significance measures whether an effect exists. **Decision value measures whether knowing it changes a pick.** They are independent properties and the second one is what the guide is for.

Every entry in the research question registry (§68) must therefore declare, before any analysis is run:

```text
decision_change: what pick would I make differently if this result is positive?
decision_frequency: roughly how many times per draft does that situation arise?
decision_magnitude: rough points-per-pick swing when it does
```

If `decision_change` cannot be written in one concrete sentence, the question is not researched. The formal metric is specified in §36.1, and the chapter list in §18 is ordered by it.

This principle also constrains presentation. A signal with high statistical confidence and low decision value must be reported as such — §4 already requires a `practical importance` label, and a finding marked `low` may not appear in Targets, Avoids, or the draft-day sheet, no matter how strong the evidence grade.

---

# 3. Evidence-strength system

Evidence quality must be visible throughout the guide.

Every signal, methodology, and recommendation should have an `evidence_grade`.

| Grade | Meaning | Use |
|---|---|---|
| **A — Strong** | Meaningful effect, adequate sample, survives out-of-sample testing, direction stable across reasonable definitions | Can materially influence recommendations |
| **B — Moderate** | Useful effect with some instability, smaller sample, or dependence on definition | Important supporting signal |
| **C — Weak** | Small effect, unstable estimate, or limited sample | Minor supporting signal only |
| **D — Exploratory** | Interesting pattern not validated prospectively / out of sample | Display for research, do not materially weight |
| **F — Failed** | Did not replicate or has no practical signal | Preserve in guide as a rejected hypothesis |
| **U — Unanswerable** | Available sample cannot detect a plausible effect (§5.1) | Never graded on results. Record the question, record why, do not build the chapter |

## 3.1 The rubric must be numeric and committed in advance

r1 described grades in prose — "meaningful effect," "adequate sample" — while §51 required that evidence-grade rules be deterministic. Those two requirements are incompatible, and the gap resolves in exactly one direction: toward grade inflation, because the researcher grades their own work while looking at the results.

Grades are therefore assigned **by code, from thresholds committed to `config/evidence_rules.yaml` before any analysis runs.** No manual grade assignment. No post-hoc threshold adjustment; changing a threshold requires a new `methodology_version` and an entry in the update tracker (§26) stating that the rule changed and why.

Starting thresholds — tune these once, in advance, then leave them alone:

```yaml
evidence_rules:
  version: 1.0.0
  committed: 2026-08-12

  A:
    min_analysis_n: 150
    min_holdout_n: 40
    fdr_q_max: 0.05                  # after §5.2 correction
    holdout_ci_excludes_null: true
    min_absolute_effect_pp: 5.0      # binary outcomes
    min_standardized_effect: 0.15    # continuous outcomes
    definition_stability: 3_of_4     # §22.1 outcome-definition variants
    adp_incremental: positive_out_of_sample   # §36

  B:
    min_analysis_n: 100
    min_holdout_n: 25
    fdr_q_max: 0.10
    holdout_direction_retained: true  # CI may include null
    min_absolute_effect_pp: 3.0
    min_standardized_effect: 0.10
    definition_stability: 3_of_4
    adp_incremental: non_negative_out_of_sample

  C:
    min_analysis_n: 50
    nominal_p_max: 0.05              # pre-correction
    holdout_direction_retained_or_untestable: true
    definition_stability: 2_of_4

  D:
    requires: exploratory_only
    note: fails holdout, or never tested prospectively, or secondary outcome (§5.2)

  F:
    requires: adequately_powered_null_or_sign_reversal
    note: preserve permanently in the failed-signal archive (§31.7)

  U:
    requires: mde_exceeds_plausible_effect   # §5.1
    note: never assigned a results-based grade
```

**Expected distribution.** With realistic sample sizes and honest multiple-comparison control, a mature edition should be expected to produce roughly one to three Grade-A signals, a handful of B, and a long tail of C/D/F/U. That is the correct outcome, not a shortfall. Any edition reporting six or more Grade-A signals should be treated as evidence of a bug in the grading code or a threshold that was loosened after seeing results.

A methodology card should show:A methodology card should show:

```text
Evidence grade: B — Moderate
Graded by: evidence_rules v1.0.0 (automatic)

Relationship type: Predictive association
Primary metric: Odds ratio
Raw effect: 1.42×
Shrunk effect: 1.24×            (§39.1)
95% CI: 1.12–1.79
Sample: n=186 analysis / n=52 holdout
Minimum detectable effect: 1.31× at 80% power   (§5.1)
Tests run in this family: 9
FDR-adjusted q: 0.06            (§5.2)
Primary or secondary outcome: primary
Training period: 2012–2020
Holdout period: 2021–2025
Holdout result: direction retained, smaller magnitude
Independent of ADP: partial
Decision value: 0.7 pts/pick × ~1.8 picks per draft   (§36.1)
Recommendation weight: Medium
```

Every field above is generated. None is typed by hand.

---

# 4. Correlation / signal reporting standard

Do not summarize every relationship with a generic correlation coefficient.

Use the statistic that matches the question.

## Continuous feature → continuous outcome

Examples:

- target share vs next-season PPG;
- age vs fantasy PPG.

Report:

- Spearman rank correlation as default;
- Pearson correlation when linearity is appropriate;
- confidence interval;
- scatterplot / binned trend;
- sample size.

## Binary feature → binary outcome

Examples:

- Year-2 WR vs breakout yes/no;
- team WR1 by ADP vs breakout yes/no.

Report:

- hit rate in each group;
- absolute percentage-point difference;
- risk ratio;
- odds ratio;
- confidence interval;
- sample size.

## Continuous feature → binary outcome

Examples:

- target share vs probability of breakout.

Report:

- logistic-regression coefficient;
- odds ratio per meaningful unit;
- calibration chart;
- binned observed hit rates.

## Category → continuous outcome

Examples:

- experience-year group vs future PPG.

Report:

- median;
- interquartile range;
- distribution plot;
- effect size.

## Required interpretation language

For every signal include:

```text
Signal strength: strong / moderate / weak / none
Independent signal: yes / partial / no / not tested
Correlation is not causation: explicit note where relevant
Practical importance: high / medium / low
```

A statistically detectable effect that adds almost no useful draft information should be labeled **low practical importance**.

---

# 5. Required robustness checks

Checks 1 and 2 are **gates**: they run before the method is built, and a failure stops the work rather than lowering a grade. Checks 3–14 run on every major method where data permits.

```text
GATE  1. statistical power / minimum detectable effect      (§5.1)
GATE  2. multiple-comparison budget and primary outcome     (§5.2)

      3. sample-size check
      4. confidence interval
      5. alternate threshold definitions
      6. scoring-format sensitivity
      7. leave-one-season-out analysis
      8. forward-chaining / time-based holdout
      9. comparison against a naive baseline
     10. comparison against ADP alone
     11. multivariable test for information beyond ADP
     12. availability vs per-game production decomposition   (§15.1)
     13. missing-data sensitivity
     14. era sensitivity
```

r1 listed multiple-testing last, as a "caution." That understates it: across roughly twenty methods testing eight or so candidate features each, the project runs 150+ hypothesis tests, which at α=0.05 produces about eight false positives by construction — approximately the number of A and B signals a naive process would report. Correction is not a caveat, it is load-bearing.

The guide should visibly say when a robustness test cannot be performed.

---

# 5.1 Statistical power and the feasibility gate

**This section runs before any chapter is built.**

Fantasy research populations are small, and the document's chapter list was inherited from a guide with no obligation to demonstrate that its sections were answerable. Many are not.

### Required calculation

For every question in the registry (§68), before implementation:

```python
n_analysis   = size of the population after all filters
n_holdout    = size within the time-based holdout window
baseline     = outcome rate in the comparison group
mde          = minimum detectable effect at 80% power, alpha 0.05,
               using the FDR-adjusted alpha from §5.2
```

For a two-group binary comparison, the usual approximation is sufficient:

```python
from statsmodels.stats.power import NormalIndPower
from statsmodels.stats.proportion import proportion_effectsize

mde = solve_for_effect(n1, n2, alpha=alpha_adjusted, power=0.80)
```

Record `n_analysis`, `n_holdout`, `baseline`, `mde`, and `plausible_effect` in the question registry and in the exported method artifact.

### The gate

```text
if mde > plausible_effect:
    status = UNANSWERABLE
    build  = False
```

`plausible_effect` is the largest effect a reasonable person would expect *before* looking at the data — for most fantasy signals that is 5 to 12 percentage points on a breakout rate. If the sample cannot detect an effect that large, the chapter cannot produce information, and building it produces a noisy estimate that will be graded C or D and then quietly influence recommendations anyway.

### Expected findings

Rough populations, 2012–2025, before quality filters:

```text
RB, ADP 37–72              ~150–200 seasons   holdout ~45–60
WR, ADP 50–100             ~200–260 seasons   holdout ~60–80
QB, all draftable          ~380–450 seasons   holdout ~110–130
TE, ADP 60–150             ~90–130 seasons    holdout ~28–40
Team-seasons (regression)  ~448                holdout ~128
```

At these sizes the team regression chapters (§24, §25) are comfortably powered; the RB and WR archetype chapters can detect only large effects; and the tight end feature work (§23.2, §23.3) and pocket-passer ceiling (§20.3) are very likely `UNANSWERABLE`. Determine this in week one rather than after building them.

### Reporting

Every methodology page displays its MDE next to its effect estimate. A reader must be able to see that a null result was a null result and not merely an underpowered one. When a chapter is marked `UNANSWERABLE`, the question still appears in the guide, in the failed/retired archive (§31.7), with the sample size that would be required to answer it.

---

# 5.2 Multiple-comparison protocol

### Pre-registration

Each question in `research/questions.yaml` declares exactly one **primary outcome** before analysis. Everything else is secondary.

```yaml
primary_outcome: wr_breakout_top24
secondary_outcomes:
  - wr_breakout_ppg15
  - wr_beat_adp
  - wr_value_over_replacement
```

Rules:

- The primary outcome determines the evidence grade.
- Secondary outcomes are used for the definition-stability check (§22.1) and for nothing else.
- **A result appearing only on a secondary outcome is capped at Grade D** and is described as exploratory in the prose.
- Changing a primary outcome after seeing results requires a new method version and an update-tracker entry recording the change.

### Correction

Apply Benjamini–Hochberg false-discovery-rate control **within each method's family of tests** — the candidate features examined for one research question — and report the family size:

```python
from statsmodels.stats.multitest import multipletests
reject, q_values, _, _ = multipletests(p_values, alpha=0.05, method="fdr_bh")
```

Store in every method artifact:

```json
"multiplicity": {
  "family_size": 9,
  "primary_outcome": "wr_breakout_top24",
  "alpha_nominal": 0.05,
  "correction": "fdr_bh",
  "q_primary": 0.06
}
```

### Guide-level accounting

The evidence leaderboard (§31.6) reports the project-wide totals: how many tests were run across all editions, how many survived correction, and how many false positives would be expected by chance. This number belongs in the guide. It is the single most useful piece of context a reader has for interpreting any individual claim.

---

# 6. Time-aware validation

Never use random train/test splits as the primary evaluation.

Fantasy research is chronological.

**Rolling-origin validation is the default.** A fixed three-way split spends most of a small dataset on training and leaves a holdout too small to estimate anything precisely — with an RB dead-zone population of ~180, a 2022–2025 holdout is ~50 observations, which cannot distinguish a moderate effect from noise. Rolling origin uses every season as test data exactly once and produces a holdout estimate with usable precision.

```text
Train through 2018 → test 2019
Train through 2019 → test 2020
Train through 2020 → test 2021
Train through 2021 → test 2022
Train through 2022 → test 2023
Train through 2023 → test 2024
Train through 2024 → test 2025
```

Pool the out-of-fold predictions and evaluate once on the pooled set. Report the per-season results as well, since stability across folds is itself evidence and instability across folds is a downgrade under §3.1.

Retain a fixed final holdout only for methods with an adequately large population — the team-level regression chapters (§24, §25) qualify at 448 team-seasons; most player archetype chapters do not.

```text
Fixed split (large-n methods only):
  Development  2012–2018
  Validation   2019–2021
  Final holdout 2022–2025
```

The current season must never leak into historical research outcomes.

---

# 6.1 As-of discipline: making leakage a schema violation

§51 requires a test that "outcomes do not use future data." As written that cannot be implemented, because nothing in the data model records *when* a value became knowable. This section supplies the missing mechanism, and it is cheap now and impossible to retrofit later.

### Rule

**Every row in every feature table carries an `as_of` date: the date on which that value first became knowable to someone standing outside the future.**

```text
as_of          date the value became knowable
source_as_of   date the upstream source published or last revised it
value_type     observed | derived | imputed | unavailable   (§37)
```

### Enforcement

The feature builder asserts, for every feature entering any model, that its `as_of` precedes the decision date for the season in question:

```python
DRAFT_DATE = {2012: date(2012, 8, 25), ...}   # config/decision_dates.yaml

def assert_knowable(frame, season):
    cutoff = DRAFT_DATE[season]
    bad = frame.filter(pl.col("as_of") > cutoff)
    if len(bad):
        raise LeakageError(
            f"{len(bad)} rows in {frame.name} postdate the {season} draft: "
            f"{bad.select(['player_id', 'feature', 'as_of']).head(10)}"
        )
```

This converts leakage from a question a reviewer might think to ask into an exception that stops the build. It also catches the subtle cases that review reliably misses: end-of-season roster data used as a preseason feature, a revised statistic backfilled by the provider after the fact, an injury designation assigned in November, or a `player_season` aggregate joined onto a preseason population.

### Decision dates

`config/decision_dates.yaml` records one draft date per historical season — the point the research is pretending to stand at. Use the last week of August for each season, and state the choice on every methodology page, because a feature knowable on August 30 is not knowable on August 10 and the ADP snapshot differs materially between them.

### Outcome tables are exempt and must be marked

Outcome columns necessarily come from the future. They live in separate tables, are never joined into a feature frame, and carry `is_outcome: true` so the assertion skips them deliberately rather than by omission.

---

# 7. Versioning and annual editions

Multiple versions can exist in the same season.

Use an edition identifier:

```text
2026.07.15-r1
2026.08.01-r2
2026.08.11-r3
```

Each build stores:

```yaml
season: 2026
edition: 2026.08.11-r3
generated_at: 2026-08-11T21:53:00-05:00
data_cutoff: 2026-08-11
research_code_version: git commit hash
methodology_version: semver or git tag
scoring_profile: half_ppr_1qb_12team
projection_snapshot: provider + snapshot id
adp_snapshot: provider + date
```

Output directories:

```text
dist/
  2026/
    2026.07.15-r1/
    2026.08.01-r2/
    2026.08.11-r3/
  2027/
    ...
```

Create a top-level edition chooser.

## Do not overwrite past editions

An old edition is a record of what the system believed **at that time**.

If code changes August 20, create a new edition rather than rebuilding the August 11 edition with new logic.

---

# 8. Recommended technology stack

The framework can vary, but the recommended implementation is:

## Research / data layer

```text
Python
Polars or pandas
DuckDB
SciPy
statsmodels
scikit-learn
PyArrow / Parquet
```

## Static publication layer

**Recommended for v1:**

```text
Quarto
Python execution
static HTML output
```

Reasons:

- renders directly from Python, so analysis and prose live in one file and numbers cannot drift out of sync with the code that produced them;
- book/chapter structure, cross-references, a searchable index, and figure caching are built in rather than assembled;
- native support for foldable technical sections, which is exactly the reader/technical toggle in §73;
- freeze/cache means a rebuild does not re-run the whole research pipeline;
- plain static files, archivable, no runtime server.

**Deferred to v2 (only if the guide is ever distributed publicly):**

```text
Astro
TypeScript
static output
```

Astro is the better choice for a polished public product with heavily interactive components. It is the wrong first choice here: it adds a TypeScript site layer, a component architecture, and a hydration story to a project whose actual bottleneck is research throughput, and it is where a solo build loses weeks that were budgeted for analysis. The artifact-driven design in §16 — the site renders from exported JSON, never from numbers embedded in prose — means the publication layer is swappable later at low cost. That is precisely why it should not be built first.

**Do not build any publication layer before the draft-day sheet (§83) exists.**

## Charts

Use a declarative chart library such as:

```text
Vega-Lite / vega-embed
```

Store chart specifications as JSON when possible.

## Search

Build a static search index, e.g. Pagefind or equivalent.

## Data tables

Use lightweight client-side tables with:

- sort;
- filter;
- search;
- column visibility;
- CSV export.

## Offline requirement

The finished edition should not require a backend.

Prefer vendored JS/CSS assets rather than mandatory CDN dependencies.

---

# 9. Repository architecture

```text
fantasy-research-guide/
│
├── config/
│   ├── league_profiles.yaml
│   ├── outcomes.yaml
│   ├── evidence_rules.yaml
│   └── sources.yaml
│
├── data/
│   ├── raw/
│   │   ├── nflverse/
│   │   ├── adp/
│   │   ├── projections/
│   │   ├── rankings/
│   │   └── manual/
│   │
│   ├── snapshots/
│   │   └── 2026-08-11/
│   │
│   └── processed/
│       ├── player_week.parquet
│       ├── player_season.parquet
│       ├── team_season.parquet
│       ├── adp_history.parquet
│       ├── current_players.parquet
│       └── player_ids.parquet
│
├── research/
│   ├── foundations/
│   ├── quarterback/
│   ├── running_back/
│   ├── wide_receiver/
│   ├── tight_end/
│   ├── teams/
│   └── market/
│
├── pipeline/
│   ├── ingest/
│   ├── normalize/
│   ├── features/
│   ├── analyses/
│   ├── models/
│   ├── evidence/
│   └── export/
│
├── artifacts/
│   └── 2026.08.11-r3/
│       ├── manifest.json
│       ├── methods/
│       ├── charts/
│       ├── tables/
│       ├── players/
│       └── recommendations/
│
├── site/
│   ├── src/
│   ├── public/
│   └── astro.config.*
│
├── dist/
├── tests/
└── README.md
```

---

# 10. Free historical data: recommended source stack

The preferred approach is **not one source**. Use multiple sources, with nflverse as the statistical backbone.

## Option A — nflverse / nflreadpy
### Recommended primary historical football dataset

Official resources:

```text
https://nflverse.nflverse.com/
https://nflreadpy.nflverse.com/
https://nflreadr.nflverse.com/
https://github.com/nflverse/nflverse-data
```

As of August 2026, nflverse exposes or documents loaders for:

```text
play-by-play
player game/season statistics
team game/season statistics
schedules
players
rosters
weekly rosters
snap counts
Next Gen Stats
historical participation data
draft picks
injuries
combine data
depth charts
contracts
FantasyPros ranking archive
fantasy player ID mappings
expected fantasy opportunity
```

A 2012+ research window is a reasonable default for modern fantasy work.

### Particularly useful fields / derived data

From player stats and play-by-play:

```text
targets
receptions
receiving yards
air yards
carries
rushing yards
touchdowns
fantasy scoring
target share
air-yard share
team pass attempts
team rush attempts
red-zone opportunities
goal-line work
team scoring
pass/run rates
```

From rosters / draft / combine:

```text
age
experience
height
weight
draft round
draft pick
combine metrics
team
position
```

From snap counts:

```text
offensive snaps
offensive snap percentage
```

### Participation data

nflverse documents participation data as available from 2016 when requesting the complete history.

Current documentation states:

- pre-2023 participation source: NFL Next Gen Stats via nflverse;
- 2023 onward: FTN Data via nflverse;
- recent participation data may not arrive until after the postseason;
- 2023+ participation carries CC-BY-SA attribution requirements.

The participation data contains players on the field and some route/coverage information.

### Important route-data caveat

Do **not** assume nflverse provides perfect historical `routes run` totals for every fantasy player across every desired season.

The participation dictionary includes on-field player lists and a `route` field for a primary receiver on a play, but this is not equivalent to a complete decade-long proprietary routes-run database.

Therefore:

```text
target share          HIGH historical availability
snap share            GOOD availability
air-yard share        GOOD availability
full routes-run data  LIMITED / incomplete for a long free history
```

Initial research should use target share, snap share, receiving opportunity, and other reproducible free signals.

Add true routes-run/TPrR/YPRR research only when a reliable dataset is available for the desired period.

### Licensing

The main `nflverse-data` repository is published under CC-BY-4.0 according to the nflverse GitHub organization page.

Some component datasets have their own attribution/share-alike requirements.

The implementation must record source and license metadata at the dataset level.

### Recommendation

**Use nflverse as the default historical-statistics source.**

---

## Option B — Fantasy Football Calculator ADP API
### Recommended free historical ADP source

Official documentation:

```text
https://help.fantasyfootballcalculator.com/article/42-adp-rest-api
https://help.fantasyfootballcalculator.com/article/34-average-draft-position-adp-data
```

Their documentation states that:

- historical ADP is available back to 2007;
- ADP comes from their mock-draft selections;
- computer selections are removed;
- formats include standard, Half-PPR, PPR, 2-QB, dynasty, and rookie;
- API parameters include scoring format, team count, year, and position;
- REST API use is free for personal and commercial use with requested attribution;
- data updates once per day.

Example documented API shape:

```text
/api/v1/adp/{format}?teams=12&year=YYYY
```

### Strengths

```text
long historical depth
simple JSON
historical year parameter
multiple scoring formats
clear documented API
free use with attribution
```

### Limitations

ADP represents the Fantasy Football Calculator draft population, not the entire fantasy market.

Historical data may represent a final/preseason market snapshot rather than every point in time during the summer.

### Recommendation

**Use as the default historical ADP backbone.**

---

## Option C — nflverse FantasyPros ranking archive
### Recommended historical expert-market comparison

Official nflverse loader:

```text
https://nflreadr.nflverse.com/reference/load_ff_rankings.html
```

`load_ff_rankings(type="all")` accesses an archive of FantasyPros expert-consensus rankings maintained through the DynastyProcess data repository.

Useful columns include:

```text
player
position
team
ECR
best expert rank
worst expert rank
standard deviation
scrape date
```

### What it is good for

```text
expert consensus history
expert disagreement
rank movement
ECR vs eventual outcome
ECR vs ADP
market-vs-expert divergence
```

### What it is NOT

This is **ranking data, not ADP**.

Never merge the concepts.

Use:

```text
ADP = where fantasy managers drafted
ECR = where experts ranked
```

Both are valuable because disagreement between the two can itself be studied.

---

## Option D — Sleeper public API
### Optional raw-draft / pick-distribution source

Official docs:

```text
https://docs.sleeper.com/
```

Sleeper documents a read-only API that can retrieve:

```text
leagues
drafts
specific drafts
draft picks
rosters
players
```

The documented API is free for non-commercial use without an API token.

### Potential use

If the project has access to a useful corpus of Sleeper league/draft IDs, raw picks make it possible to study the entire draft-position distribution:

```text
median pick
P10 / P90 draft pick
platform-specific ADP
chance player is drafted before pick X
recency-weighted ADP
league-format-specific ADP
```

This is more powerful than a single ADP number.

### Major limitation

The API makes drafts accessible, but it is not a turnkey `download every public draft on Sleeper` research dataset.

A legal/reliable method of obtaining the draft corpus still has to be defined.

Commercial use requires contacting Sleeper.

### Recommendation

**Deferred.** The capability is real and the survival-probability use case is genuinely valuable (§31.2), but the corpus-acquisition problem is unsolved and the permitted-access path is undefined. Neither is worth resolving under time pressure.

For the MVP, obtain survival probability from Fantasy Football Calculator pick distributions where exposed, and otherwise from the labelled normal approximation in §19.4. Revisit Sleeper in the offseason, and note that the drafter's own historical league draft logs are a small but fully permitted corpus that requires no external access at all — start exporting them (§84).

---

# 11. Free / authenticated current-season projection options

The user does not generate proprietary projections.

The system should therefore define a **projection-provider interface** with both manual-file and authenticated-API implementations.

Do not make the rest of the research code dependent on one projection provider.

The preferred FantasyPros implementation should support an API key when one is available, while retaining manual CSV and alternate-provider fallbacks.

Canonical schema:

```text
snapshot_date
source
player_id
player_name
team
position

pass_attempts
pass_completions
pass_yards
pass_tds
interceptions

rush_attempts
rush_yards
rush_tds

targets
receptions
receiving_yards
receiving_tds

fantasy_points
games
```

## Projection option 1 — FantasyPros Public API
### Preferred authenticated projection adapter when an API key is available

FantasyPros documents a public REST API that includes NFL projections, consensus rankings/ADP, players and metadata, news/injuries, and player fantasy points.

Official resources:

```text
https://www.fantasypros.com/api-data/
https://support.fantasypros.com/hc/en-us/articles/49749297704475-How-do-I-request-access-to-the-FantasyPros-API
```

The documented API base is:

```text
https://api.fantasypros.com/public/v2/json
```

Authentication is performed with an API key in the request header:

```text
x-api-key: YOUR_API_KEY
```

The documented NFL projections endpoint is conceptually:

```text
GET /nfl/{season}/projections
```

The consensus-ranking endpoint is:

```text
GET /nfl/{season}/consensus-rankings
```

### Required adapter behavior

Implement a provider such as:

```python
class FantasyProsAPIProjectionProvider(ProjectionProvider):
    provider_id = "fantasypros_api"

    def fetch(self, season, snapshot_date, positions=None, **params):
        ...
```

Configuration:

```yaml
projections:
  provider: fantasypros_api
  fantasypros:
    api_key_env: FANTASYPROS_API_KEY
    season: 2026
```

The key must **never** be committed to the repository, logged, written to snapshots, or embedded in generated HTML.

Use an environment variable:

```bash
export FANTASYPROS_API_KEY="..."
```

or a local ignored secrets file.

### Fetch flow

```text
1. Read API key from environment.
2. Call the FantasyPros projections endpoint.
3. Validate response schema.
4. Save the unmodified raw API response to the edition snapshot.
5. Normalize FantasyPros player IDs to the project's canonical IDs.
6. Transform provider fields into the canonical projection schema.
7. Store provider, endpoint, query parameters, retrieval timestamp, and response hash.
8. Continue the research pipeline using normalized projection data.
```

### Snapshot requirement

Even when projections come from an API, the edition must use a frozen snapshot.

Example:

```text
data/snapshots/2026-08-11/
  fantasypros/
    projections_raw.json
    players_raw.json
    consensus_rankings_raw.json
  current_projections.parquet
```

This ensures an August 11 edition can be regenerated later even if FantasyPros updates its live API data.

### FantasyPros metadata / ID mapping

If available under the active API access level, also use the FantasyPros players/metadata endpoint to obtain FantasyPros IDs and available external-ID cross-references.

Store:

```text
fantasypros_player_id
canonical_player_id
team
position
external_ids
```

Do not use player names as the primary join key.

### Consensus rankings and ADP

The same adapter family may expose separate methods for FantasyPros consensus rankings/ADP.

Keep these datasets logically separate:

```text
projection = forecasted player statistics / fantasy points
ECR        = expert consensus ranking
ADP        = market draft position
```

Do not collapse them into one field.

### API access / licensing

API access should be treated as configurable rather than assumed. The official FantasyPros API page provides a key-request flow and distinguishes API access/licensing needs, including commercial or higher-volume use.

The application should therefore support:

```text
FantasyPros API available
    → use authenticated adapter

FantasyPros API unavailable
    → use manual projection import or another provider
```

Never fall back to scraping FantasyPros pages automatically just because the API key is missing.

---

## Projection option 1B — FantasyPros manual import fallback

If an API key is not available, retain a manual import path for a FantasyPros projection snapshot.

Public projection pages may be used by a human to obtain data in a permitted way, but the research pipeline itself should accept a normalized CSV rather than depend on page scraping.

Example public page:

```text
https://www.fantasypros.com/nfl/projections/qb.php?week=draft
```

Required metadata:

```text
source = FantasyPros
source_type = manual_import
snapshot_date
season
scoring
file_hash
```

## Projection option 2 — FF Today

FF Today publicly displays season projections by position.

Example:

```text
https://www.fftoday.com/rankings/playerproj.php
```

Useful as:

- a free projection source;
- a comparison source;
- a fallback when consensus data is unavailable.

Treat automated ingestion as a separate adapter whose use must respect site terms.

## Projection option 3 — ESPN

ESPN publicly exposes current fantasy projection/ranking pages.

Example:

```text
https://fantasy.espn.com/football/players/projections
```

Use the same policy:

- allow manual import;
- snapshot the source/date;
- only automate if access terms permit it.

## Recommended current projection policy

For version 1, support **both**:

```text
1. FantasyPros authenticated API adapter
2. manual / user-supplied projection CSV adapter
```

The API path is preferred when `FANTASYPROS_API_KEY` is configured successfully.

Fallback order:

```text
FantasyPros API key configured
        ↓ yes
FantasyPros API adapter
        ↓ no / disabled
Manual normalized projection snapshot
        ↓
Optional alternate provider adapter
```

Normalize every provider into the same canonical projection schema.

The research guide should work regardless of whether projections came from:

```text
FantasyPros Public API
FantasyPros manual import
FF Today
ESPN
another projection provider
the user's existing draft application
```

The user can swap providers later without rewriting the research layer.

The edition manifest must record the exact provider path used:

```yaml
projection_source:
  provider: fantasypros
  transport: api
  snapshot_date: 2026-08-11
  endpoint_family: public_v2
```

or:

```yaml
projection_source:
  provider: fantasypros
  transport: manual_csv
  snapshot_date: 2026-08-11
```

---

# 12. Player ID normalization

This is foundational.

Never join solely by player name.

Preferred canonical key:

```text
gsis_id where available
```

Maintain crosswalks:

```text
gsis_id
espn_id
sleeper_id
fantasypros_id
pfr_id
yahoo_id
mfl_id
```

nflverse/ffverse ID mapping should be the initial crosswalk source.

Create:

```text
data/processed/player_ids.parquet
```

Manual corrections should live in version control.

---

# 13. Canonical analytical datasets

## player_week

One row per player / season / week.

Required:

```text
season
week
player_id
position
team
opponent
as_of                       # §6.1 — when this row became knowable
value_type                  # observed | derived | imputed | unavailable
games_active
active_status               # active | inactive | ir | dnp
inactive_reason             # injury | coach | suspension | unknown
fantasy_points_standard
fantasy_points_half_ppr
fantasy_points_ppr
targets
receptions
receiving_yards
receiving_tds
air_yards
carries
rushing_yards
rushing_tds
offensive_snaps
red_zone_targets
red_zone_carries
team_pass_attempts
team_rush_attempts
team_points
```

## player_season

Derived from `player_week` plus roster/draft information.

```text
season
player_id
position
team
as_of                       # §6.1
age
experience
draft_round
draft_pick

games
games_missed
games_missed_injury         # §15.1 — availability modelled separately
fantasy_points
fantasy_ppg
fantasy_ppg_active          # per-game production conditional on playing

targets
target_share
receptions
receiving_yards
air_yards
air_yard_share
red_zone_targets
carries
rush_share
goal_line_carries
red_zone_carries
offensive_snaps
snap_share

depth_chart_rank            # §86
depth_chart_rank_preseason  # as of the decision date, not end of season
team_position_share_rank
```

## team_season

```text
season
team
plays
plays_per_game
points
offensive_tds
passing_tds
rushing_tds
pass_attempts
rush_attempts
pass_rate
neutral_pass_rate_if_available
yards_per_play
red_zone_trips
red_zone_td_rate
turnovers
```

## adp_history

```text
season
snapshot_date
as_of
source
format
teams
player_id
adp
position_adp
sample_size_if_available

# pick-distribution fields — populate wherever the source exposes them (§31.1)
adp_stdev
pick_p10
pick_p25
pick_p50
pick_p75
pick_p90
n_drafts
```

Where a source publishes only a mean, leave the distribution fields null rather than approximating them in the table; approximation happens at analysis time and must be labelled there (§31.2).

## projection_snapshot

```text
season
snapshot_date
as_of
source
provider_id
player_id
all projection columns
```

Store one row per provider per player. Do not average providers into a single row — cross-provider dispersion is the only available proxy for projection uncertainty and averaging destroys it (§38.1).

---

# 14. League scoring profiles

Never hard-code one scoring system into the raw data.

Define profiles:

```yaml
half_ppr_1qb_12team:
  teams: 12
  pass_td: 4
  pass_yd: 0.04
  interception: -2
  rush_yd: 0.10
  rush_td: 6
  reception: 0.5
  receiving_yd: 0.10
  receiving_td: 6

  starters:
    QB: 1
    RB: 2
    WR: 3
    TE: 1
    FLEX: 1
```

Research pages should show which scoring profile produced the displayed result.

### Encode the leagues actually played before running any research

This is the first configuration task in the project, ahead of ingestion. Every downstream conclusion is conditional on scoring and roster structure, and the differences are not cosmetic: a Superflex league changes the entire quarterback section, and §19.4 explicitly requires a different replacement level for it. Half-PPR 12-team is a reasonable default only if it is the format actually being drafted.

```yaml
active_profiles:
  - id: home_league_half_ppr_12
    real: true
    draft_date: 2026-08-24
    draft_slot: 7
    teams: 12
    # ... full scoring and starter definition
  - id: work_league_superflex_10
    real: true
    draft_date: 2026-08-30
    draft_slot: unknown
```

Run research only for profiles marked `real: true`. Generic profiles are for illustration and are excluded from Targets, Avoids, and the draft-day sheet. Known draft slot and draft date matter — they determine which picks the drafter actually holds, which is an input to opportunity cost (§19.4), survival probability (§31.2), and the draft simulator (§36.2).

Eventually allow tabs for Standard / Half-PPR / PPR / Superflex. The MVP runs the real profiles only.

---

# 15. Standard outcome definitions

Centralize outcomes in configuration.

Do not scatter definitions through code.

Example:

```yaml
rb_high_end:
  type: positional_finish
  position: RB
  max_finish: 12

wr_high_end:
  type: ppg_threshold
  min_ppg: 15.0
  min_games: 8

beat_adp:
  type: value_over_adp_baseline

bust:
  type: value_over_adp_percentile
  threshold: -0.75
```

Every chapter must link to the exact outcome definition used.

---

# 15.1 Outcome decomposition: availability × per-game production

Fantasy outcome is the product of two very different processes:

```text
season_points = games_played × points_per_game_when_active
```

These have almost nothing in common. Per-game production is substantially predictable from opportunity, role, and situation — the features this project is built on. Availability is close to unpredictable from the same features, and it is the dominant cause of the outcomes the guide labels "bust."

Modelling the combined outcome mixes a learnable signal with a large, mostly irreducible noise term. The consequence is systematic and damaging: **every evidence grade in the guide is depressed for a reason that has nothing to do with the quality of the signal being tested.** A target-share signal that genuinely predicts per-game production will look weak against a season-total outcome because a third of the cohort missed six games for reasons the model could never see.

### Requirement

Every method with a season-level outcome fits and reports both components:

```python
# (a) availability
P(games >= 14)  ~  ADP + age + position + prior_games_missed + injury_history

# (b) per-game production, conditional on playing
PPG | active    ~  ADP + candidate_signal + controls

# (c) recombination for the season-level statement
E[season_points] = E[games] * E[PPG | active]
```

Report the candidate signal's effect on **both** components separately, then on the recombined outcome. In most cases the signal will show a clear effect on component 2 and none on component 1, and that is the honest and useful finding: *this tells you about production, not durability.*

### Outcome definitions

Add availability-conditional variants to `config/outcomes.yaml`:

```yaml
rb_high_end_active:
  type: positional_finish_ppg
  position: RB
  min_games: 10
  max_finish_ppg: 12

wr_breakout_active:
  type: ppg_threshold
  min_ppg: 15.0
  min_games: 10
```

### Reporting rule

Where a signal predicts production but not availability, the prescriptive section must say so explicitly, because the draft implication differs. A player with a strong production profile and a poor availability profile is a different asset from one with both, and collapsing them into one expected-points number hides exactly the information a drafter needs when choosing between a high-floor and a high-ceiling roster slot.

### Limitation

Injury history has weak predictive power for future injury in most published work. Do not oversell component 1. Its purpose here is primarily to *remove* injury noise from component 2 so the signals under test can be measured cleanly — that is the main benefit, and it applies whether or not availability itself turns out to be predictable.

---

# 16. Standard methodology artifact

Every research method should export a JSON document.

Example:

```json
{
  "method_id": "rb_dead_zone",
  "title": "Running Back Dead Zone",
  "version": "2.1.0",
  "claim_type": "descriptive_predictive_prescriptive",
  "status": "active",
  "evidence_grade": "B",
  "population": {
    "seasons": [2012, 2025],
    "position": "RB",
    "scoring": "half_ppr"
  },
  "outcome": "rb_high_end",
  "sample_size": 284,
  "primary_results": {},
  "robustness": {},
  "current_matches": [],
  "historical_successes": [],
  "historical_failures": [],
  "limitations": [],
  "sources": []
}
```

The website should render from these research artifacts rather than embedding all numbers directly in prose.

---

# 17. Page anatomy for every methodology

Every major chapter uses the same reader flow.

1. **In one minute** — plain-English summary.
2. **Thesis** — the idea being investigated.
3. **Research question** — a falsifiable question.
4. **Definitions** — exact population/outcome.
5. **Data** — years, sample, sources, missingness, filters.
6. **Methodology** — formula/query/model.
7. **Evidence** — charts, tables, effect sizes.
8. **Signal quality** — evidence grade and explanation.
9. **Robustness** — alternate definitions and holdouts.
10. **Historical successes** — examples selected algorithmically.
11. **Historical failures** — mandatory, algorithmically selected.
12. **What would falsify this?** — explicit failure condition.
13. **Current-season matches** — strong/medium/near matches.
14. **Current-season non-matches / false friends**.
15. **Draft implication** — clearly labeled prescriptive section.
16. **Technical specification** — code-level formula/features/thresholds.
17. **Limitations**.
18. **Sources**.

---

# 18. Site information architecture

```text
HOME

PART I — FOUNDATIONS
  1. How to Read the Research
  2. Regression Is Everything
  3. Embracing Variance
  4. Rankings and Tiers
  5. Opportunity Cost & Positional Value

PART II — QUARTERBACK
  6. Quarterback Strategy
  7. Rushing / Konami Effect
  8. Pocket-Passer Ceiling
  9. Current QB Application

PART III — RUNNING BACK
  10. Running Back Strategy
  11. Running Back Dead Zone
  12. Beating the Dead Zone
  13. Middle-Round RB Breakouts
  14. Late-Round RB Breakouts
  15. Receiving Value
  16. Backfield Ambiguity
  17. Contingent Upside
  18. Current RB Application

PART IV — WIDE RECEIVER
  19. Wide Receiver Strategy
  20. WR Breakout Definitions
  21. Middle-Round WR Breakouts
  22. Late-Round WR Breakouts
  23. Youth / Year-2 Effects
  24. Rookie WR Development
  25. Team WR Hierarchy
  26. Current WR Application

PART V — TIGHT END
  27. Tight End Strategy
  28. TE Breakout Definitions
  29. Middle/Late TE Breakouts
  30. Experience Curves
  31. Team Pass-Catcher Hierarchy
  32. Current TE Application

PART VI — TEAM REGRESSION
  33. Team Philosophy Regression
  34. Team Scoring Regression
  35. Current Team Regression Flags

PART VII — CURRENT DRAFT MARKET
  36. Update Tracker
  37. Targets
  38. Avoids
  39. Late-Round Dart Throws
  40. Current Tiers / Research Board

PART VIII — ADDITIONAL RESEARCH
  41. ADP Uncertainty & Pick Distributions
  42. Will the Player Make It Back?
  43. ADP Movement / Recency
  44. Platform Differences
  45. Historical Comparable Players
  46. Evidence Leaderboard
  47. Failed / Retired Signals

APPENDICES
  A. Data Dictionary
  B. Outcome Definitions
  C. Scoring Profiles
  D. Source / License Registry
  E. Method Versions
  F. Build Manifest
```

### This list is a candidate pool, not a commitment

The structure above mirrors the broad 2022 Late-Round progression. That is useful as an inventory of questions the field considers interesting; it is not a research agenda, and adopting it wholesale imports another author's 2022 beliefs about what is worth investigating.

Every chapter above must pass two gates before it is built:

```text
1. decision value  — does a positive result change a pick?   (§2.3, §36.1)
2. statistical power — can this sample detect a plausible effect?  (§5.1)
```

Chapters failing gate 1 are cut. Chapters failing gate 2 are marked `UNANSWERABLE`, recorded in the failed/retired archive (§31.7) with the sample size that would be needed, and not built.

**Expect roughly fifteen to twenty chapters to survive, not forty-seven.** Sections most likely to fail the power gate on free data: pocket-passer ceiling (§20.3), TE breakout features (§23.2), TE experience curves (§23.3), and late-round RB breakouts (§21.4) at any useful granularity. Sections most likely to fail the decision-value gate: platform differences (§31.4) for a drafter whose platforms are known.

Order the surviving chapters by estimated decision value (§36.1), not by the sequence above. Foundations still come first for readability, but within each position the highest-value question is written first, because a compressed schedule will truncate the list from the bottom.

---

# 19. Foundation chapter specifications

## 19.1 Regression Is Everything

### Research question

How often do extremely high/low player and team rates move toward league average the following season?

### Candidate metrics

Team:

```text
offensive TDs/game
passing TD share
rushing TD share
plays/game
pass rate
red-zone TD rate
turnover rate
yards/play
```

Player:

```text
TD per touch
TD per target
yards per carry
catch rate
yards per reception
fantasy points per opportunity
```

### Formula

```python
z = (x - historical_mean) / historical_std
next_change = x_next_season - x_current_season
```

Estimate:

```text
E[next_change | current_z_bucket]
```

### Required chart

Current-season z-score vs next-season change, with a smoothed trend.

### Reporting

Report:

```text
Spearman relationship between current extremeness and next-year change
confidence interval
sample size
probability of moving toward the mean by z bucket
```

### Current-season application

List teams/players at `|z| >= 1.5` and `|z| >= 2.0`.

Explain likely direction, not guaranteed outcome.

---

## 19.2 Embracing Variance

### Goal

Move from point projections to distributions.

### MVP approach

For historical seasons where preseason projection snapshots exist:

```python
error = actual_points - preseason_projection
```

For comparable players, estimate P10/P25/P50/P75/P90 of the error distribution and add those errors to the current projection.

If historical projection archives are incomplete, use ADP-conditioned outcome distributions as the initial version and label the limitation.

### Better method

Quantile regression.

### Page output

Compare players with similar medians but different tails.

### Prescriptive use

Bench / late-round picks can place more weight on upper-tail outcomes. Early picks can place more weight on avoiding disastrous lower tails.

This is a recommendation layer and must be labeled prescriptive.

---

## 19.3 Structuring Rankings and Tiers

### Goal

Detect meaningful player-value clusters rather than imply precision in ordinal ranks.

### Inputs

Preferred:

```text
projection distribution
ADP
positional replacement value
```

### MVP tier metric

```python
player_value = projected_points - replacement_points(position)
```

Detect breaks with adjacent value gaps, change-point detection, or hierarchical clustering.

### Required visual

Value curve by position with tier breaks.

### Evaluation

A tier system should ideally show lower within-tier outcome separation than across-tier separation.

---

## 19.4 Opportunity Cost and Positional Value

### Core question

What future value is lost by selecting Position A instead of Position B now?

### Formula

```text
OC(p) =
Value(best available p now)
-
E[Value(best available p at next pick)]
```

If ADP distributions exist:

```text
E[next value] =
Σ P(player_i available at next pick) × conditional best-player value
```

### Replacement level

Define from league demand and starting requirements. Do not use the same replacement level for 1-QB and Superflex. Use the real league profiles from §14.

### Dependency — resolved in r2

`P(player_i available at next pick)` is not derivable from a mean ADP. It requires the pick distribution (§31.1) and the survival calculation (§31.2), which r1 scheduled in phase 3 — meaning the most decision-relevant chapter in the guide depended on the last data to be built.

**§31.1 and §31.2 are promoted to the MVP** (§59). Opportunity cost is not implementable without them, and "will he make it back to my next pick" is the question asked most often during an actual draft.

Until pick distributions are available, compute opportunity cost with an explicitly labelled approximation:

```python
# fallback only — label prominently on every page that uses it
P_available = 1 - Phi((next_pick - adp_mean) / adp_stdev_estimate)
```

and record `opportunity_cost_method: normal_approximation` in the method artifact so that pages built on the approximation can be found and regenerated when real distributions arrive.

---

# 20. Quarterback research specification

## 20.1 Positional supply

Measure:

```text
QB1–QB12 PPG spread
QB12–QB18 PPG spread
ADP cost by QB rank
replacement QB production
```

Compare with RB/WR/TE.

## 20.2 Rushing / Konami effect

### Research question

Does QB rushing materially increase the probability of elite fantasy outcomes after controlling for ADP?

Features:

```text
rush attempts/game
rush yards/game
rushing TDs
designed runs if available
scrambles if available
goal-line rushing
ADP
experience
```

Outcome:

```text
top-3 QB
top-6 QB
PPG threshold
```

Model:

```text
logit(P(elite)) =
β0 + β1*ADP + β2*rush_yards_game + β3*rush_attempts_game + β4*age
```

Report whether rushing adds predictive information beyond ADP.

## 20.3 Pocket-passer ceiling

Determine what passing production a low-rush QB historically needed to become elite.

Study pass attempts, passing yards, passing TDs, TD rate, and team pass volume.

---

# 21. Running back research specification

## 21.1 Detect the RB Dead Zone

Do not define the zone solely by tradition.

Create ADP buckets, e.g. 1–12, 13–24, 25–36, 37–48, etc.

For each bucket calculate:

```text
RB high-end hit rate
RB usable hit rate
RB bust rate
PPG
value over replacement
same-range WR metrics
same-range TE metrics
```

Candidate discovery score:

```python
dead_zone_score = (
    z(rb_bust_rate)
    - z(rb_high_end_rate)
    - z(rb_value_over_replacement)
    + z(wr_relative_advantage)
)
```

Use this score for discovery. The reader-facing chapter should present the simpler observed outcomes.

### Robustness

Test:

```text
different bucket sizes
round-based buckets
rolling ADP windows
PPR / Half-PPR / Standard
different high-end definitions
pre- and post-2020 eras
```

## 21.2 Beating the Dead Zone

Among RBs in the detected range, compare successes vs failures.

Candidate signals:

```text
age
experience
prior target share
projected targets
snap share
team scoring
NFL draft capital
backfield competition
prior PPG
```

For each feature show:

```text
raw difference
univariate signal
ADP-adjusted signal
multivariable signal
holdout signal
```

Retest old guide ideas instead of assuming them.

## 21.3 Middle-round RB breakouts

Candidate archetypes:

```text
team RB1
team RB2 behind expensive RB
young player
receiving role
rookie
ambiguous backfield
high-scoring offense
```

Outcome = breakout rate relative to ADP baseline.

## 21.4 Late-round RB breakouts

Emphasize contingent workload, receiving ability, uncertain depth chart, cheap veterans with prior production, and rookies with role uncertainty.

Floor should receive less weight in late-round prescriptive scoring.

## 21.5 Backfield ambiguity

### Compute ambiguity from realized shares, not projected shares

r1 defined ambiguity over *projected* opportunity shares. That is circular: the projections come from the same provider whose output is later used as the value baseline the signal is supposed to improve on. A provider that already believes a backfield is ambiguous encodes that belief in both the ambiguity score and the projection, and the resulting "signal" partly measures the provider's opinion rather than the football situation.

**Primary definition — realized prior-season shares plus known offseason change:**

```python
# s_i = each back's share of team RB opportunity in the PRIOR season,
#       carried forward with roster and draft changes applied
HHI       = sum(s_i ** 2)
ambiguity = 1 - HHI

role_gap  = share_RB1_prior - share_RB2_prior
```

Adjustments applied to the carried-forward shares are limited to observable facts as of the decision date (§6.1): departures, signings, draft capital spent on the position, and preseason depth chart (§86). Not projections.

**Secondary variant** — the same statistics computed on projected shares, retained only for the definition-stability check (§22.1) and never used as the primary outcome basis.

Research which version predicts low-cost breakouts better, and report both. If the projected-share version outperforms the realized-share version, the honest interpretation is that the provider's backfield opinion carries information — which is a finding about the provider, not about backfield ambiguity, and must be described that way.

## 21.6 Contingent upside

MVP:

```python
contingent_upside = starter_out_ppg - baseline_ppg
```

If starter-out projections are not available, estimate using historical team RB opportunity redistribution and label uncertainty prominently.

---

# 22. Wide receiver research specification

## 22.1 WR breakout definitions

Show at least:

```text
absolute PPG threshold
top-12 / top-24 positional finish
beat-ADP threshold
value-over-replacement threshold
```

A result should not be called robust if it exists only under one arbitrary breakout definition.

## 22.2 Middle-round WR breakouts

Candidate signals:

```text
age
experience
team WR rank by ADP
target share
air-yard share
prior-year PPG
draft capital
vacated opportunity
team pass volume
```

Compare team WR1 by ADP vs team WR2+.

## 22.3 Youth / Year-2 effect

Calculate observed breakout rates for rookie, Year 2, Year 3, Year 4, Year 5+.

Then adjust for ADP.

Key question:

> Does Year 2 predict breakout after accounting for the fact that exciting Year-2 players are already drafted earlier?

Model:

```text
logit(P(breakout)) =
ADP + experience_group + prior_target_share + prior_PPG
```

## 22.4 Rookie WR development

Use weekly data.

For each rookie calculate:

```text
Weeks 1–4 PPG
Weeks 5–8
Weeks 9–12
Weeks 13–17
target-share development
snap-share development
```

Compare growth to non-rookie WRs at similar ADP.

## 22.5 Team WR hierarchy

Create:

```text
team_pass_catcher_adp_rank
team_wr_adp_rank
ADP gap to teammate
```

Research whether inexpensive team WR2s behind expensive WR1s outperform other similarly priced WRs.

---

# 23. Tight end research specification

## 23.1 Elite-or-late question

Measure expected value at TE by ADP:

```text
top-tier TE PPG gap
middle-round TE PPG
late-round TE PPG
opportunity cost vs RB/WR at same ADP
```

The prescriptive conclusion should depend on the current year's market, not a permanent rule.

## 23.2 TE breakout features

Candidate signals:

```text
age
experience
team pass-catcher rank by ADP
target share
snap share
red-zone targets
team passing volume
QB projection
draft capital
athletic metrics
```

Test each independently and jointly.

## 23.3 Experience curves

Report breakout rates for rookie / Year 2 / Year 3 / Year 4+, controlling for ADP.

---

# 24. Team philosophy regression

Study year-to-year movement in:

```text
plays/game
pass rate
neutral pass rate
pace
run rate
```

For each metric:

```python
delta_next = value_next - value_current
```

Group current value into percentiles or z-score buckets.

Output:

```text
Current extreme
Historical average next-year change
Probability of moving toward league mean
```

Separate mathematical regression from context such as coaching/personnel changes.

---

# 25. Team scoring regression

Study:

```text
offensive TDs
points
passing-TD share
rushing-TD share
red-zone efficiency
turnovers
```

Example:

```python
pass_td_share = passing_tds / offensive_tds
```

Research whether extreme pass/rush TD shares rebalance next season.

Current page should show team, metric, historical percentile, z-score, historical regression tendency, projection context, and evidence grade.

---

# 26. Update Tracker

Every edition should contain an automatically generated update log.

Example:

```text
2026.08.11-r3

DATA
+ ADP snapshot updated through Aug 11
+ projection snapshot updated
+ injuries refreshed

METHOD CHANGES
~ RB dead-zone detection v2.0 → v2.1
  Reason: rolling window replaced fixed buckets

PLAYER APPLICATION
+ Player X added as RB target
- Player Y no longer a target due to ADP rise

RESEARCH
~ Year-2 WR evidence grade B → C
  Reason: weaker holdout effect
```

Record **why beliefs changed**.

---

# 27. Targets section

Targets must be generated from evidence, not subjective labels alone.

A target page should show:

```text
player
ADP
position
projection
research fair value
methodologies matched
evidence grades
positive signals
negative signals
recommended price range
```

Example:

```text
Player X
TARGET at ADP >= 58
NEUTRAL 49–57
AVOID at ADP <= 48
```

Targets must be price-sensitive.

---

# 28. Avoids section

An avoid is not necessarily a bad player.

Distinguish:

```text
bad player thesis
bad price thesis
fragile projection thesis
poor archetype thesis
opportunity-cost thesis
```

Output:

```text
Why the market likes him
Why the research is skeptical
What price would remove the concern
```

---

# 29. Dart Throws

Late-round players should be scored differently from early picks.

Candidate score:

```python
dart_score = (
    0.30 * upper_tail_score
    + 0.20 * contingent_role_score
    + 0.15 * breakout_archetype_score
    + 0.15 * role_ambiguity_score
    + 0.10 * offense_score
    + 0.10 * youth_or_prior_ceiling_score
)
```

These are starting weights only. Backtest them.

Prioritize path to a 90th-percentile outcome over safe but replaceable floor.

---

# 30. Current tiers / research board

This is not intended to replace the user's draft app.

It is a research appendix answering:

> How do the research signals map onto the current market?

Columns:

```text
player
position
ADP
projection rank
tier
evidence-adjusted value
strong signals
weak signals
target / neutral / avoid
```

---

# 31. Additional research after the mirrored sections

Most of this section follows the core guide. **Two items do not: §31.1 and §31.2 are MVP-critical**, because §19.4 (opportunity cost), §36.2 (the draft simulator), and the draft-day sheet (§83) all depend on pick distributions and survival probability. r1 scheduled them in phase 3, which inverted the dependency. Build them with the foundations.

## 31.1 ADP uncertainty — **MVP**

If raw draft data exists, store mean, median, standard deviation, P10/P25/P75/P90, and sample size.

**Resolve the source question in week one.** The entire section, plus §19.4, §31.2, §31.3 and §36.2, depends on whether Fantasy Football Calculator exposes an underlying pick distribution or only a mean ADP per player per snapshot, and whether historical ADP is retrievable as intra-summer snapshots or a single end-of-preseason value per season. r1 flagged this uncertainty and then scheduled the dependent work anyway. Answer it before committing to the chapters that need it, and if distributions are unavailable, fall back to the labelled normal approximation in §19.4 and record the limitation on every affected page.

## 31.2 Will the player make it back? — **MVP**

Empirical method:

```python
P(available_at_next_pick) = count(pick > next_pick) / count(all_drafts)
```

If only mean/SD exist, use a distribution approximation and label it as an approximation.

## 31.3 Recency-weighted ADP

Possible weighting:

```python
weight = exp(-lambda * age_in_days)
weighted_adp = sum(weight_i * pick_i) / sum(weight_i)
```

Research whether recency weighting improves next-draft prediction.

## 31.4 Platform differences — **deferred**

Compare platform-specific price behavior when data permits.

Fails the decision-value gate (§2.3) for a drafter whose leagues sit on known platforms: the relevant ADP is the one from the platform being drafted on, which is obtained directly rather than inferred from cross-platform comparison. Retain the question in the registry; do not build it.

## 31.5 Historical comparable players

Use methodology-specific normalized feature vectors, weighted distances, and algorithmic similarity.

Show successes **and failures** among close comps.

## 31.6 Evidence leaderboard

Rank active signals by evidence grade, out-of-sample lift, sample size, ADP independence, and recommendation weight.

## 31.7 Failed / retired signals

Never delete failed ideas. Preserve why they were retired and in which edition.

---

# 32. Current-player matching engine

Every methodology should define eligibility and match strength.

Example YAML:

```yaml
method_id: rb_dead_zone_exception

eligibility:
  position: RB
  adp_min: 30
  adp_max: 72

signals:
  age_le_24:
    weight: 1
  target_share_ge_08:
    weight: 3
  experience_le_2:
    weight: 1
  strong_offense:
    weight: 1

negative_signals:
  target_share_lt_04:
    weight: -3
  age_ge_28:
    weight: -2
```

Weights should be generated/tuned from research where possible.

Player application states:

```text
STRONG MATCH
PARTIAL MATCH
NEAR MATCH
FAILS MODEL
INSUFFICIENT DATA
```

Show the exact fields that produced the classification.

---

# 33. Historical successes and failures

For every major methodology select 5–10 strongest historical successes and 5–10 strongest failures algorithmically.

Example:

```text
players with highest methodology match score
then split by actual outcome
```

Each historical card shows:

```text
season
player
ADP
method match score
input features
actual finish / PPG
why it succeeded/failed relative to model
```

This prevents cherry-picking only memorable winners.

---

# 34. False Friend current-player concept

Create a special component for players who look like a methodology match at first glance but fail a key condition.

Example:

```text
FALSE FRIEND

Player Y

Looks like:
✓ projected lead RB
✓ middle-round ADP

But:
✗ no receiving role
✗ age profile historically weak
✗ backfield role already concentrated
✗ offense below scoring baseline
```

---

# 35. Baseline statistical models

Prefer transparent baselines before ML.

## Binary breakout

```text
logit(P(breakout)) =
β0 + β1*normalized_ADP + β2*feature_1 + β3*feature_2 + ...
```

Always fit:

```text
Model A: ADP only
Model B: ADP + candidate signal
```

Report incremental value.

Metrics:

```text
log loss
Brier score
ROC AUC as secondary
calibration
lift in top probability deciles
```

Do not rely on AUC alone.

## Continuous future PPG

```text
future_ppg = β0 + β1*ADP + β2*prior_ppg + β3*candidate_signal
```

Metrics:

```text
MAE
RMSE
Spearman rank correlation
```

---

# 36. Signal independence test

A key question is:

> Does this feature tell us anything that ADP did not already know?

For every candidate feature run:

```text
baseline model:
Outcome ~ ADP

extended model:
Outcome ~ ADP + feature
```

Compare out-of-sample log loss / Brier / MAE / calibration as appropriate.

Then label:

```text
Independent signal: YES
Independent signal: SMALL
Independent signal: NO
```

This is critical because many exciting football metrics are already incorporated into market price.

---

# 36.1 Decision value

Signal independence (§36) establishes that a feature carries information ADP lacks. It does not establish that the information is worth anything. A signal can be independent, well-powered, and still never change a pick — because it fires on players already taken, or at picks the drafter does not hold, or in the same direction the projection already pointed.

**Decision value is the metric the research agenda is ordered by** (§2.3, §18).

### Definition

```python
decision_value = frequency × magnitude

frequency = expected number of picks per draft where the signal is
            (a) applicable to an available player, and
            (b) strong enough to change the selection

magnitude = expected points gained on those picks, relative to the
            choice that would have been made without the signal
```

### Estimation

Estimate both terms from the draft simulator (§36.2) rather than analytically, since both depend on draft slot, league size, and roster construction:

```text
1. Run N simulated drafts with the signal disabled.
2. Run N simulated drafts with the signal enabled, same seeds.
3. frequency = mean count of picks where the two boards diverge.
4. magnitude = mean projected-value delta on the diverging picks only.
5. Report the product, with a bootstrap CI.
```

Diluting the magnitude across all picks rather than the diverging ones understates the value of a rare, high-impact signal; measure it on the picks where it actually acts.

### Use

```text
decision_value HIGH    → eligible for Targets, Avoids, draft-day sheet
decision_value MEDIUM  → shown on player pages, not on the sheet
decision_value LOW     → research chapter only, excluded from recommendations
```

A Grade-A signal with low decision value stays in the guide as research and is kept out of the recommendation layer. §4 already requires a `practical importance` label; this section supplies the number behind it.

Record the estimate in the question registry before building (a rough hand estimate is acceptable as a gate) and replace it with the simulated value once the method exists.

---

# 36.2 Draft-simulation backtest

**This is the governing evaluation of the entire project.**

Every other test in this document operates at the player level: does feature X predict outcome Y beyond ADP? The actual decision is a *sequence* of picks under constraints — a draft slot, a roster to fill, opponents removing players, and positional scarcity that shifts as the board empties. Player-level lift does not imply strategy-level gain, and §64.10 already states this without providing a way to test it. This section provides it.

### The test

```text
Board A: pure ADP
Board B: ADP adjusted by the research signals and evidence weights
Board C: projections only, no research signals    (control)

For N = 2000 simulated drafts:
  - opponents select by ADP with realistic noise
  - the test team drafts from Board A, B, or C at a fixed slot
  - roster requirements from the real league profile (§14)

Outcome per draft:
  expected roster points, using held-out actual outcomes for
  historical seasons and projection distributions for the current one
```

Report the Board B minus Board A delta with a bootstrap confidence interval, per draft slot.

### Opponent model

Opponents pick by ADP with noise calibrated to observed pick dispersion (§31.1), with positional-need behavior applied — an opponent with two rostered running backs is measurably less likely to take a third. A pure-ADP opponent model is too easy to beat and will overstate the strategy's value. Where pick distributions are unavailable, use the labelled normal approximation and mark the entire simulation as approximate.

### Historical backtest

Run the same simulation on completed seasons using the ADP and features knowable at that season's decision date (§6.1), scored against actual outcomes. This is the only end-to-end out-of-sample evidence the project can produce, and it is worth more than any individual method's holdout result.

### Acceptance

```text
If Board B does not beat Board A out of sample,
the research does not change the draft process,
regardless of how many Grade-A signals were produced.
```

That outcome is publishable and belongs in the guide. It would mean the signals are real but already priced, or real but too rare to act on — which is the single most valuable thing the project could learn, and the reason §64.8 sits where it does.

### Secondary outputs

The simulator also produces, at no extra cost:

- decision-value estimates for every signal (§36.1);
- empirical opportunity cost by slot and round, validating §19.4;
- the price thresholds that make Targets and Avoids price-sensitive (§27, §28);
- positional run behavior, which informs the draft-day sheet (§83).

### Limitations to state

The simulator scores a projected roster, not a season of head-to-head results, so it omits in-season management, waivers, and schedule luck. It is a comparison of drafting processes under a fixed evaluation, which is the right scope, but it should not be described as a simulation of winning a league.

---

# 37. Missing-data policy

Every feature must define:

```text
coverage by season
coverage by position
missing rate
imputation policy
```

Never silently fill unavailable advanced metrics with zero.

Allowed states:

```text
observed
derived
imputed
unavailable
```

---

# 38. Projection handling

Because projections are externally sourced, separate them into:

```text
RAW PROVIDER PROJECTION
DERIVED FANTASY POINTS
RESEARCH ADJUSTMENT
```

Do not silently modify the raw projection.

Example:

```text
Provider projection: 212.4
Research adjustment: +8.7
Research fair-value estimate: 221.1
```

If a methodology is not strong enough to alter a projection, it should remain an annotation only.

---

# 38.1 Provider projection uncertainty

Every "research fair value" in the guide is a provider projection plus an adjustment. The provider number carries error, and because historical preseason projection archives are unavailable (§66), **that error cannot be measured.** This is a large unquantified uncertainty sitting underneath every recommendation, and r1 did not acknowledge it.

It cannot be eliminated. It can be bounded and displayed.

### Minimum viable treatment

Ingest two or three providers for the current season and use their **dispersion** as a crude uncertainty proxy:

```python
provider_spread   = max(points_i) - min(points_i)
provider_cv       = stdev(points_i) / mean(points_i)
provider_agreement = "high" if provider_cv < 0.08 else
                     "medium" if provider_cv < 0.15 else "low"
```

Store one row per provider (§13) — never an averaged row, which destroys the only signal available here.

### Display

The player page (§74) and the draft-day sheet (§83) show agreement alongside the projection:

```text
Projection      212.4   (FantasyPros)
Provider range  186 – 241 across 3 providers
Agreement       LOW
```

### Use

```text
LOW agreement    → widen the recommended price band; suppress any
                   recommendation whose thesis rests on the projection
                   rather than on a research signal
MEDIUM           → normal treatment, display the range
HIGH             → normal treatment
```

Low agreement is itself information. It usually marks an unresolved role — the exact situation where the research signals in §21.5, §22.5, and §86 have the most to say, and where a point projection has the least.

### Limitation to state on every page

Provider dispersion measures disagreement, not accuracy. Three providers can agree closely and all be wrong, particularly where they share upstream inputs. This proxy sets a floor on uncertainty, never a ceiling. When historical projection archives become available, replace it with measured error and mark the change as a new methodology version.

---

# 39. Recommendation weighting

Do not assign every signal equal weight.

Recommended process:

1. assign evidence grade;
2. estimate out-of-sample incremental value;
3. convert to recommendation weight;
4. cap correlated signals so they do not double-count the same information.

Example:

```text
Target share
Evidence: A
Weight: high

Year-2 status
Evidence: C
Weight: low

Strong offense
Evidence: B
Weight: medium
```

---

# 39.1 Effect-size shrinkage

Raw effect estimates from small samples are biased away from zero: the noisiest estimates are the most extreme ones, and taking the largest observed effects at face value systematically overstates them. With the sample sizes in §5.1 this is not a subtle correction — it is the difference between a usable weight and an inflated one.

r1 handled this implicitly, by multiplying effects by an evidence-grade weight (§56). That double-counts, because the effect size already encodes magnitude while the grade encodes confidence, and the multipliers were invented. Shrinkage does the intended job properly and removes the invented constants.

### Method

Apply empirical-Bayes shrinkage across the family of effect estimates within a method (or across comparable methods, where the family is too small):

```python
# tau2 = between-effect variance, estimated across the family
# se_i = standard error of effect i

shrinkage_i = tau2 / (tau2 + se_i**2)
effect_shrunk_i = grand_mean + shrinkage_i * (effect_raw_i - grand_mean)
```

Properties this gives, all of them desirable and none requiring a hand-chosen constant:

- a precise estimate from a large sample barely moves;
- a noisy estimate from a small sample is pulled hard toward the family mean;
- a single spectacular result from n=40 stops dominating the recommendation layer;
- the amount of shrinkage is derived from the data rather than asserted.

### Reporting

Both values appear on every evidence card and in every artifact:

```text
Raw effect:    1.42×
Shrunk effect: 1.24×
Shrinkage:     0.57   (moderate — small holdout sample)
```

Recommendation weights (§39) and any composite score (§56) use **shrunk** effects exclusively. Raw effects are displayed for transparency and never used in a calculation that reaches a draft recommendation.

### Note on the grade

The evidence grade still governs eligibility — a Grade-D signal is excluded from recommendations entirely regardless of its shrunk effect. Shrinkage governs magnitude among the signals that qualify. The two mechanisms are separate and should not be multiplied together.

---

# 40. Evidence card UI

Reusable component:

```text
┌────────────────────────────────────────────┐
│ RECEIVING INVOLVEMENT                      │
│ Evidence: A — Strong                       │
│                                            │
│ Historical lift: +11.2 percentage points  │
│ Risk ratio: 1.74×                          │
│ 95% CI: 1.29–2.33                          │
│ n = 286                                    │
│                                            │
│ Independent of ADP? Yes                   │
│ Holdout: replicated                        │
│ Recommendation weight: HIGH               │
│                                            │
│ [View data] [Methodology] [Failures]       │
└────────────────────────────────────────────┘
```

---

# 41. Static-site UX

Desktop:

```text
┌─────────────────────────────────────────────────────────────┐
│ 2026 DRAFT RESEARCH GUIDE     Edition 2026.08.11-r3  Search │
├───────────────┬─────────────────────────────────────────────┤
│ Foundations   │ Running Back Dead Zone                      │
│ QB            │                                             │
│ RB            │ One-minute summary                          │
│ WR            │                                             │
│ TE            │ [evidence card]                             │
│ Teams         │                                             │
│ Targets       │ [interactive chart]                         │
│ Avoids        │                                             │
│ Darts         │ Historical successes / failures             │
│ Evidence      │                                             │
│ Appendix      │ Current-season matches                      │
└───────────────┴─────────────────────────────────────────────┘
```

Mobile:

- collapsible section nav;
- readable single column;
- sticky edition indicator;
- horizontally scrollable tables;
- responsive charts.

---

# 42. Global site features

Must have:

```text
full-text search
player search
methodology search
edition selector
evidence-grade filter
position filter
show technical details toggle
```

Useful:

```text
copy deep link
download chart data as CSV
copy methodology formula
print chapter
```

---

# 43. Chapter navigation

Use a research-site model, not literal page-turn animation.

Provide:

```text
left navigation
breadcrumbs
Previous chapter
Next chapter
On this page
Related methodologies
```

---

# 44. Search behavior

Search index should include chapter titles, method names, player names, signals, formula terms, historical examples, and current recommendations.

---

# 45. Source transparency

Every chart/table must be traceable to a research artifact.

Footer example:

```text
Data:
nflverse player stats
Fantasy Football Calculator ADP
projection snapshot: FF Today 2026-08-06

Research:
rb_dead_zone v2.1.0

Edition:
2026.08.11-r3
```

---

# 46. Source / license registry

Create `config/sources.yaml`.

Example:

```yaml
nflverse:
  url: https://github.com/nflverse/nflverse-data
  purpose:
    - play_by_play
    - player_stats
    - team_stats
    - rosters
    - snap_counts
  license: CC-BY-4.0
  attribution_required: true

fantasy_football_calculator:
  url: https://help.fantasyfootballcalculator.com/article/42-adp-rest-api
  purpose:
    - historical_adp
    - current_adp
  attribution_required: true

sleeper:
  url: https://docs.sleeper.com/
  purpose:
    - optional_raw_draft_picks
  commercial_use: contact_provider
```

This is a technical registry, not legal advice. Review current source terms before public/commercial distribution.

---

# 47. Build pipeline

High-level command:

```text
research build --season 2026 --edition 2026.08.11-r3
```

Stages:

```text
1. fetch/import raw data
2. validate source snapshots
3. normalize IDs
4. build player-week dataset
5. build player-season dataset
6. build team-season dataset
7. calculate fantasy scoring
8. run research modules
9. run robustness / validation
10. assign evidence grades
11. apply methods to current player pool
12. generate targets/avoids/darts
13. export research JSON
14. build static Astro site
15. run validation tests
16. write edition manifest
17. publish to dist/
```

---

# 48. Reproducibility

Every generated number should be reproducible from:

```text
edition manifest
source snapshot
configuration
research code commit
method version
```

Use deterministic seeds for models / bootstrap procedures.

---

# 49. Research module interface

Suggested Python interface:

```python
class ResearchMethod:
    method_id: str
    version: str

    def population(self, datasets, config):
        ...

    def compute(self, population, config):
        ...

    def validate(self, results, datasets, config):
        ...

    def grade_evidence(self, validation):
        ...

    def current_application(self, current_players, results, config):
        ...

    def export(self):
        ...
```

This makes new chapters plug-ins rather than one-off notebooks.

---

# 50. Research should not live only in notebooks

Notebooks are useful for exploration.

Production findings should be converted to a version-controlled Python module, test, config, and artifact.

A notebook may discover the signal. The published guide should be generated from production code.

---

# 51. Test suite

## Data tests

```text
no duplicate player-week keys
valid player IDs
valid seasons
ADP within plausible ranges
snap share 0–1
target share 0–1
```

## Research tests

```text
population count matches expected
holdout never enters training
confidence intervals calculate
```

## Leakage tests (§6.1)

These are assertions in the feature builder, not review checklist items:

```text
every feature row carries a non-null as_of
every feature as_of <= the season's decision date
no outcome-flagged column appears in any feature frame
no player_season aggregate joins onto a preseason population
provider revisions do not backfill a prior snapshot
```

A violation fails the build. r1's "outcomes do not use future data" could not be implemented as a test because nothing recorded when a value became knowable; §6.1 supplies that field, which turns the intent into an executable check.

## Grading tests (§3.1)

```text
evidence grades reproduce exactly from evidence_rules.yaml
no grade is assigned by hand anywhere in the codebase
grade thresholds unchanged since the committed date, or a
  methodology_version bump and update-tracker entry exists
family_size and FDR q recorded for every graded result
MDE recorded for every method, including null results
UNANSWERABLE methods produce no effect estimate
```

## Evaluation tests (§36.2)

```text
simulator reproduces a known-ADP board exactly when signals are disabled
Board A vs Board B comparison uses identical seeds
historical simulation uses only decision-date-knowable features
```

## Site tests

```text
every methodology page builds
every chart artifact exists
every current player link resolves
edition manifest present
no source without attribution metadata
```

---

# 52. Publication-quality chart standards

Every evidence chart must show:

```text
title
plain-English subtitle
sample size
axis labels
outcome definition
source
edition
```

Where relevant add confidence intervals, baseline lines, and ADP baseline.

Avoid decorative charts with no analytical purpose.

---

# 53. What the data says vs What we do

Every chapter should end with two visually distinct boxes.

## WHAT THE DATA SAYS

Example:

> Historically, this player profile produced high-end RB seasons more frequently than the rest of the same ADP range.

## HOW WE USE IT

Example:

> Treat this as a medium-weight positive signal. It is not strong enough to override a two-tier projection gap.

This is mandatory.

---

# 54. Recommendation conflict handling

A player can have contradictory signals.

Example:

```text
+ Strong target-share profile
+ Young
- Poor offense
- High price
```

Do not hide conflict inside a single score.

Show positive evidence, negative evidence, net recommendation, and uncertainty.

---

# 55. Current-season recommendation taxonomy

Use:

```text
STRONG TARGET
TARGET
NEUTRAL / FAIR PRICE
AVOID AT COST
STRONG AVOID AT COST
DART THROW
```

Always include a price condition when possible.

---

# 56. Evidence-aware player score

**A composite is optional, secondary, and never the primary display.** §54 argues against collapsing conflicting signals into one number, and that argument does not weaken because a score is convenient. The signal-by-signal view with positive and negative evidence shown separately remains the primary presentation on every player page.

Where a single number is genuinely needed — sorting the research board (§30), ranking dart candidates (§29) — compute it as follows.

### Formula

```python
signal_contribution_i = (
    standardized_signal_i        # z-scored within position and ADP band
    * effect_shrunk_i            # §39.1 — shrunk, never raw
    * eligible_i                 # 1 if grade >= C and decision_value != LOW, else 0
)

factor_contribution_f = cap(
    sum(signal_contribution_i for i in factor f),   # §57
    limit = factor_cap[f]
)

research_score = sum(factor_contribution_f)
```

### What changed from r1 and why

r1 multiplied the effect by an evidence weight on an invented A=1.00 / B=0.65 / C=0.30 ladder. Two problems: the effect size already carries magnitude while the grade already carries confidence, so multiplying them double-counts; and the ladder's values were asserted rather than derived.

The replacement separates the two jobs cleanly:

```text
evidence grade  → eligibility (a gate: in or out)
shrinkage       → magnitude   (derived from each estimate's own precision)
factor caps     → prevents correlated signals stacking (§57)
```

No hand-chosen constants survive except the factor caps, which are structural rather than statistical.

### Required display

Wherever the score appears, its decomposition appears with it — the contribution of each factor, and the signals inside each factor. A score whose components cannot be inspected is exactly the "unexplained score" that §80 prohibits.

### Calibration

Validate the score against the draft simulator (§36.2), not against player outcomes. A score that correlates well with player outcomes but does not improve drafted rosters has not demonstrated anything useful.

---

# 57. Avoiding double-counting

Metrics like targets/game, target share, and receptions/game are related.

Do not count them as three independent strong signals.

### Declare the factor taxonomy up front

r1 offered three possible remedies without choosing one, which defers the decision to the moment it is hardest to make objectively — after the signals exist and their contributions are visible. Fix the taxonomy in configuration before any signal is built:

```yaml
factors:
  opportunity:      # volume and role
    cap: 1.0
    signals: [target_share, snap_share, rush_share, air_yard_share,
              red_zone_share, route_participation]
  efficiency:       # per-touch production
    cap: 0.6
    signals: [yards_per_route, yards_per_carry, tds_per_touch, catch_rate]
  situation:        # team and environment
    cap: 0.6
    signals: [team_pass_rate, team_scoring, backfield_ambiguity,
              teammate_hierarchy, qb_quality]
  profile:          # player attributes
    cap: 0.5
    signals: [age, experience, draft_capital, athletic_profile]
  market:           # price behavior
    cap: 0.4
    signals: [adp_movement, ecr_adp_divergence, expert_dispersion]
```

Caps are on total contribution per factor. Targets per game, target share, and receptions per game all live in `opportunity` and jointly cannot exceed its cap, which is the concrete fix for the problem this section identifies.

Efficiency is capped lower than opportunity deliberately: efficiency metrics regress harder (§19.1) and are more sample-dependent, so the structural prior against them is justified independently of any single season's estimates.

Use regularization *within* factors where several signals overlap heavily, and report the grouped factor scores alongside the underlying metrics on every player page.

---

# 58. Planned edition home page

```text
2026 Fantasy Football Draft Research Guide
Edition 2026.08.11-r3

Data through: August 11, 2026
Historical seasons: 2012–2025

ACTIVE RESEARCH
16 methodologies built / 9 unanswerable / 4 retired
1 Grade-A signal
4 Grade-B signals
7 Grade-C signals
4 exploratory
178 tests run · 11 survived FDR correction
Draft simulation: +4.1 pts vs ADP board (95% CI −1.2 to +9.6)

CURRENT MARKET
12 Targets
9 Avoid-at-Cost
14 Late-Round Darts

WHAT CHANGED
RB Dead Zone boundary moved 4 picks later
Two target recommendations changed due to ADP
Year-2 WR signal downgraded from B to C

[Start with Foundations]
[Browse Current Players]
[View Evidence Leaderboard]
```

Counts are generated dynamically.

The example above is deliberately unglamorous, and r1's version — six Grade-A and eight Grade-B signals — is not a realistic target at these sample sizes. A home page that advertises an outcome the data cannot produce creates steady pressure to loosen the grading rules, which is precisely what §3.1 exists to prevent. Surfacing the unanswerable count, the total tests run, and a simulation result whose confidence interval crosses zero is the honest version, and it is also the more useful one for a reader deciding how much weight to place on anything inside.

---

# 59. MVP implementation scope

Do not build all chapters first.

First make the research framework work end-to-end.

### Gates — before any method is written

```text
G1. Real league profiles encoded                        (§14)
G2. Decision dates set, as_of assertions in the builder (§6.1)
G3. evidence_rules.yaml committed with numeric thresholds (§3.1)
G4. Power analysis run for every registry question;
    UNANSWERABLE chapters marked and removed             (§5.1)
G5. FDR protocol and primary outcomes pre-registered     (§5.2)
G6. FFC pick-distribution question resolved              (§31.1)
```

G4 is expected to remove roughly a third of §18. Doing it first is what makes the rest of the MVP fit.

## MVP methods

### Foundation
1. Regression (§19.1)
2. Rankings / Tiers (§19.3)
3. ADP distributions and survival probability (§31.1, §31.2) — **promoted from phase 3**; §19.4 and §36.2 depend on it
4. Opportunity Cost (§19.4)

### RB
5. RB Dead Zone (§21.1)
6. Dead-Zone Exceptions (§21.2)

### WR
7. Young / Year-2 WR Breakouts (§22.3)

### Evaluation — not optional
8. Draft-simulation backtest (§36.2)

### Current
9. Targets / Avoids from those methods
10. Evidence leaderboard
11. Draft-day sheet (§83)
12. Recommendation audit trail (§76) — **promoted**; it is a persisted JSON write, costs almost nothing now, and is the only mechanism that ever calibrates the evidence grades

Item 8 is listed as a method because it is one. Without it the MVP can report that signals exist but cannot report whether acting on them helps, which is the question the project was built to answer.

These methods solve most infrastructure questions:

```text
historical data
ADP
current projections
outcome definitions
statistics
confidence intervals
holdouts
current matching
evidence grading
charts
static publication
versioning
```

---

# 60. Phase 2

Add:

```text
variance / range of outcomes
QB rushing
pocket passer ceiling
middle/late RB breakout research
backfield ambiguity
rookie WR development
late WR breakouts
TE breakout analysis
team regression
darts
```

---

# 61. Phase 3

Add original extensions:

```text
recency-weighted ADP
historical comps
failed-signal archive
research-score calibration against the simulator
in-season extension                          (§87)
schedule and roster-construction factors     (§85)
```

Removed from phase 3 in r2:

```text
ADP distributions   → MVP  (§31.1) — opportunity cost depends on it
player survival     → MVP  (§31.2) — asked at every pick on draft day
platform differences → cut (§31.4) — fails the decision-value gate
```

---

# 62. Data acquisition plan for the MVP

## Historical football stats

Use nflverse / nflreadpy.

Target period:

```text
2012–2025
```

## Historical ADP

Use Fantasy Football Calculator REST API.

Pull season, scoring format, 12-team configuration, and all positions.

Store raw JSON before normalization.

## Historical expert rankings

Optional but recommended:

```text
nflverse load_ff_rankings(type="all")
```

## Current projection

MVP should support two ingestion paths:

```text
preferred:
FantasyPros Public API using FANTASYPROS_API_KEY

fallback:
manual normalized CSV
```

Provider can be FantasyPros, FF Today, ESPN, another source, or the existing draft app.

For FantasyPros API ingestion, freeze the returned API response inside the edition snapshot before normalization.

## Current ADP

Use Fantasy Football Calculator plus existing draft-app ADP if already available.

Do not average sources blindly. Store each source independently.

---

# 63. Data-source priority matrix

| Need | Preferred free source | Alternative | Confidence |
|---|---|---|---|
| Play-by-play | nflverse | — | High |
| Weekly stats | nflverse | — | High |
| Seasonal stats | nflverse | derive from weekly/PBP | High |
| Team stats | nflverse | derive from PBP | High |
| Rosters / age | nflverse | — | High |
| Draft capital | nflverse | — | High |
| Combine | nflverse | — | High |
| Snap counts | nflverse | — | High |
| Injuries | nflverse | — | Medium/High |
| Target share | derive from nflverse | nflverse derived fields | High |
| Air-yard share | derive / nflverse | — | High |
| Complete routes run | **No ideal long free source** | licensed provider later | Low |
| Historical ADP | Fantasy Football Calculator | other platform archives | High |
| Historical ECR | nflverse/DynastyProcess FantasyPros archive | — | Medium/High |
| Raw draft picks | Sleeper API if corpus available | own draft history | Medium |
| Current projections | FantasyPros Public API with API key | manual FantasyPros / FF Today / ESPN import | High when API access is configured |
| Projection *error* | **No source exists** — no free preseason archive | cross-provider dispersion as a proxy (§38.1) | Low |
| ADP pick distributions | FFC if exposed; own draft logs | normal approximation, labelled (§19.4) | **Unresolved — verify week one** |
| Depth charts | nflverse depth charts | manual preseason capture (§86) | Medium |
| Bye weeks / schedule | nflverse schedules | — | High |
| Injury history | nflverse injuries | — | Medium |
| Camp / role news | **No structured free source** | manual notes, logged with as_of (§86) | Low |

---

# 64. Important research limitations to state upfront

1. ADP is source-specific.
2. Public historical projection archives are much harder to obtain than historical NFL stats.
3. Free routes-run history is incomplete relative to paid charting datasets.
4. Training-camp information is not captured well by historical stat models.
5. Injuries create censoring and role changes.
6. NFL strategy changes by era.
7. Small position samples can make apparent effects unstable.
8. ADP itself already incorporates public information, so many candidate signals will add little after controlling for ADP.
9. Historical associations do not prove football causality.
10. A model that predicts player outcomes does not automatically prove a draft strategy because opportunity cost matters.
11. Fantasy outcomes are the product of availability and per-game production; the first is largely unpredictable from these features and will suppress every evidence grade unless the two are modelled separately (§15.1).
12. Provider projection error cannot be measured without a historical projection archive, so a large uncertainty underneath every fair-value estimate remains unquantified (§38.1).
13. Sample sizes are small enough that most chapters in the original outline cannot detect a plausible effect at all; power is a gate, not a footnote (§5.1).
14. Roughly 150 hypothesis tests across the project imply about eight false positives by chance alone, which is approximately the number of strong signals a naive process would report (§5.2).

**Items 8 and 10 are the governing constraints of the entire project.** ADP already prices public information, and predicting players is not the same as drafting better. Every chapter should be read as an attempt to find the narrow space where both objections fail, and most attempts should be expected to fail honestly.

---

# 65. Data-snapshot rules

Raw data should be immutable.

Example:

```text
data/snapshots/2026-08-11/
  ffc_adp_half_ppr_12.json
  current_projections.csv
  current_injuries.parquet
  manifest.json
```

Manifest should store retrieval time, source, hashes, and notes.

Research reruns within an edition should use the same snapshot.

---

# 66. Current projections and historical backtests

If archived preseason projections for historical seasons are unavailable, do not fake them.

For historical expectation use historically observable data such as:

```text
ADP
ECR
prior-year statistics
```

When historical projection archives become available, add them as a new methodology version.

---

# 67. Fair baseline requirement

Every breakout archetype should be compared against players at similar ADP.

Bad:

```text
Young WRs broke out 25% of the time.
```

Better:

```text
Young WRs in ADP 50–100 broke out 25%.
All WRs in ADP 50–100 broke out 14%.
Absolute lift: +11 points.
Relative risk: 1.79×.
```

Best:

```text
After adjusting for ADP continuously, youth retained X incremental predictive value in the holdout sample.
```

---

# 68. Research question registry

Create `research/questions.yaml`.

Example:

```yaml
- id: wr_year2_breakout
  question: >
    Among WRs with similar preseason ADP, do second-year players
    produce high-end fantasy outcomes more frequently?

  # pre-registration (§5.2) — set before analysis, changing these
  # requires a method version bump and an update-tracker entry
  primary_outcome: wr_breakout_top24
  secondary_outcomes:
    - wr_breakout_ppg15
    - wr_beat_adp
  family_size_expected: 6

  # decision-value gate (§2.3, §36.1)
  decision_change: >
    Whether to take the second-year WR over the similarly priced
    veteran WR in rounds 5-8.
  decision_frequency_est: 2.0        # picks per draft
  decision_magnitude_est: 0.9        # points per affected pick
  decision_value: MEDIUM

  # power gate (§5.1) — populated by the feasibility pass, before build
  n_analysis_est: 240
  n_holdout_est: 70
  baseline_rate: 0.17
  plausible_effect_pp: 8.0
  mde_pp: 6.4
  power_status: ANSWERABLE           # ANSWERABLE | UNANSWERABLE

  # scheduling
  effort_hours_est: 14
  kill_rule: >
    Stop if the ADP-adjusted univariate effect is below 2pp with the
    full population; do not proceed to the multivariable stage.

  status: active
  owner: public_methodology
  first_edition: 2026.07.15-r1
```

This prevents research from becoming ad hoc, and the added fields do three specific jobs: `primary_outcome` and `family_size_expected` make the multiple-comparison correction honest, `decision_*` and `power_status` decide whether the question is built at all, and `effort_hours_est` plus `kill_rule` give a solo build an explicit stopping condition instead of an open-ended one.

**Every question carries a kill rule.** Without one, an underpowered chapter absorbs unbounded time and then gets graded C rather than abandoned.

---

# 69. Method history

Each method should have a version history.

Example:

```text
rb_dead_zone

v1.0
fixed ADP 37–72 definition

v2.0
data-driven rolling window

v2.1
added WR opportunity-cost comparison
```

An edition stores exactly which version it used.

---

# 70. Public/general vs custom methods — **deferred**

Architecture must allow custom future methods without mixing them into public/general research.

The metadata field below is sufficient for now. The folder split is premature abstraction for a single-maintainer project with no external contributors and can be introduced later without cost, since the `ResearchMethod` interface (§49) already makes methods relocatable.

```text
research/public/     # later
research/custom/     # later
```

Metadata:

```yaml
method_origin: public_general
```

or:

```yaml
method_origin: custom
```

---

# 71. Method status

Every methodology has:

```text
ACTIVE          in use, graded, may influence recommendations
EXPERIMENTAL    built, exploratory only, may not influence recommendations
RETIRED         withdrawn after previously being active
FAILED          tested with adequate power, did not replicate
UNANSWERABLE    sample cannot detect a plausible effect; never built  (§5.1)
```

`UNANSWERABLE` is distinct from `FAILED` and the difference matters. A failed method was tested and the answer was no. An unanswerable question was never testable, and recording it as failed would falsely imply evidence against it. Store the sample size that would be required, so the question can be revisited if a data source ever makes it viable.

Do not remove retired methods from old editions.

---

# 72. Definition glossary

Create plain-English and technical definitions for:

```text
ADP
ECR
target share
air-yard share
PPG
VOR
breakout rate
risk ratio
odds ratio
confidence interval
calibration
holdout sample
regression to the mean
z-score
tier
opportunity cost
```

Use hover/tooltips plus a full glossary page.

---

# 73. Methodology detail toggle

Each chapter has:

```text
Reader view
Technical specification
```

Technical section contains dataset, filters, formula, features, model, validation, and code identifier.

---

# 74. Current-player page

Every relevant current player gets a page.

Example:

```text
PLAYER X — RB

Market
ADP: 54
Position ADP: RB22

Projection
Provider: ...
Projected points: ...

Research signals
A  Receiving involvement      Positive
B  Backfield ambiguity        Positive
B  Team environment           Positive
C  Youth/experience           Positive
A  ADP value                  Neutral

Methods matched
RB Dead-Zone Exception
Middle-Round RB Breakout

Historical comparable profiles
...

Research conclusion
TARGET AT 58+
```

---

# 75. Historical-player page

Useful for examples.

```text
PLAYER X — 2022

Preseason state
ADP
age
experience
team
prior stats

Method matches
...

Actual outcome
PPG
finish
games

Used in:
RB Dead Zone
Receiving Involvement
Historical Failure Example
```

---

# 76. Recommendation audit trail — **MVP**

Promoted from a late-stage item in r1. It is a persisted JSON write of data the pipeline already produces, it costs almost nothing to add now, it cannot be reconstructed later, and it is the only mechanism by which the evidence grades in §3.1 are ever checked against reality. Build it in the first edition even if that edition contains three methods.

For every target/avoid store:

```text
edition
player
market price
recommendation
method contributions
evidence grades
```

After the season, generate a review of what worked and failed and whether failure was model miss vs ordinary variance.

---

# 77. End-of-season review

Create an annual retrospective evaluating targets, avoids, darts, method predictions, calibration, and evidence-grade accuracy. r1 marked this optional; it is not, because it is the feedback loop that makes the evidence grades mean anything after year one.

Required outputs:

```text
calibration curve: predicted breakout probability vs observed rate
grade accuracy:    did Grade-A signals outperform Grade-C signals?
recommendation P&L: targets vs same-ADP baseline players
model miss vs variance attribution for each failed recommendation
simulator check:   did the Board B advantage appear in the real draft?
```

The grade-accuracy check is the important one. If Grade-A signals did not outperform Grade-C signals, the grading rubric in §3.1 is miscalibrated and its thresholds need revision — recorded as a version bump, not a silent edit.

Do not judge only by anecdotes.

---

# 78. Acceptance criteria for the first usable release

**Target date: this must carry one.** An acceptance list without a deadline will be met after the season it was built for. See §88 for the 2026 dates; the full release below targets the offseason build.

### Gates
- [ ] Real league profiles encoded before any research ran.
- [ ] `evidence_rules.yaml` committed with numeric thresholds, dated before first results.
- [ ] Power analysis completed for every registry question; `UNANSWERABLE` items recorded and not built.
- [ ] Primary outcomes pre-registered; FDR correction applied and family sizes reported.
- [ ] `as_of` present on every feature row; leakage assertions run in the build.

### Research
- [ ] At least five methodology chapters are generated from data.
- [ ] Every methodology has exact definitions/formulas.
- [ ] Every methodology has sample size, MDE, and an automatically assigned evidence grade.
- [ ] Major signals have an ADP-adjusted test.
- [ ] Major signals have a rolling-origin holdout.
- [ ] Effects are reported both raw and shrunk.
- [ ] Availability and per-game production are reported separately.
- [ ] Historical successes and failures render automatically.

### Evaluation — the criteria r1 was missing
- [ ] **The draft simulator runs and reports Board B vs Board A with a confidence interval.**
- [ ] **Decision value is estimated for every signal that reaches the recommendation layer.**
- [ ] A signal with low decision value is verifiably excluded from Targets, Avoids, and the sheet.
- [ ] The recommendation audit trail is persisted for the edition.

### Product
- [ ] **A one-page draft-day sheet is produced and is usable during a live draft.**
- [ ] Current-season matches render automatically.
- [ ] Descriptive vs predictive vs prescriptive statements are visually distinct.
- [ ] A reader can expand technical details.
- [ ] Targets/avoids are price-sensitive.

### Infrastructure
- [ ] A 2026 edition builds to static files.
- [ ] The build can be opened without a backend.
- [ ] Historical data comes from documented free sources.
- [ ] Raw source snapshots are preserved.
- [ ] Player IDs are normalized.
- [ ] Search works.
- [ ] Edition metadata is visible.
- [ ] A second 2026 edition can be generated without overwriting the first.
- [ ] Sources and attribution are visible.

---

# 79. Recommended implementation order for another model

**If the current date is inside three weeks of a draft, follow §88 instead of this section.** The order below assumes an offseason build with time to do it properly.

## Step 0 — gates

Before any pipeline code:

```text
encode the real league profiles                      (§14)
set decision dates per season                        (§6.1)
commit evidence_rules.yaml with numeric thresholds   (§3.1)
write questions.yaml with decision_value + kill_rule (§68)
resolve the FFC pick-distribution question           (§31.1)
```

Then run the feasibility pass: estimate `n_analysis`, `n_holdout`, and `mde` for every question, mark the `UNANSWERABLE` ones, and delete those chapters from the plan (§5.1). This typically removes about a third of §18 and is the step that makes everything after it fit.

Do not skip Step 0 because it produces no visible output. It is the only step that prevents months of work on questions the data cannot answer.

## Step 1 — scaffold

Create the Python research package, a Quarto book skeleton, the edition manifest, and config files. Include the `as_of` assertion in the feature builder from the very first table — it cannot be retrofitted.

Do not start with visual polish.

## Step 2 — data ingestion

Implement:

```text
nflverse adapter
FFC ADP adapter
FantasyPros API projection adapter
projection CSV fallback adapter
player ID normalization
```

FantasyPros adapter requirements:

```text
read FANTASYPROS_API_KEY from environment
never log or persist the key
persist raw API payloads
normalize FantasyPros IDs
record endpoint/query metadata
fail cleanly to configured fallback
```

Persist raw snapshots.

## Step 3 — canonical tables

Build player_week, player_season, team_season, adp_history, current_players.

## Step 4 — research framework

Implement ResearchMethod interface, outcome registry, evidence grading, forward validation, and artifact exporter.

## Step 5 — first methodology

Implement **RB Dead Zone** end-to-end.

It should produce:

```text
research JSON
chart JSON
historical successes
historical failures
current matches
evidence grade
site page
```

Do not begin method #2 until this full loop works.

## Step 6 — foundations

Implement Regression, Tiers, ADP distributions and survival probability (§31.1, §31.2), then Opportunity Cost — in that order, since opportunity cost depends on survival.

## Step 7 — WR

Implement Year-2 / young WR breakouts.

## Step 8 — draft simulator

Implement §36.2 and run Board A vs Board B. Do this **before** the recommendation layer, because the simulator supplies the decision-value estimates and price thresholds that the recommendation layer needs, and because a negative result here changes what the recommendation layer should contain.

## Step 9 — recommendation layer

Generate Targets, Avoids, the Evidence Leaderboard, the draft-day sheet (§83), and the audit trail (§76).

## Step 10 — edition/version support

Build two dummy editions to prove no overwrite occurs.

## Step 11 — remaining guide

Expand in decision-value order (§36.1), not in the chapter order of §18.

---

# 80. What not to do

Do not:

```text
copy Late-Round proprietary prose
copy proprietary rankings
hard-code old-guide percentages as eternal truths
call every correlation a useful signal
optimize models using the current season
hide sample sizes
hide failures
use one arbitrary breakout definition
join players only by name
silently scrape projection sites without reviewing terms
overwrite historical editions
make ML the first implementation
collapse every signal into an unexplained score
```

Added in r2:

```text
grade a method that failed the power gate instead of marking it UNANSWERABLE
loosen an evidence threshold after seeing results
assign an evidence grade by hand
use raw effect sizes in any calculation reaching a recommendation
treat a secondary outcome result as a primary finding
report a null result without reporting the minimum detectable effect
build the publication layer before the draft-day sheet exists
average multiple projection providers into one number
compute backfield ambiguity from the same projections used as the baseline
model season totals without separating availability from per-game production
ship a recommendation layer that has never been tested in a draft simulation
let the home page's advertised signal counts drive the grading
```

---

# 81. Source references researched for this plan

## nflverse

```text
https://nflverse.nflverse.com/
https://github.com/nflverse/nflverse-data
https://nflreadpy.nflverse.com/
https://nflreadr.nflverse.com/reference/index.html
https://nflreadr.nflverse.com/reference/load_participation.html
https://nflreadr.nflverse.com/articles/dictionary_participation.html
https://nflreadr.nflverse.com/reference/load_ff_rankings.html
```

## Fantasy Football Calculator

```text
https://help.fantasyfootballcalculator.com/article/34-average-draft-position-adp-data
https://help.fantasyfootballcalculator.com/article/42-adp-rest-api
```

## Sleeper

```text
https://docs.sleeper.com/
```

## Current projection candidates

FantasyPros Public API:

```text
https://www.fantasypros.com/api-data/
https://support.fantasypros.com/hc/en-us/articles/49749297704475-How-do-I-request-access-to-the-FantasyPros-API
```

FantasyPros public projection pages / manual-import fallback:

```text
https://www.fantasypros.com/nfl/projections/qb.php?week=draft
```

Other manual-import candidates:

```text
https://www.fftoday.com/rankings/playerproj.php
https://fantasy.espn.com/football/players/projections
```

---

# 82. Final product definition

The finished system should be thought of as:

> **A versioned, reproducible, static fantasy-football research book generated from data.**

It combines three things:

### 1. Textbook

Explain regression, variance, tiers, opportunity cost, and position-specific strategy.

### 2. Research paper

Show data, definitions, formulas, sample sizes, effect sizes, confidence, validation, failures, and limitations.

### 3. Annual draft guide

Apply the research to current ADP, current projections, current players, targets, avoids, darts, team regression, and current tiers.

The quality bar is not:

> Can the site produce an interesting stat?

The quality bar is:

> **Can another person understand exactly what was tested, reproduce it, see how strong the evidence is, see where it failed, and understand why it does or does not influence the current draft recommendation?**

That should remain the governing requirement for every methodology added to the project.

r2 adds a second bar, which sits above the first:

> **Does drafting on this research produce better rosters than drafting on ADP?**

The first bar is about integrity and is met by documentation. The second is about value and is met only by §36.2. A project that clears the first and fails the second is honest research that should not be used to draft — which is a legitimate and publishable outcome, but it must be stated rather than obscured by the quality of the documentation.

---

# 83. Draft-day sheet

Everything in this document must compress to **one page usable during a live draft**.

The guide is a research publication read in July and August. The sheet is the artifact used during a two-hour event with a 90-second pick clock, where no one opens a methodology chapter. r1 produced no such output, which meant the entire system terminated in a browsable site rather than in a decision aid.

### Contents

```text
TIERS            by position, with tier breaks marked, ADP alongside  (§19.3)
TARGETS          player, price trigger, one-line thesis                (§27)
AVOIDS           player, price trigger, one-line thesis                (§28)
REGRESSION       teams flagged for positive/negative TD regression     (§25)
DARTS            late-round list, ranked                               (§29)
SURVIVAL         P(available) at each pick the drafter actually holds  (§31.2)
FALSE FRIENDS    players who look like a match and are not             (§34)
```

### Constraints

- One page, printable, legible at arm's length.
- Every recommendation carries its price trigger. A target without a price is not usable at a live pick.
- No evidence grades, no confidence intervals, no sample sizes. Those belong in the guide; the sheet carries conclusions and prices only.
- Generated from the same artifacts as the site (§16), never maintained by hand.
- Generated **per league profile**, since tiers, replacement level, and survival probabilities all depend on scoring, team count, and draft slot.

### Priority

The sheet is an acceptance criterion (§78) and precedes the publication layer in build order. If the schedule collapses, the sheet is the deliverable that survives — a one-page output backed by three sound analyses is worth more on draft day than a forty-chapter site that is not finished.

---

# 84. ADP archival program — start immediately

**This is the only item in this document whose value expires.**

§31.3 (recency-weighted ADP) and parts of §31.1 need intra-summer ADP history: how a player's price moved across July and August. That history may not be purchasable retroactively, and Fantasy Football Calculator may expose only a current or end-of-preseason value per season. Whatever is not captured this summer is likely gone permanently.

### Action

Write a scheduled job now — before any research, before the pipeline, before the site.

```text
frequency:  daily during July–August, weekly otherwise
capture:    FFC ADP for every real league format (§14)
            FantasyPros ECR where accessible
            projection snapshots from every configured provider
store:       data/snapshots/<date>/ with source, retrieval time, and hash
never:       overwrite a prior capture
```

Roughly twenty minutes of work. It requires none of the rest of the system to exist, and by August 2027 it will have produced a proprietary two-summer price-movement dataset that cannot be bought.

### Also capture, once, before Week 1

```text
preseason depth charts                      (§86)
preseason injury designations
final preseason ADP for every format
projection snapshots from all providers      (§38.1)
```

These become the decision-date snapshot for the 2026 season in next year's historical research. Without them, 2026 enters the training data with the same missing preseason context that limits every season before it.

---

# 85. Schedule and roster-construction factors

Absent from r1 entirely. Modest effects, cheap to compute, and directly actionable.

## 85.1 Bye weeks and playoff schedule

```text
bye_week                       from nflverse schedules
playoff_weeks_opponent_rank    weeks 15–17, prior-season defensive strength
stacked_byes                   count of rostered starters sharing a bye
```

State the limitation clearly: prior-season defensive strength is a weak predictor of next-season defensive strength, so playoff schedule strength is a **tiebreaker between similar players**, never a primary thesis. It should carry a low decision-value rating and be excluded from Targets on its own.

## 85.2 Roster correlation

Player values are not independent within a roster:

```text
qb_stack           QB + own pass catcher — raises ceiling, raises variance
bring_back         opposing pass catcher in the same game
own_handcuff       backup to a rostered RB — reduces downside, costs a slot
opponent_handcuff  backup to another manager's RB — pure contingency
```

§21.6 treats contingent upside as a property of a player. It is better understood as a property of a *roster*: the value of a handcuff depends entirely on whether the starter is already rostered, and by whom.

Model within the draft simulator (§36.2), which already tracks the full roster state and is therefore the only place these effects can be measured. Report the variance effect alongside the mean effect — stacking raises both, and which one matters depends on league format and playoff structure.

## 85.3 Decision value

Both subsections are expected to rate MEDIUM or LOW. They are included because they are cheap and because they are the kind of small edge that survives when the larger signals turn out to be priced. They do not appear on the draft-day sheet unless they reach MEDIUM.

---

# 86. Depth chart and camp information

§64.4 states that training-camp information is not captured well by historical stat models, and r1 offered no remedy. A partial one exists.

## Structured, free, historically available

```text
nflverse depth charts    weekly, back through the research window
nflverse rosters         roster status, transactions
nflverse injuries        designations by week
nflverse draft picks     draft capital as a role proxy
```

Preseason depth chart position is a genuine, machine-readable role signal that is knowable at the decision date, and it is available historically — which means it can be tested rather than assumed. It feeds §21.5 (backfield ambiguity from realized shares plus known changes) and §22.5 (team hierarchy).

Caveats to state: NFL depth charts are inconsistently maintained, frequently do not reflect actual usage, and treat some positions nominally. Test the signal rather than assuming it; it may well grade C.

## Unstructured — manual, logged

Beat-reporter role news has no free structured source and cannot be backfilled historically, so it cannot enter the research layer at all. It may enter the current-season application only as a manually entered annotation:

```yaml
- player_id: "00-0000000"
  as_of: 2026-08-14
  note: "Running with the first team; incumbent limited."
  source: "beat report"
  affects: role_uncertainty
  research_weight: none
```

`research_weight: none` is mandatory. These notes are displayed on the player page as context and never enter a model, because they exist for the current season only and would leak an information source unavailable in every historical season — precisely the asymmetry §6.1 exists to prevent.

---

# 87. In-season extension

The draft is one day. The season is eighteen weeks. The pipeline built here supports both, and the marginal cost of the second is small.

Deferred to the offseason build, but designed for now:

```text
waiver / FAAB valuation      rest-of-season value over replacement,
                             reusing the tier and replacement machinery (§19.3, §19.4)
start / sit                  distribution-based, reusing §19.2
buy-low / sell-high          regression flags applied in-season (§19.1, §25)
rookie development tracking  §22.4 is already a weekly-data method and is
                             more useful in October than in August
```

Two design implications for the MVP:

1. The canonical datasets (§13) already support weekly updating; do not build anything that assumes a single preseason snapshot.
2. The scoring profile and replacement-level code must accept a mid-season roster state, not only a draft-day one.

Decision value here is plausibly **higher** than several draft chapters — a season contains far more waiver and lineup decisions than draft picks — which is a reason to keep it in the registry and rank it honestly rather than treating it as an afterthought.

---

# 88. 2026 compressed timeline

The 2026 regular season opens September 9. This document describes a multi-month build that will not produce a usable 2026 draft input in the weeks remaining. This section defines what to do instead.

**Rule: nothing on this path that cannot be finished and used is started.**

## Week 1 — data only, no site

```text
[ ] Start the ADP archival job                     (§84)   ← do this first
[ ] Encode the real league profiles                (§14)
[ ] Player ID normalization                        (§12)
[ ] nflverse pull, 2012–2025                       (§10A)
[ ] Current FFC ADP + one projection snapshot
[ ] Snapshot everything with hashes                (§65)
[ ] Resolve the FFC pick-distribution question     (§31.1)
```

No research, no chapters, no publication layer. §84 comes first because its value expires; everything else on this list can be done in September at no loss.

## Week 2 — three analyses, chosen for power and decision value

```text
[ ] Team scoring / TD regression                   (§25)
      448 team-seasons, adequately powered, large effect, directly
      moves player valuations. Best ratio in the document.
[ ] Dead-zone ADP-bucket hit rates, DESCRIPTIVE ONLY (§21.1)
      Observed rates by bucket. No modelling, no evidence grades,
      no exception research — the sample will not support it here.
[ ] Tiers and value over replacement on the current board (§19.3)
      Uses the real league profiles.
```

Three. Not eight. Each is labelled DESCRIPTIVE under §2.2, and no prescriptive claim is made from a two-week analysis.

## Week 3 — output

```text
[ ] The draft-day sheet                            (§83)
[ ] Jupyter or Quarto to a single HTML file
```

No Astro, no Vega-Lite, no search index, no edition chooser, no evidence cards.

## Before Week 1 of the season

```text
[ ] Capture the preseason snapshot bundle          (§84)
[ ] Persist the recommendation audit trail for the real draft (§76)
```

The audit trail matters even with three descriptive analyses — it is the first entry in a record that becomes the calibration data for every later edition.

## September – February — build the real thing

Everything in this specification, in the order given by §79, starting from Step 0. Three advantages of building it then rather than now:

1. The power analysis is done before the work rather than after it.
2. The 2026 season completes in January, supplying a genuine new holdout year.
3. The 2026 audit trail exists, so evidence grades can be checked against a real draft instead of only against backtests.

## What this costs

The 2026 draft gets three descriptive analyses and a one-page sheet instead of a research publication. That is a real reduction. The alternative is a partially built system arriving in October, which is worth nothing for 2026 and no more complete for 2027.

---

# 89. Revision log

## r2 — 2026-08-12

### New sections

```text
§2.3   Decision value precedes statistical significance
§3.1   Numeric, pre-committed evidence rubric
§5.1   Statistical power and the feasibility gate
§5.2   Multiple-comparison protocol
§6.1   As-of discipline — leakage as a schema violation
§15.1  Outcome decomposition: availability × per-game production
§36.1  Decision value
§36.2  Draft-simulation backtest
§38.1  Provider projection uncertainty
§39.1  Effect-size shrinkage
§83    Draft-day sheet
§84    ADP archival program — start immediately
§85    Schedule and roster-construction factors
§86    Depth chart and camp information
§87    In-season extension
§88    2026 compressed timeline
§89    This log
```

### Changed

```text
§3     Added grade U (Unanswerable); card now reports MDE, family size,
       FDR q, shrunk effect, and decision value
§5     Power and multiplicity promoted from checklist items to gates
§6     Rolling-origin validation is now the default; fixed splits are
       reserved for large-n methods
§8     Quarto recommended for v1; Astro deferred to a possible v2
§13    Added as_of, availability fields, red-zone usage, depth chart rank,
       ADP pick-distribution fields, per-provider projection rows
§14    Real league profiles must be encoded before any research runs
§18    Chapter list reframed as a candidate pool subject to two gates;
       expect ~15–20 chapters to survive, not 47
§19.4  Dependency on §31.1/§31.2 made explicit; approximation fallback added
§21.5  Backfield ambiguity computed from realized shares, not projections
       (removes circularity with the projection baseline)
§31    §31.1 and §31.2 promoted to MVP; §31.4 cut
§51    Leakage, grading, and evaluation test groups added
§56    Composite rebuilt on shrinkage and factor caps; the invented
       A=1.00/B=0.65 evidence-weight ladder removed
§57    Factor taxonomy declared in configuration up front
§58    Home page example made realistic; unanswerable and test counts added
§59    Gates added ahead of methods; simulator, sheet, and audit trail
       added to MVP
§61    Phase 3 reduced by promotions and one cut
§63    Rows added for projection error, pick distributions, depth charts,
       schedule, injuries, and camp news
§64    Limitations 11–14 added; items 8 and 10 marked as governing
§68    Registry gains pre-registration, decision-value, power, effort,
       and kill-rule fields
§70    Public/custom folder split deferred
§71    UNANSWERABLE status added and distinguished from FAILED
§76    Audit trail promoted to MVP
§77    End-of-season review no longer optional; required outputs specified
§78    Acceptance criteria restructured; date, simulator, decision value,
       and draft-day sheet added
§79    Step 0 gates added; simulator moved ahead of the recommendation layer
§80    Twelve prohibitions added
§82    Second quality bar added above the first
```

### Rationale summary

Three problems in r1 drove the revision.

**The evidence system could not deliver what it promised.** Grades were defined in prose while the test suite required determinism, no numeric thresholds existed anywhere, and multiple testing was the last item on a checklist despite the project running 150+ hypothesis tests. §3.1, §5.1, and §5.2 close this. The expected result is materially fewer strong signals than r1's home page advertised, which is the correct outcome rather than a shortfall.

**Nothing measured whether the research improved a draft.** Every test operated at the player level, while §64.10 correctly noted that predicting players is not the same as proving a strategy — and then provided no way to test the difference. §36.2 supplies the missing evaluation and §36.1 supplies the metric that orders the research agenda by it.

**The scope did not fit the calendar.** §88 defines what is achievable before the 2026 draft and moves the rest to the offseason, where the work also gains a fresh holdout season and a real audit trail to calibrate against.

## r1 — original specification

Baseline. Retained in full; every section above is an amendment to it rather than a replacement of it.
