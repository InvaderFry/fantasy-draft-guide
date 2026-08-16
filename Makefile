.PHONY: setup ingest ids tables research sheet fit validate test lint snapshot preseason \
	draft-check draft-record draft-review all

SEASONS ?= 2012-2025

setup:
	uv sync

ingest:
	uv run research ingest --seasons $(SEASONS)

ids:
	uv run research normalize-ids

tables:
	uv run research build-tables --seasons $(SEASONS)

# Captures the sources whose value expires (S84). Normally run by GitHub
# Actions; this target is for a manual capture from a machine that can reach
# Fantasy Football Calculator.
snapshot:
	uv run research snapshot --sources ffc,projections

# S84's second capture program: the preseason state, taken once before Week 1.
# Normally run by GitHub Actions daily; the command itself decides whether today
# is a capture day (pipeline/preseason.py) and exits 0 when it is not.
# `research preseason-status` reports what the archive holds.
preseason:
	uv run research snapshot --sources nflverse-preseason

research:
	uv run research run-research

# S83. The one-page decision aid, generated per real league profile AND per
# draft slot from the S16 artifacts `make research` just wrote, plus an
# index.html chooser. S78 acceptance criterion.
#
# The draft order is drawn about an hour before the draft, so every seat is
# rendered ahead of time and the draw is a file open. `make sheet SLOT=7`
# regenerates one seat against the freshest ADP if a machine is to hand.
sheet:
	uv run research sheet $(if $(SLOT),--slot $(SLOT),)

# S83's one-page rule, measured. This replaces the hand sweep the README used to
# describe: it prints every sheet with the same headless invocation, counts the
# PDF pages, and reports how many more rows a position the tightest page would
# take before it breaks. Needs a Chromium or Chrome on PATH (or $CHROME_BIN).
#
# Re-measure with this before raising MAX_TIER_PLAYERS or adding a column.
fit:
	uv run research fit-check $(if $(EDITION),--edition $(EDITION),)

# S76's audit trail, in the order it has to happen. PROFILE, SLOT and PICKS are
# required; DATE defaults to today.
#
# The rehearsal, and it is not optional. S84 refuses a second record on the same
# date, so this is the only chance to find a line the parser skipped or a name
# S12 cannot resolve -- both parse cleanly and then pair with nothing, which
# reads as a well calibrated approximation rather than as a fault. It also
# reports the board it would freeze, and reds on everything the real run reds on.
draft-check:
	uv run research draft-record --profile $(PROFILE) --slot $(SLOT) --picks $(PICKS) \
		$(if $(DATE),--date $(DATE),) --dry-run

# Run it the same night. The freeze is why: `2026-draft` is regenerated in place
# at 11:00 UTC, so the survival artifact this draft was priced against is gone by
# breakfast, and it cannot be rebuilt -- nothing pins an as-of date, so a rebuild
# quotes prices the sheet never carried and pairs against them without complaint.
draft-record:
	uv run research draft-record --profile $(PROFILE) --slot $(SLOT) --picks $(PICKS) \
		$(if $(DATE),--date $(DATE),)
	uv run research build-tables --tables draft_pick

# No --edition. The record names the frozen board it was taken against, per
# league, which is the only board the pairing means anything against.
draft-review:
	uv run research draft-review

validate:
	uv run research validate

test:
	uv run pytest -q

lint:
	uv run ruff check .

all: setup ingest ids tables research sheet validate test
