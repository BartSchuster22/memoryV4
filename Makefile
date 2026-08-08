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
	@printf '%s\n' '{"status":"partial","phase":"governance","pass":0,"partial":[1,2,3,5,6,7,8,9,10],"not_implemented":[4],"message":"Governance-complete record slice; full QA10 remains incomplete. See docs/QA10.md."}' > build/qa10-scorecard.json
	@cat build/qa10-scorecard.json

docker-build:
	docker build -t $(IMAGE) .
