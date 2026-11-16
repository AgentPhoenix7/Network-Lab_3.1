PDFLATEX ?= pdflatex
BUILD_DIR ?= $(CURDIR)/build
REPORT_ENV := TMPDIR="$(BUILD_DIR)/tmp" TMP="$(BUILD_DIR)/tmp" TEMP="$(BUILD_DIR)/tmp"
SOURCES := preamble.tex refs.tex $(wildcard sections/*.tex sections_H/*.tex code/ftp_lab/*.py evidence/*.txt figures/*.pdf figures/*.png)

.PHONY: all rebuild
all: report.pdf report_H.pdf

rebuild:
	$(MAKE) -B all

%.pdf: %.tex $(SOURCES)
	@mkdir -p "$(BUILD_DIR)/tmp" "$(BUILD_DIR)/$*"
	$(REPORT_ENV) $(PDFLATEX) -no-shell-escape -halt-on-error -interaction=nonstopmode -output-directory "$(BUILD_DIR)/$*" "$<"
	$(REPORT_ENV) $(PDFLATEX) -no-shell-escape -halt-on-error -interaction=nonstopmode -output-directory "$(BUILD_DIR)/$*" "$<"
	$(REPORT_ENV) $(PDFLATEX) -no-shell-escape -halt-on-error -interaction=nonstopmode -output-directory "$(BUILD_DIR)/$*" "$<"
	cp "$(BUILD_DIR)/$*/$@" "$@"
