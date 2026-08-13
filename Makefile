.PHONY: setup ingest ids tables validate test lint snapshot all

SEASONS ?= 2012-2025

setup:
	uv sync

ingest:
	uv run research ingest --seasons $(SEASONS)
	uv run python -c "from pipeline.ingest.nflverse import fetch_schedules; \
	from pipeline.config import RAW_DIR; f = fetch_schedules(); \
	(RAW_DIR/'nflverse'/f.filename).write_bytes(f.data); print('schedules ok')"

ids:
	uv run research normalize-ids

tables:
	uv run research build-tables --seasons $(SEASONS)

# Captures the sources whose value expires (S84). Normally run by GitHub
# Actions; this target is for a manual capture from a machine that can reach
# Fantasy Football Calculator.
snapshot:
	uv run research snapshot --sources ffc,projections

validate:
	uv run research validate

test:
	uv run pytest -q

lint:
	uv run ruff check .

all: setup ingest ids tables validate test
