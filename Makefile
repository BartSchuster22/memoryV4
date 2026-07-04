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
	@printf '%s\n' '{"status":"partial","phase":"foundation","pass":0,"partial":[1,2,3,5,6,8,9,10],"not_implemented":[4,7],"message":"Foundation slice only; see docs/QA10.md for gate status."}' > build/qa10-scorecard.json
	@cat build/qa10-scorecard.json

docker-build:
	docker build -t $(IMAGE) .
