PYTHON ?= python3
BUILD_DIR ?= $(abspath build)
TEST_ENV := TMPDIR="$(BUILD_DIR)/tmp" TMP="$(BUILD_DIR)/tmp" TEMP="$(BUILD_DIR)/tmp" PYTHONDONTWRITEBYTECODE=1

.PHONY: test server client app

test:
	@mkdir -p "$(BUILD_DIR)/tmp"
	$(TEST_ENV) $(PYTHON) -W error::ResourceWarning -m unittest discover -s tests -v
	@if command -v node >/dev/null 2>&1; then node tests/test_frontend.cjs; else echo "Node.js not available: frontend helper tests skipped"; fi

server:
	$(PYTHON) -m ftp_lab.server

client:
	$(PYTHON) -m ftp_lab.client

app:
	$(PYTHON) -m ftp_lab.webapp --demo
