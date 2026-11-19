#!/usr/bin/env bash
# Backup and restore round trip (`make test-backup-restore`). DESTRUCTIVE:
# deletes every volume of the stack in between. Meant for CI and throwaway
# stacks; refuses to run unless NIGHTLOOM_TEST_DESTRUCTIVE=1.
#
#  1. create (or reuse) an admin, seed a case with entities, a relationship
#     and audit events through the API, and put an object in the bucket
#  2. make a backup (scripts/backup.sh)
#  3. `docker compose down -v`: every container and volume is gone
#  4. start an empty stack, restore the backup (scripts/restore.sh)
#  5. check the case, its graph, the audit chain (POST /audit/verify) and the
#     object are back
#
# The API is reached at NIGHTLOOM_TEST_BASE_URL (default: the published web
# port of the development stack). With the production stack and the HTTPS
# test edge (EDGE_TEST=1), it is Caddy on 127.0.0.1:8443, verified with
# Caddy's internal CA, which is fetched again after the restore (the wipe
# also deletes Caddy's CA).
#
# Environment: NIGHTLOOM_COMPOSE, ENV_FILE (as for backup.sh),
# NIGHTLOOM_TEST_ADMIN_EMAIL / NIGHTLOOM_TEST_ADMIN_PASSWORD (a throwaway admin).
set -euo pipefail

if [[ "${NIGHTLOOM_TEST_DESTRUCTIVE:-}" != 1 ]]; then
  echo "This deletes ALL data of the stack. Set NIGHTLOOM_TEST_DESTRUCTIVE=1 to run it." >&2
  exit 1
fi

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"
ENV_FILE=${ENV_FILE:-.env}
[[ "$ENV_FILE" == /* ]] || ENV_FILE="./$ENV_FILE"
read -r -a compose <<<"${NIGHTLOOM_COMPOSE:-docker compose -f deploy/compose.yaml --env-file $ENV_FILE}"
compose+=(--progress quiet)
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

: "${NIGHTLOOM_TEST_ADMIN_EMAIL:?set NIGHTLOOM_TEST_ADMIN_EMAIL (a throwaway admin)}"
: "${NIGHTLOOM_TEST_ADMIN_PASSWORD:?set NIGHTLOOM_TEST_ADMIN_PASSWORD}"
export NIGHTLOOM_TEST_BASE_URL=${NIGHTLOOM_TEST_BASE_URL:-http://127.0.0.1:${WEB_HOST_PORT:-8080}}

work=$(mktemp -d)
trap 'rm -rf -- "${work:?}"' EXIT
state="$work/state.json"
object_key="round-trip/$(date -u +%Y%m%dT%H%M%SZ)-$$.txt"
object_body="backup round trip object $(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')"

step() { printf '\n== %s\n' "$*"; }

fetch_ca() {
  if [[ "$NIGHTLOOM_TEST_BASE_URL" == https://* ]]; then
    "${compose[@]}" cp caddy:/data/caddy/pki/authorities/local/root.crt "$work/ca.crt" >/dev/null 2>&1
    export NIGHTLOOM_TEST_CA="$work/ca.crt"
    export NIGHTLOOM_TEST_HOST=${NIGHTLOOM_TEST_HOST:-$NIGHTLOOM_DOMAIN}
  fi
}

check() { uv run --no-sync python -I scripts/tests/backup_restore_check.py "$@"; }

step "Admin and seed data"
if out=$(printf '%s\n' "$NIGHTLOOM_TEST_ADMIN_PASSWORD" | "${compose[@]}" run --rm --no-deps -T api \
  python -m nightloom_api.cli create-admin --email "$NIGHTLOOM_TEST_ADMIN_EMAIL" \
  --display-name "Backup test admin" --password-stdin 2>&1); then
  echo "Created the admin $NIGHTLOOM_TEST_ADMIN_EMAIL."
elif grep -q 'already exists' <<<"$out"; then
  echo "Reusing the admin $NIGHTLOOM_TEST_ADMIN_EMAIL."
else
  printf '%s\n' "$out" >&2
  exit 1
fi
fetch_ca
check seed --state "$state"
printf '%s' "$object_body" | "${compose[@]}" run --rm --no-deps -T s3-tools \
  rcat "store:$NIGHTLOOM_S3_BUCKET/$object_key"
echo "Stored s3://$NIGHTLOOM_S3_BUCKET/$object_key."

step "Backup"
backup_output=$(scripts/backup.sh | tee /dev/stderr)
archive=$(sed -n 's/^Backup complete: \(.*\.tar\.gz\) (.*)$/\1/p' <<<"$backup_output")
[[ -f "$archive" ]] || { echo "No archive found in the backup output." >&2; exit 1; }

step "Wipe: delete every container and volume"
"${compose[@]}" down -v --remove-orphans
if "${compose[@]}" config --volumes | while read -r volume; do
  docker volume inspect "${COMPOSE_PROJECT_NAME:-nightloom}_$volume" >/dev/null 2>&1 && exit 0
done; then
  echo "A volume survived the wipe." >&2
  exit 1
fi
echo "All volumes deleted."

step "Empty stack"
"${compose[@]}" up -d --wait
fetch_ca
if check verify --state "$state" >/dev/null 2>&1; then
  echo "The seeded data is still there after the wipe; the test would prove nothing." >&2
  exit 1
fi
echo "The seeded data is gone, as expected."

step "Restore"
echo restore | scripts/restore.sh "$archive"

step "Verify"
fetch_ca
check verify --state "$state"
restored=$("${compose[@]}" run --rm --no-deps -T s3-tools cat "store:$NIGHTLOOM_S3_BUCKET/$object_key")
if [[ "$restored" != "$object_body" ]]; then
  echo "The bucket object did not come back intact." >&2
  exit 1
fi
echo "Object s3://$NIGHTLOOM_S3_BUCKET/$object_key is back, byte for byte."
echo
echo "Backup and restore round trip passed."
