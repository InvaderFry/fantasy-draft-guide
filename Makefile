.PHONY: setup ingest ids tables validate test lint snapshot all

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

validate:
	uv run research validate

test:
	uv run pytest -q

lint:
	uv run ruff check .

all: setup ingest ids tables validate test
