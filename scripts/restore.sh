#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: COMPOSE_FILE=docker-compose.prod.yml CONFIRM_RESTORE=yes $0 backups/gradebook_YYYYMMDDTHHMMSSZ"
  exit 2
fi

if [[ "${CONFIRM_RESTORE:-}" != "yes" ]]; then
  echo "Refusing to restore without CONFIRM_RESTORE=yes"
  exit 2
fi

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
PREFIX="$1"
DB_BACKUP="${PREFIX}.sql.gz"
FILES_BACKUP="${PREFIX}_files.tar.gz"

if [[ ! -f "${DB_BACKUP}" ]]; then
  echo "Database backup not found: ${DB_BACKUP}"
  exit 2
fi

if [[ ! -f "${FILES_BACKUP}" ]]; then
  echo "Files backup not found: ${FILES_BACKUP}"
  exit 2
fi

gunzip -c "${DB_BACKUP}" | docker compose -f "${COMPOSE_FILE}" exec -T db sh -c \
  'mysql -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE"'

docker compose -f "${COMPOSE_FILE}" exec -T app sh -c \
  'rm -rf /app/profile_photos/* /app/achievements/* /app/homework_submissions/* /app/homework_attachments/* /app/message_attachments/*'

docker compose -f "${COMPOSE_FILE}" exec -T app tar -C /app -xzf - < "${FILES_BACKUP}"

echo "Restore complete from ${PREFIX}"
