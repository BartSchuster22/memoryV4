PYTHON ?= python3
APP ?= app.main:app
IMAGE ?= memoryv4-core:dev

.PHONY: run test lint qa qa10 qa10-gate-1 docker-build docker-test

run:
	$(PYTHON) -m uvicorn $(APP) --host 127.0.0.1 --port 8000

test:
	$(PYTHON) -m pytest -q

lint:
	$(PYTHON) -m compileall -q app tests scripts

qa: lint test qa10

qa10-gate-1:
	$(PYTHON) -m pytest tests/test_migrations_p2.py -q

qa10: qa10-gate-1
	$(PYTHON) scripts/write_qa10_scorecard.py

docker-build:
	docker build -t $(IMAGE) .

docker-test: docker-build
	docker run --rm -e MEMORYV4_DB_PATH=/tmp/memoryv4-test.sqlite3 $(IMAGE) python -m pytest -q
