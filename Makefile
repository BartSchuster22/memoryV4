PYTHON ?= python3.11
APP ?= app.main:app
IMAGE ?= memoryv4-core:dev

.PHONY: run test lint qa qa10 docker-build

run:
	$(PYTHON) -m uvicorn $(APP) --host 127.0.0.1 --port 8000

test:
	$(PYTHON) -m pytest -q

lint:
	$(PYTHON) -m ruff check app tests

qa: lint test qa10

qa10:
	@mkdir -p build
	$(PYTHON) scripts/qa10.py

docker-build:
	docker build -t $(IMAGE) .
