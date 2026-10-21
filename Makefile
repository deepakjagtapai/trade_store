PYTHON ?= python3
PLANTUML ?= plantuml
IMAGE ?= trade-store:latest

.PHONY: install install-e2e lint format typecheck test regression e2e run demo scan diagrams docker-build clean

install:
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements-dev.txt

install-e2e:
	$(PYTHON) -m pip install -r requirements-e2e.txt

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

format:
	$(PYTHON) -m ruff check --fix .
	$(PYTHON) -m ruff format .

typecheck:
	$(PYTHON) -m mypy app

test:
	$(PYTHON) -m pytest -m "not regression" --cov=app --cov-report=term-missing --cov-report=xml

regression:
	$(PYTHON) -m pytest -m regression

e2e:
	$(PYTHON) -m pytest -m e2e --run-e2e

run:
	$(PYTHON) -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

demo:
	DEMO_MODE=true $(PYTHON) -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

scan:
	$(PYTHON) -m pip_audit -r requirements.txt
	trivy fs --severity CRITICAL --exit-code 1 .

diagrams:
	$(PLANTUML) -tpng docs/*.puml
	$(PLANTUML) -tsvg docs/*.puml

docker-build:
	docker build -t $(IMAGE) .

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml htmlcov reports
