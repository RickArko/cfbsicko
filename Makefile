UV ?= uv
FLY ?= $(shell command -v fly || command -v flyctl || echo "$(HOME)/.fly/bin/fly")
FLY_APP ?= cfbsicko
FLY_ORG ?= personal
FLY_REGION ?= iad
FLY_VOLUME ?= cfbsicko_data
FLY_VOLUME_SIZE ?= 1
FLY_PUBLIC_APP_URL ?= https://cfbsicko.com
FLY_PUBLIC_URL ?= https://cfbsicko.fly.dev
SEASON ?= 2026

.PHONY: bootstrap run test lint fmt supabase.check \
	fly.app fly.volume fly.secrets fly.deploy fly.status fly.logs fly.certs \
	fly.db-backup fly.db-backup-verify fly.db-restore import-sheet

bootstrap: ## uv sync + .env from example
	$(UV) sync --group dev
	@if [ ! -f .env ]; then cp .env.example .env; printf 'wrote .env from .env.example\n'; fi
	@cd frontend && npm install

run: ## API on HOST:PORT from .env
	$(UV) run cfbsicko serve

test:
	$(UV) run pytest

lint:
	$(UV) run ruff check src tests scripts
	$(UV) run ruff format --check src tests scripts

fmt:
	$(UV) run ruff check --fix src tests scripts
	$(UV) run ruff format src tests scripts

import-sheet:
	$(UV) run cfbsicko import-sheet "data/assets/CFB Locks MASTER SHEET 2026.xlsx"

supabase.check:
	$(UV) run python scripts/check_supabase.py

fly.app:
	$(FLY) apps create $(FLY_APP)

fly.volume:
	$(FLY) volumes create $(FLY_VOLUME) --app $(FLY_APP) --region $(FLY_REGION) --size $(FLY_VOLUME_SIZE) --yes

fly.secrets:
	FLY_APP="$(FLY_APP)" FLY_PUBLIC_APP_URL="$(FLY_PUBLIC_APP_URL)" FLY_BIN="$(FLY)" \
		bash scripts/fly_set_secrets.sh

fly.deploy:
	$(FLY) deploy --app $(FLY_APP)

fly.status:
	$(FLY) status --app $(FLY_APP)

fly.logs:
	$(FLY) logs --app $(FLY_APP)

fly.certs:
	$(FLY) certs add cfbsicko.com --app $(FLY_APP)
	$(FLY) certs add www.cfbsicko.com --app $(FLY_APP)
	$(FLY) certs check cfbsicko.com --app $(FLY_APP)
	$(FLY) certs check www.cfbsicko.com --app $(FLY_APP)

fly.db-backup:
	FLY_APP="$(FLY_APP)" FLY_BIN="$(FLY)" bash scripts/fly_db_backup.sh

fly.db-backup-verify:
	$(UV) run python scripts/fly_db_backup_verify.py $(BACKUP)

fly.db-restore:
	FLY_APP="$(FLY_APP)" FLY_BIN="$(FLY)" CONFIRM="$(CONFIRM)" CONFIRM_PROD="$(CONFIRM_PROD)" \
	BACKUP="$(BACKUP)" bash scripts/fly_db_restore.sh
