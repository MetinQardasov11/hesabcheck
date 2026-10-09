#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
ROOT=/root/hesabcheck
export RELEASE_SHA=$(cat "$ROOT/current-sha")
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP="$ROOT/backups/daily-$STAMP"
mkdir -p "$BACKUP"
docker compose --project-name hesabcheck --env-file "$ROOT/.env" -f "$ROOT/current/deploy/compose.yaml" \
  exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$BACKUP/database.dump"
tar -czf "$BACKUP/media.tar.gz" -C "$ROOT" media
cp "$ROOT/.env" "$BACKUP/production.env"
cp "$ROOT/current-sha" "$BACKUP/release-sha"
find "$ROOT/backups" -maxdepth 1 -type d -name 'daily-*' -mtime +14 -exec rm -rf -- {} +
