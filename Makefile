# Orphograph — one-button operational targets.
#
# Usage: `make <target>`. Run `make help` for the full list.

ROOT  := $(shell pwd)
PY    := python3
# Prefer Homebrew install (handles macOS code-signing properly) over
# the curl-pipe install which has hit truncated-download problems.
FLY   := $(shell command -v flyctl 2>/dev/null || command -v fly 2>/dev/null || echo $(HOME)/.fly/bin/flyctl)
PORT  ?= 8989
HOST  ?= 127.0.0.1
DATA  ?= $(HOME)/orphograph/data

.PHONY: help dev test test-quick smoke safety preflight \
        local-start local-stop local-restart local-status local-logs \
        first-deploy deploy fly-deploy fly-secrets fly-logs fly-check \
        backup sweep-test e2e revenue-probe \
        health milestone clean

help:
	@echo "Orphograph make targets"
	@echo ""
	@echo "  --- dev loop ---"
	@echo "  make dev              run server in foreground on $(HOST):$(PORT)"
	@echo "  make test             full pytest suite (offline)"
	@echo "  make test-quick       fast subset (skip live OTS smoke)"
	@echo "  make smoke            live OTS calendar end-to-end"
	@echo "  make safety           publish safety check on web/verify/"
	@echo "  make preflight        preflight probe against http://$(HOST):$(PORT)"
	@echo ""
	@echo "  --- local service (launchd) ---"
	@echo "  make local-start      bootstrap all 6 launchd agents"
	@echo "  make local-stop       unload all 6 launchd agents"
	@echo "  make local-restart    bounce server agent only"
	@echo "  make local-status     show pid/exit for each agent"
	@echo "  make local-logs       tail server.err.log + health_monitor.log"
	@echo ""
	@echo "  --- production deploy (Fly) ---"
	@echo "  make fly-check        verify flyctl is installed + authed"
	@echo "  make first-deploy     one-shot: signup→launch→volume→deploy→cert"
	@echo "  make deploy           subsequent: tests→safety→fly deploy→preflight"
	@echo "  make fly-secrets      list current Fly secrets (no values shown)"
	@echo "  make fly-logs         tail Fly logs"
	@echo ""
	@echo "  --- ops ---"
	@echo "  make backup           gpg-encrypted local backup of the data dir"
	@echo "  make e2e              end-to-end revenue-flow probe"
	@echo "  make health           one-shot health probe + status summary"
	@echo "  make milestone        one-shot milestone check (dry-run notifications)"

# ============================================================================
# dev loop
# ============================================================================

dev:
	HOST=$(HOST) PORT=$(PORT) ORPHO_DATA_DIR=$(DATA) ORPHO_COOKIE_SECURE=0 $(PY) server/app.py

test:
	rm -rf server/__pycache__ tests/__pycache__
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 $(PY) -m pytest tests/ -q

test-quick:
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 $(PY) -m pytest tests/ -q \
		--ignore=tests/test_ui.py --ignore=tests/test_attacks.py

smoke:
	PORT=$(PORT) ORPHO_DATA_DIR=$(DATA)_smoke bash scripts/smoke_test.sh

safety:
	bash scripts/publish_safety_check.sh

preflight:
	bash scripts/preflight.sh http://$(HOST):$(PORT)

# ============================================================================
# local service via launchd
# ============================================================================

LAUNCHD_AGENTS := server upgrade expire btc_settle health milestone
UID            := $(shell id -u)

local-start:
	@for a in $(LAUNCHD_AGENTS); do \
		plist=$(HOME)/Library/LaunchAgents/com.orphograph.$$a.plist; \
		echo "→ $$a"; \
		launchctl bootstrap gui/$(UID) "$$plist" 2>&1 || \
		launchctl load "$$plist" 2>&1 || true; \
	done
	@$(MAKE) local-status

local-stop:
	@for a in $(LAUNCHD_AGENTS); do \
		echo "→ stopping $$a"; \
		launchctl bootout gui/$(UID)/com.orphograph.$$a 2>&1 || \
		launchctl unload $(HOME)/Library/LaunchAgents/com.orphograph.$$a.plist 2>&1 || true; \
	done

local-restart:
	@echo "→ kickstart server"
	@launchctl kickstart -k gui/$(UID)/com.orphograph.server
	@sleep 2
	@$(MAKE) health

