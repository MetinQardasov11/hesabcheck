#!/usr/bin/env bash
# Installed as a forced SSH command. No general-purpose SSH shell is granted.
set -Eeuo pipefail
umask 077
ROOT=/root/hesabcheck
if [[ ! ${SSH_ORIGINAL_COMMAND:-} =~ ^deploy\ ([a-f0-9]{40})$ ]]; then
  echo 'Only deploy <40-character commit SHA> is permitted.' >&2
  exit 64
fi
SHA=${BASH_REMATCH[1]}
exec 9>"$ROOT/deploy.lock"
flock -w 1200 9
WORK=$(mktemp -d "$ROOT/incoming.XXXXXXXX")
trap 'rm -rf "$WORK"' EXIT
# The CI bundle contains exactly these two generated artifacts, never secrets.
tar -xz -C "$WORK" --no-same-owner --no-same-permissions image.tar source.tar.gz
mkdir -p "$ROOT/releases/$SHA"
python3 - "$WORK/source.tar.gz" "$ROOT/releases/$SHA" <<'PY'
import sys, tarfile
with tarfile.open(sys.argv[1]) as archive:
    archive.extractall(sys.argv[2], filter='data')
PY
docker load -i "$WORK/image.tar"
ACTUAL=$(docker image inspect "hesabcheck:$SHA" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
[[ "$ACTUAL" == "$SHA" ]] || { echo 'Image revision mismatch' >&2; exit 1; }
bash "$ROOT/releases/$SHA/deploy/activate.sh" "$SHA"
