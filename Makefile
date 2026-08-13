.PHONY: setup ingest ids tables research sheet validate test lint snapshot all

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

validate:
	uv run research validate

test:
	uv run pytest -q

lint:
	uv run ruff check .

all: setup ingest ids tables research sheet validate test
