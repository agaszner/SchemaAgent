.PHONY: help install dev api ui test data

help:  ## List the targets
	@grep -E '^[a-z]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-8s %s\n", $$1, $$2}'

install:  ## Install dependencies with uv
	uv sync

dev:  ## Start the API and the UI together (Ctrl-C stops both)
	$(MAKE) -j2 api ui

api:  ## Start the API on http://127.0.0.1:8000 (docs at /docs)
	uv run fastapi dev

ui:  ## Start the Streamlit UI on http://localhost:8501
	uv run python -m streamlit run ui/main.py

test:  ## Run the test suite
	uv run pytest -q

data:  ## Download the sample databases (TIER=easy|medium|hard|very-hard)
	data/download.sh $(TIER)
