PYTHON ?= python3
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
	@printf '%s\n' '{"status":"pending","phase":"D0","message":"QA10 gates are defined but not implemented in D0"}' > build/qa10-scorecard.json
	@cat build/qa10-scorecard.json

docker-build:
	docker build -t $(IMAGE) .
