# Shortcuts for Linux and macOS (and Git Bash on Windows). Every target is one plain command, shown by `make help`,
# so nothing here is needed: on Windows run the same `py -3 -I src\mirror.py ...` commands directly.
#
#   make ui          open the setup and sync page in your browser
#   make preview     what a sync would add (changes nothing)
#   make sync        mirror new activity and push
#   make test        run all tests (offline)

PYTHON ?= $(shell command -v python3 2>/dev/null || command -v python 2>/dev/null)
MIRROR := $(PYTHON) -I src/mirror.py
TIME ?= 18:00

.DEFAULT_GOAL := help
.PHONY: help setup ui preview sync accounts doctor schedule schedule-login schedule-daily schedule-off schedule-status \
        test check clean

help: ## show the targets
	@echo "Contribution mirror: common commands (variables: PYTHON=$(PYTHON) TIME=$(TIME))"
	@echo
	@awk 'BEGIN {FS = ":.*## "} /^[a-z][a-zA-Z_-]*:.*## / {printf "  make %-17s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: ## create your settings (.env) and project list (repos.json) from the templates, show where (never overwrites)
	$(MIRROR) setup

ui: ## open the setup and sync page in your browser
	$(MIRROR) ui

preview: ## show what a sync would add (changes nothing)
	$(MIRROR) sync --dry-run

sync: ## mirror new activity and push to your mirror repo
	$(MIRROR) sync

accounts: ## show which login each host would use for pull requests and reviews
	$(MIRROR) accounts

doctor: ## check the whole setup and say how to fix what is wrong (reads only)
	$(MIRROR) doctor

schedule: ## automatic runs at login and daily at TIME (default 18:00), e.g. make schedule TIME=14:00
	$(MIRROR) schedule install --time $(TIME)

schedule-login: ## automatic run at login only
	$(MIRROR) schedule install --no-daily

schedule-daily: ## automatic run daily at TIME only, e.g. make schedule-daily TIME=09:30
	$(MIRROR) schedule install --no-login --time $(TIME)

schedule-off: ## stop all automatic runs
	$(MIRROR) schedule uninstall

schedule-status: ## show what automatic runs are set up
	$(MIRROR) schedule status

test: ## run all tests (offline, needs only Python and git)
	$(PYTHON) -W error::ResourceWarning -m unittest discover -s tests

check: test ## the tests, plus a byte-compile of every source file
	$(PYTHON) -m compileall -q src tests

clean: ## remove Python caches (never touches your settings or logs)
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
