IMAGE ?= salimova_scrape_datasets:latest
ARTIFACTS_DIR ?= artifacts

# CARSS requires fresh cookies exported from your browser.
COOKIES ?= $(ARTIFACTS_DIR)/secrets/cookies.json

PY ?= python3

.PHONY: help
help:
	@echo "Targets:"
	@echo "  make build                 Build docker image"
	@echo "  make shell                 Open bash in container"
	@echo "  make carss-drug YEAR=2024  Scrape CARSS publish/drug"
	@echo "  make carss-indicator       Scrape CARSS publish/indicator lineplot"
	@echo "  make yearbook-2014 YEAR=2024  Index+download JPG tables (2014–2025)"
	@echo "  make yearbook-2005 YEAR=2005  Index+download HTML tables (2005–2013)"
	@echo "  make env-info              Print environment versions"

.PHONY: build
build:
	docker build -t $(IMAGE) .

.PHONY: shell
shell:
	docker run --rm -it \
	  -v "$(CURDIR):/app" \
	  -e PYTHONPATH=/app/src \
	  $(IMAGE) bash

.PHONY: env-info
env-info:
	@mkdir -p "$(ARTIFACTS_DIR)"
	@bash scripts/export_environment.sh | tee "$(ARTIFACTS_DIR)/env_versions.txt"

.PHONY: carss-drug
carss-drug:
	@mkdir -p "$(ARTIFACTS_DIR)/carss/publish_drug"
	docker run --rm -t \
	  -v "$(CURDIR):/app" \
	  -e PYTHONPATH=/app/src \
	  $(IMAGE) $(PY) CARSS/publish_drug/run.py \
	    --cookies "$(COOKIES)" \
	    --out "$(ARTIFACTS_DIR)/carss/publish_drug/carss_drug_resistance.csv" \
	    $(if $(YEAR),--year $(YEAR),)

.PHONY: carss-indicator
carss-indicator:
	@mkdir -p "$(ARTIFACTS_DIR)/carss/publish_indicator"
	docker run --rm -t \
	  -v "$(CURDIR):/app" \
	  -e PYTHONPATH=/app/src \
	  $(IMAGE) $(PY) CARSS/publish_indicator/run.py \
	    --cookies "$(COOKIES)" \
	    --out "$(ARTIFACTS_DIR)/carss/publish_indicator/carss_drug_resistance_lineplot.csv"

.PHONY: yearbook-2014
yearbook-2014:
	@mkdir -p "$(ARTIFACTS_DIR)/yearbook/2014-2025"
	docker run --rm -t \
	  -v "$(CURDIR):/app" \
	  -e PYTHONPATH=/app/src \
	  $(IMAGE) $(PY) Yearbook/2014-2025/run.py \
	    --year $(YEAR) \
	    --out "$(ARTIFACTS_DIR)/yearbook/2014-2025/$(YEAR)" \
	    --download \
	    --index

.PHONY: yearbook-2005
yearbook-2005:
	@mkdir -p "$(ARTIFACTS_DIR)/yearbook/2005-2013"
	docker run --rm -t \
	  -v "$(CURDIR):/app" \
	  -e PYTHONPATH=/app/src \
	  $(IMAGE) $(PY) Yearbook/2005-2013/run.py \
	    --year $(YEAR) \
	    --out "$(ARTIFACTS_DIR)/yearbook/2005-2013/$(YEAR)" \
	    --download-pages \
	    --index

