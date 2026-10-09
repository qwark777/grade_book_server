#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
BACKUP_DIR="${BACKUP_DIR:-backups}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
PREFIX="${BACKUP_DIR}/gradebook_${TIMESTAMP}"

mkdir -p "${BACKUP_DIR}"

docker compose -f "${COMPOSE_FILE}" exec -T db sh -c \
  'mysqldump --single-transaction --routines --triggers --no-tablespaces -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE"' \
  | gzip -c > "${PREFIX}.sql.gz"

docker compose -f "${COMPOSE_FILE}" exec -T app tar -C /app -czf - \
  profile_photos achievements homework_submissions homework_attachments message_attachments \
  > "${PREFIX}_files.tar.gz"

{
  echo "created_at=${TIMESTAMP}"
  echo "compose_file=${COMPOSE_FILE}"
  echo "database=${PREFIX}.sql.gz"
  echo "files=${PREFIX}_files.tar.gz"
  shasum -a 256 "${PREFIX}.sql.gz" "${PREFIX}_files.tar.gz"
} > "${PREFIX}.manifest"

echo "Backup written:"
echo "  ${PREFIX}.sql.gz"
echo "  ${PREFIX}_files.tar.gz"
echo "  ${PREFIX}.manifest"
