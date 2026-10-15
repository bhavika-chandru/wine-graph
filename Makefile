# Wine Explorer - run `make help` for the list of commands.
VENV    := .venv
PYTHON  := $(VENV)/bin/python
PIP     := $(VENV)/bin/pip
IMAGE   ?= wine-graph-api
COMPOSE := docker compose

.DEFAULT_GOAL := help
.PHONY: help install run docker-build docker-run docker-down test test-db test-integration lint format clean tree seed gds eval bench bench-data

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

$(VENV)/bin/activate:
	python3 -m venv $(VENV)

install: $(VENV)/bin/activate ## Create .venv and install dependencies
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements-dev.txt

run: ## Run the API locally with auto-reload (needs Neo4j running)
	$(VENV)/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

docker-build: ## Build the API image
	docker build -t $(IMAGE) .

docker-run: ## Start neo4j + api + nginx with docker compose
	$(COMPOSE) up -d --build
	$(COMPOSE) ps

docker-down: ## Stop the stack
	$(COMPOSE) down

test: ## Unit and API tests with coverage (no database needed)
	$(PYTHON) -m pytest -m "not integration and not db" --cov --cov-report=term-missing --cov-report=xml --cov-fail-under=60

test-db: ## Every query against a dev Neo4j loaded with tests/fixtures/seed.cypher (WIPES that DB)
	RUN_DB_TESTS=1 $(PYTHON) -m pytest -m db -v

test-integration: ## Health tests against the running compose stack
	RUN_INTEGRATION=1 $(PYTHON) -m pytest -m integration -v

lint: ## Pylint (must stay >= 9.5)
	$(VENV)/bin/pylint app tests scripts --fail-under=9.5

format: ## Format code with black
	$(VENV)/bin/black app tests scripts

seed: ## ETL both sources into Neo4j (Person 2)
	$(PYTHON) scripts/create_constraints.py
	$(PYTHON) scripts/seed_data.py

gds: ## GDS projections, algorithms and classifier (Person 3)
	$(PYTHON) scripts/gds_setup.py

eval: ## NL->Cypher evaluation on tests/eval/nl_questions.json (Person 3)
	$(PYTHON) -m scripts.eval_nl

bench-data: ## Load ~100k synthetic wines into the dev Neo4j for benchmarks (asks before wiping it)
	$(PYTHON) scripts/generate_bench_data.py

bench: ## Before/after query benchmarks + PROFILE plans (writes docs/benchmarks.md)
	$(PYTHON) scripts/benchmark.py

tree: ## Show the project structure
	@tree -I '$(VENV)|__pycache__|.git|htmlcov|.pytest_cache' || find . -path ./$(VENV) -prune -o -print | grep -v __pycache__

clean: ## Remove caches and generated files
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache htmlcov .coverage coverage.xml
