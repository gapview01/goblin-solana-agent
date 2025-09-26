.PHONY: run-executor run-telegram test lint

run-executor:
	bash scripts/run-executor-local.sh

run-telegram:
	bash scripts/run-telegram-local.sh

lint:
	python3 -m pip install -q pytest
	@echo "No dedicated linter configured; skipping."

test:
	pytest -q


