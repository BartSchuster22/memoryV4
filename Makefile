PYTHON ?= python3
APP ?= app.main:app
IMAGE ?= memoryv4-core:dev

.PHONY: run test qa10 docker-build guardrails-check

run:
	$(PYTHON) -m uvicorn $(APP) --host 127.0.0.1 --port 8000

test:
	$(PYTHON) -m pytest -q

qa10:
	@mkdir -p build
	@printf '%s\n' '{"status":"pending","phase":"D0","score":0,"total_gates":10,"message":"QA10 gates are defined in docs/QA10.md but not implemented in D0"}' > build/qa10-scorecard.json
	@cat build/qa10-scorecard.json

guardrails-check:
	./scripts/kanban_guardrails.sh --self-test

docker-build:
	docker build -t $(IMAGE) .
