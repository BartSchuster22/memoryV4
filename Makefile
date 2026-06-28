PYTHON ?= python3
APP ?= app.main:app
IMAGE ?= memoryv4-core:dev

.PHONY: run test lint qa qa10 docker-build

run:
	$(PYTHON) -m uvicorn $(APP) --host 127.0.0.1 --port 8000

test:
	$(PYTHON) -m pytest -q

lint:
	$(PYTHON) -m compileall -q app tests

qa: lint test qa10

qa10:
	@mkdir -p build
	@printf '%s\n' '{"status":"pending","phase":"P1","message":"P0-P1 bootstrap and Store seam are implemented; full QA10 gates start in later phases"}' > build/qa10-scorecard.json
	@cat build/qa10-scorecard.json

docker-build:
	docker build -t $(IMAGE) .
