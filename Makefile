.PHONY: doctor test compile audit security ci predeploy migrate migration-stamp prod-up prod-down backup restore

PYTHON ?= python3
COMPOSE_FILE ?= docker-compose.prod.yml

doctor:
	$(PYTHON) scripts/doctor.py

test:
	SECRET_KEY=$${SECRET_KEY:-test_secret_key_123456789012345678901234567890} \
	ENCRYPTION_KEY=$${ENCRYPTION_KEY:-ICkoftk-wbOx89vzo2nuGkPatHZCQ1IKBVpFdRJ1F4k=} \
	$(PYTHON) -m unittest test_security_access.py test_http_security.py test_file_upload_security.py test_health.py -v

compile:
	SECRET_KEY=$${SECRET_KEY:-test_secret_key_123456789012345678901234567890} \
	ENCRYPTION_KEY=$${ENCRYPTION_KEY:-ICkoftk-wbOx89vzo2nuGkPatHZCQ1IKBVpFdRJ1F4k=} \
	$(PYTHON) -m compileall app

audit:
	pip-audit -r requirements.txt

security:
	semgrep --no-git-ignore --config p/security-audit --config p/python app
	bandit -q -r app -x app/models/advice_model.pkl,app/models/advice_scaler.pkl -s B608

ci:
	bash scripts/security_check.sh

predeploy:
	bash scripts/predeploy_check.sh

migrate:
	$(PYTHON) -m alembic upgrade head

migration-stamp:
	$(PYTHON) -m alembic stamp 20260611_0001

prod-up:
	docker compose -f $(COMPOSE_FILE) --env-file .env.production up -d --build

prod-down:
	docker compose -f $(COMPOSE_FILE) --env-file .env.production down

backup:
	COMPOSE_FILE=$(COMPOSE_FILE) bash scripts/backup.sh

restore:
	@test -n "$(BACKUP)" || (echo "Usage: make restore BACKUP=backups/gradebook_YYYYMMDDTHHMMSSZ CONFIRM_RESTORE=yes" && exit 2)
	COMPOSE_FILE=$(COMPOSE_FILE) CONFIRM_RESTORE=$(CONFIRM_RESTORE) bash scripts/restore.sh $(BACKUP)
