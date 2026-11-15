PYTHON ?= python3
SCRATCH := $(abspath ../../.scratch)
TEST_ENV := TMPDIR="$(SCRATCH)/tmp" TMP="$(SCRATCH)/tmp" TEMP="$(SCRATCH)/tmp" PYTHONDONTWRITEBYTECODE=1

.PHONY: test server client app

test:
	@mkdir -p "$(SCRATCH)/tmp"
	$(TEST_ENV) $(PYTHON) -W error::ResourceWarning -m unittest discover -s tests -v
	@if command -v node >/dev/null 2>&1; then node tests/test_frontend.cjs; else echo "Node.js not available: frontend helper tests skipped"; fi

server:
	$(PYTHON) -m ftp_lab.server

client:
	$(PYTHON) -m ftp_lab.client

app:
	$(PYTHON) -m ftp_lab.webapp --demo
