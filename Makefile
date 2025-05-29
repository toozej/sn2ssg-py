# Set sane defaults for Make
SHELL = bash
.DELETE_ON_ERROR:
MAKEFLAGS += --warn-undefined-variables
MAKEFLAGS += --no-builtin-rules

# Set default goal such that `make` runs `make help`
.DEFAULT_GOAL := help

# Set variables used across the project
IMAGE_NAME = toozej/sn2ssg-py
IMAGE_TAG = latest

OS = $(shell uname -s)
ifeq ($(OS), Linux)
	OPENER=xdg-open
else
	OPENER=open
endif

.PHONY: all build test run update-requirements pre-commit pre-commit-install pre-commit-run clean help

all: build test run ## Run default workflow

build: ## Build Docker image
	docker build -f $(CURDIR)/Dockerfile -t $(IMAGE_NAME):$(IMAGE_TAG) .

test: ## Run tests locally
	if test -e $(CURDIR)/.venv/bin/activate; then \
		source $(CURDIR)/.venv/bin/activate; \
	else \
		python3 -m venv $(CURDIR)/.venv; \
		source $(CURDIR)/.venv/bin/activate; \
		pip3 install -r $(CURDIR)/requirements.txt; \
		pip3 install pytest; \
	fi
	if test -e $(CURDIR)/.env; then \
		export `cat $(CURDIR)/.env | xargs`; \
	else \
		echo "No env vars found, need to add them to ./.env. See README.md for more info"; \
	fi; \
	pytest -vv $(CURDIR)/test_sn2ssg.py

run: ## Run built Docker image
	-docker kill sn2ssg-py
	docker run --rm --env-file .env --name sn2ssg-py -v $(CURDIR)/out:/out $(IMAGE_NAME):$(IMAGE_TAG)

update-requirements: ## Update Python requirements
	@input_line=$$(grep "pip install" $(CURDIR)/Dockerfile); \
	package_name=$$(echo $$input_line | grep -oP 'pip install \K[^=]+'); \
	version=$$(echo $$input_line | grep -oP '==\K[^ ]+'); \
	latest_version=$$(curl -s "https://pypi.org/pypi/$$package_name/json" | jq -r '.info.version'); \
	if [ "$$latest_version" != "$$version" ]; then \
		sed -i "s/$$version/$$latest_version/g" "$(CURDIR)/Dockerfile"; \
		echo "Package $$package_name updated to $$latest_version in Dockerfile."; \
	else \
		echo "The version $$latest_version of package $$package_name in Dockerfile is already up to date."; \
	fi

pre-commit: pre-commit-install pre-commit-run ## Install and run pre-commit hooks

pre-commit-install: ## Install pre-commit hooks and necessary binaries
	# install and update pre-commits
	pre-commit install
	pre-commit autoupdate

pre-commit-run: ## Run pre-commit hooks against all files
	pre-commit run --all-files

clean: ## Clean up generated files and built Docker images
	-docker image rm $(IMAGE_NAME):$(IMAGE_TAG)
	rm -f $(CURDIR)/in/* $(CURDIR)/out/*

help: ## Display help text
	@grep -E '^[a-zA-Z_-]+ ?:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}'
