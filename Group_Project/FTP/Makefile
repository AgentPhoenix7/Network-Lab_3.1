UV ?= uv
PYTHON ?= python
# Refuse stale locks; uv selects this project's .venv without shell activation.
RUN := $(UV) run --locked
BUILD_DIR ?= $(abspath build)
TEST_ENV := TMPDIR="$(BUILD_DIR)/tmp" TMP="$(BUILD_DIR)/tmp" TEMP="$(BUILD_DIR)/tmp" PYTHONDONTWRITEBYTECODE=1

.PHONY: sync lint test server client app clean-db

# Include optional chart rendering in the Python suite; Node.js stays a system tool.
test:
	@mkdir -p "$(BUILD_DIR)/tmp"
	$(TEST_ENV) $(RUN) --extra plots $(PYTHON) -W error::ResourceWarning -m unittest discover -s tests -v
	@if command -v node >/dev/null 2>&1; then node tests/test_frontend.cjs; else echo "Node.js not available: frontend helper tests skipped"; fi

sync:
	$(UV) sync --locked --extra plots

lint:
	$(RUN) --extra plots $(PYTHON) -m pylint --persistent=n ftp_lab tools/*.py tests/*.py

server:
	$(RUN) --no-dev $(PYTHON) -m ftp_lab.server

client:
	$(RUN) --no-dev $(PYTHON) -m ftp_lab.client

app:
	$(RUN) --no-dev $(PYTHON) -m ftp_lab.webapp --demo

clean-db:
	$(RUN) --no-dev $(PYTHON) tools/clean_db.py --force --all
