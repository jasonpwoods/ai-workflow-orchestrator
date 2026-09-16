.PHONY: install test eval gate run mcp docker
export PYTHONPATH := src

install:
	pip install -r requirements.txt && pip install -e .

test:
	python -m pytest

eval:
	python evals/run_eval.py

gate:
	python evals/gate.py

run:
	python -m orchestrator.cli run sample_data

mcp:
	python -m orchestrator.mcp_server

docker:
	docker build -t ai-workflow-orchestrator . && docker run --rm ai-workflow-orchestrator