local-status:
	@echo "label                          pid       exit"
	@launchctl list | grep orphograph | awk '{printf "  %-30s pid=%-7s exit=%s\n", $$3, $$1, $$2}'

local-logs:
	@echo "=== server.err.log (last 20) ==="
	@tail -20 $(HOME)/orphograph/logs/server.err.log 2>/dev/null || echo "(empty)"
	@echo ""
	@echo "=== health_monitor.log (last 10) ==="
	@tail -10 $(HOME)/orphograph/logs/health_monitor.log 2>/dev/null || echo "(empty)"
	@echo ""
	@echo "=== btc_settle.err.log (last 5) ==="
	@tail -5 $(HOME)/orphograph/logs/btc_settle.err.log 2>/dev/null || echo "(empty)"

# ============================================================================
# production deploy
# ============================================================================

fly-check:
	@if [ ! -x "$(FLY)" ] && [ -z "$$(command -v $(FLY) 2>/dev/null)" ]; then \
		echo "flyctl not found. Install with one of:"; \
		echo "  brew install flyctl              # recommended on macOS"; \
		echo "  curl -fsSL https://fly.io/install.sh | sh"; \
		exit 1; \
	fi
	@echo "flyctl path: $(FLY)"
	@$(FLY) version 2>&1 | head -1 || { echo "  ERROR: flyctl binary failed to run (corrupted?)"; exit 1; }
	@$(FLY) auth whoami 2>/dev/null && echo "auth: OK" || echo "auth: NOT logged in (run: $(FLY) auth login)"

first-deploy:
	@echo "=== STEP 1: verify flyctl ==="
	@$(MAKE) fly-check
	@echo ""
	@echo "=== STEP 2: authenticate (opens browser) ==="
	@$(FLY) auth whoami >/dev/null 2>&1 || $(FLY) auth login
	@echo ""
	@echo "=== STEP 3: launch app (uses existing fly.toml) ==="
	@$(FLY) apps list | grep -q orphograph || $(FLY) launch --copy-config --no-deploy --region iad --name orphograph
	@echo ""
	@echo "=== STEP 4: create persistent volume ==="
	@$(FLY) volumes list -a orphograph 2>/dev/null | grep -q orphograph_data || \
		$(FLY) volumes create orphograph_data --region iad --size 1 -a orphograph
	@echo ""
	@echo "=== STEP 5: deploy ==="
	@$(MAKE) test
	@$(FLY) deploy -a orphograph
	@echo ""
	@echo "=== STEP 6: add TLS cert (you'll need to add DNS records at registrar) ==="
	@$(FLY) certs create orphograph.com -a orphograph || true
	@echo ""
	@echo "=== STEP 7: smoke test the public site ==="
	@bash scripts/preflight.sh https://orphograph.com || \
		echo "  preflight failed (DNS may still be propagating — re-run in 10 min)"

deploy:
	@$(MAKE) fly-check
	@$(MAKE) test
	@$(MAKE) safety
	$(FLY) deploy -a orphograph
	bash scripts/preflight.sh https://orphograph.com

fly-secrets:
	@$(MAKE) fly-check
	$(FLY) secrets list -a orphograph

fly-logs:
	@$(MAKE) fly-check
	$(FLY) logs -a orphograph --since 5m

# ============================================================================
# ops
# ============================================================================

backup:
	@if [ -z "$$ORPHO_BACKUP_GPG_KEY" ]; then \
		echo "set ORPHO_BACKUP_GPG_KEY=<gpg-key-id> first (see scripts/backup_volume.sh)"; \
		exit 1; \
	fi
	bash scripts/backup_volume.sh "$$ORPHO_BACKUP_GPG_KEY"

e2e revenue-probe:
	bash scripts/e2e_revenue_probe.sh

health:
	@curl -s http://$(HOST):$(PORT)/api/health | $(PY) -m json.tool 2>&1 | head -20

milestone:
	@ORPHO_DATA_DIR=$(DATA) $(PY) scripts/milestone_watcher.py

clean:
	@find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
	@find . -name "*.pyc" -delete 2>/dev/null || true
	@find . -name ".pytest_cache" -type d -exec rm -rf {} + 2>/dev/null || true
	@echo "cleaned"
