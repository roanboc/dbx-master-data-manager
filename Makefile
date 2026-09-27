.PHONY: install hooks lint format test test-fast test-live test-gui validate check demo ui screenshots spike clean

# pytest-xdist: one worker per core, each test file on one worker (its module fixtures stay shared)
PYTEST_ACROSS_CORES = -n auto --dist loadfile

install:            ## create .venv and install runtime and dev dependencies with uv
	uv sync

hooks:              ## run the checks before every push (scripts/hooks/pre-push)
	git config core.hooksPath scripts/hooks

lint:               ## ruff, as CI runs it: the lint rules and the formatting
	uv run ruff check . && uv run ruff format --check .

format:             ## ruff format
	uv run ruff format .

test:               ## the whole suite on DuckDB and on a Postgres started for the run (or MDM_TEST_POSTGRES)
	uv run pytest $(PYTEST_ACROSS_CORES)

test-fast:          ## DuckDB only, without the slow, live and browser tests, while iterating
	MDM_TEST_ENGINES=duckdb uv run pytest $(PYTEST_ACROSS_CORES) -m "not slow and not live and not gui"

test-live:          ## the live tests on a Lakebase endpoint (MDM_LAKEBASE_ENDPOINT and the SDK credentials)
	MDM_LIVE_LAKEBASE=1 uv run --extra databricks pytest -m live

test-gui:           ## the browser checks with axe (Chromium from PLAYWRIGHT_BROWSERS_PATH); CI runs them in their own job
	MDM_TEST_ENGINES=duckdb uv run --group gui pytest -m gui tests/ui -p no:xdist

validate:           ## the archreator validators and the public-safety scan
	python3 scripts/check_links.py && python3 scripts/check_model.py && python3 scripts/check_prose.py && \
	python3 scripts/scan_public_safe.py --root . --terms $${MDM_PUBLIC_SAFE_TERMS:-.public-safe-terms.txt}

check: validate lint test   ## everything CI runs

demo:               ## creations and hard cases -> arrive, then updates, deletes and hard cases -> arrive, on a fresh local store
	uv run mdm demo reset --yes && uv run mdm init --models models && \
	uv run mdm demo land --persons 2000 --organisations 500 --seed 7 --hard-cases 0.03 --creations-only && uv run mdm arrive && \
	uv run mdm demo land --persons 2000 --organisations 500 --seed 7 --hard-cases 0.03 --updates 0.1 --deletes 0.01 && \
	uv run mdm arrive && uv run mdm status && uv run mdm feed read --since 0 --limit 10 && uv run mdm task list --limit 10

ui:                 ## the steward workbench on http://127.0.0.1:8050 (holds the DuckDB file while it runs)
	uv run mdm ui

screenshots:        ## docs/screenshots/, from the invented demo world with the stub
	uv run --group gui python tools/screenshots.py

spike:              ## the throughput spike at 100,000 records on DuckDB
	uv run python tools/spike_throughput.py --engine duckdb --records 100000

clean:              ## remove the local store
	rm -rf .mdm
