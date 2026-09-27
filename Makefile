.PHONY: install hooks lint format test test-fast test-live validate check demo spike clean

install:            ## create .venv and install runtime and dev dependencies with uv
	uv sync

hooks:              ## run the checks before every push (scripts/hooks/pre-push)
	git config core.hooksPath scripts/hooks

lint:               ## ruff, as CI runs it: the lint rules and the formatting
	uv run ruff check . && uv run ruff format --check .

format:             ## ruff format
	uv run ruff format .

test:               ## the whole suite on DuckDB and on a Postgres started for the run (or MDM_TEST_POSTGRES)
	uv run pytest

test-fast:          ## DuckDB only, without the slow and live tests, while iterating
	MDM_TEST_ENGINES=duckdb uv run pytest -m "not slow and not live"

test-live:          ## the live tests on a Lakebase endpoint (MDM_LAKEBASE_ENDPOINT and the SDK credentials)
	MDM_LIVE_LAKEBASE=1 uv run --extra databricks pytest -m live

validate:           ## the archreator validators and the public-safety scan
	python3 scripts/check_links.py && python3 scripts/check_model.py && python3 scripts/check_prose.py && \
	python3 scripts/scan_public_safe.py --root . --terms $${MDM_PUBLIC_SAFE_TERMS:-.public-safe-terms.txt}

check: validate lint test   ## everything CI runs

demo:               ## land -> arrive -> commit -> feed, on a fresh local store
	uv run mdm demo reset --yes && uv run mdm init --models models && \
	uv run mdm demo land --persons 2000 --organisations 500 --seed 7 && uv run mdm arrive && \
	uv run mdm status && uv run mdm feed read --since 0 --limit 10 && uv run mdm task list --limit 10

spike:              ## the throughput spike at 100,000 records on DuckDB
	uv run python tools/spike_throughput.py --engine duckdb --records 100000

clean:              ## remove the local store
	rm -rf .mdm
