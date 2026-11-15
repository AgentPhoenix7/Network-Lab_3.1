PYTHON ?= python3
SCRATCH := $(abspath ../../.scratch)
TEST_ENV := TMPDIR="$(SCRATCH)/tmp" TMP="$(SCRATCH)/tmp" TEMP="$(SCRATCH)/tmp" PYTHONDONTWRITEBYTECODE=1

.PHONY: test server client

test:
	@mkdir -p "$(SCRATCH)/tmp"
	$(TEST_ENV) $(PYTHON) -W error::ResourceWarning -m unittest discover -s tests -v

server:
	$(PYTHON) -m ftp_lab.server

client:
	$(PYTHON) -m ftp_lab.client
