PYTHON ?= python3
APP ?= app.main:app
IMAGE ?= memoryv4-core:dev

.PHONY: run test lint qa qa10 qa10-gate-1 docker-build

run:
	$(PYTHON) -m uvicorn $(APP) --host 127.0.0.1 --port 8000

test:
	$(PYTHON) -m pytest -q

lint:
	$(PYTHON) -m compileall -q app tests

qa: lint test qa10

qa10-gate-1:
	$(PYTHON) -m pytest -q tests/test_migrations_p2.py

qa10: qa10-gate-1
	$(PYTHON) scripts/qa10_scorecard.py

docker-build:
	docker build -t $(IMAGE) .
