#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
ROOT=/root/hesabcheck
SHA=${1:?commit SHA required}
[[ "$SHA" =~ ^[a-f0-9]{40}$ ]] || exit 64
export RELEASE_SHA=$SHA
RELEASE="$ROOT/releases/$SHA"
COMPOSE=(docker compose --project-name hesabcheck --env-file "$ROOT/.env" -f "$RELEASE/deploy/compose.yaml")
PREVIOUS=$(cat "$ROOT/current-sha" 2>/dev/null || true)
"${COMPOSE[@]}" config --quiet
"${COMPOSE[@]}" up -d --wait --wait-timeout 120 db
mkdir -p "$ROOT/backups"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
"${COMPOSE[@]}" exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$ROOT/backups/predeploy-$STAMP.dump"
"${COMPOSE[@]}" run --rm --no-deps web python manage.py check --deploy --fail-level WARNING
"${COMPOSE[@]}" run --rm --no-deps web python manage.py migrate --noinput
if ! "${COMPOSE[@]}" up -d --no-deps --wait --wait-timeout 150 web; then
  echo 'New release failed health check.' >&2
  if [[ -n "$PREVIOUS" && "$PREVIOUS" != "$SHA" ]]; then
    echo "Restoring previous application image: $PREVIOUS" >&2
    RELEASE_SHA="$PREVIOUS" docker compose --project-name hesabcheck --env-file "$ROOT/.env" \
      -f "$ROOT/releases/$PREVIOUS/deploy/compose.yaml" up -d --no-deps --wait --wait-timeout 150 web
  fi
  echo 'Database is not automatically restored. Migrations must be backward compatible; predeploy backup is retained.' >&2
  exit 1
fi
WEB_ID=$("${COMPOSE[@]}" ps -q web)
docker exec "$WEB_ID" python -c "import urllib.request,json; r=urllib.request.Request('http://127.0.0.1:8000/health/',headers={'Host':'history.testgrelo.online','X-Forwarded-Proto':'https'}); d=json.load(urllib.request.urlopen(r,timeout=5)); assert d['release']=='$SHA'"
python3 "$RELEASE/deploy/configure_proxy.py"
ln -sfn "$RELEASE" "$ROOT/current"
printf '%s\n' "$PREVIOUS" > "$ROOT/previous-sha"
printf '%s\n' "$SHA" > "$ROOT/current-sha"
# Keep the latest five source releases and their images. Never prune unrelated projects.
python3 - "$ROOT" "$SHA" "$PREVIOUS" <<'PY'
import pathlib, re, shutil, subprocess, sys
root = pathlib.Path(sys.argv[1])
releases = sorted((p for p in (root/'releases').iterdir() if p.is_dir() and re.fullmatch('[a-f0-9]{40}', p.name)), key=lambda p: p.stat().st_mtime, reverse=True)
keep = {p.name for p in releases[:5]} | set(sys.argv[2:])
for path in releases:
    if path.name not in keep:
        subprocess.run(['docker', 'image', 'rm', 'hesabcheck:' + path.name], check=False)
        shutil.rmtree(path)
PY
find "$ROOT/backups" -maxdepth 1 -type f -name 'predeploy-*.dump' -mtime +14 -delete
echo "Deployed $SHA successfully."
