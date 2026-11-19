#!/usr/bin/env bash
# Restores a backup made by scripts/backup.sh (`make restore BACKUP=...`).
#
#   scripts/restore.sh backups/nightloom-<timestamp>.tar.gz
#
# REPLACES all current data: the PostgreSQL database is dropped and recreated,
# the Neo4j database is overwritten and the evidence bucket is synced to the
# backup's copy (objects not in the backup are deleted). Steps:
#   1. unpack into a temporary directory and verify SHA256SUMS
#   2. ask you to type "restore"
#   3. stop api, worker and web; start postgres, valkey and seaweedfs
#   4. restore PostgreSQL (drop/recreate), Neo4j (load into the stopped
#      database) and the bucket
#   5. start everything, apply migrations (as `make migrate` does) and check
#      that GET /api/v1/ready answers 200
#
# Works on a fresh server too (empty volumes): put .env back first (backups
# never contain it), then run this. The passwords in .env are the current
# ones; the backup holds data, not credentials.
#
# Environment: NIGHTLOOM_COMPOSE, ENV_FILE (as for backup.sh).
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"

archive=${1:-}
if [[ -z "$archive" || ! -f "$archive" ]]; then
  echo "Usage: $0 backups/nightloom-<timestamp>.tar.gz" >&2
  exit 2
fi
archive=$(cd "$(dirname "$archive")" && pwd)/$(basename "$archive")

ENV_FILE=${ENV_FILE:-.env}
[[ "$ENV_FILE" == /* ]] || ENV_FILE="./$ENV_FILE"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "No $ENV_FILE: put the deployment's .env back first (backups never contain it)." >&2
  exit 1
fi
read -r -a compose <<<"${NIGHTLOOM_COMPOSE:-docker compose -f deploy/compose.yaml --env-file $ENV_FILE}"
compose+=(--progress quiet)

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

log() { printf '[restore %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

backup_root=${NIGHTLOOM_BACKUP_DIR:-backups}
mkdir -p "$backup_root"
scratch=$(mktemp -d "$(cd "$backup_root" && pwd)/.restore.XXXXXX")
trap 'rm -rf -- "${scratch:?}"' EXIT

# --- 1. unpack and verify -----------------------------------------------------
log "Unpacking $(basename "$archive")."
tar -xzf "$archive" -C "$scratch" --no-same-owner --no-same-permissions
mapfile -t dirs < <(find "$scratch" -mindepth 1 -maxdepth 1 -type d -name 'nightloom-*')
if ((${#dirs[@]} != 1)); then
  echo "Not a Nightloom backup: expected one nightloom-* directory in the archive." >&2
  exit 1
fi
dir=${dirs[0]}
for required in SHA256SUMS manifest.json postgres.dump neo4j.dump; do
  if [[ ! -f "$dir/$required" ]]; then
    echo "Not a complete backup: $required is missing." >&2
    exit 1
  fi
done
mkdir -p "$dir/s3"

log "Verifying checksums."
if ! (cd "$dir" && sha256sum --check --quiet --strict SHA256SUMS); then
  echo "Checksum verification FAILED: the backup is damaged or was modified. Nothing was changed." >&2
  exit 1
fi
# Every file must be covered: nothing unchecked may be restored.
listed=$(cd "$dir" && sed -E 's/^[0-9a-f]{64}  //' SHA256SUMS | LC_ALL=C sort)
present=$(cd "$dir" && find . -type f ! -name SHA256SUMS | LC_ALL=C sort)
if [[ "$listed" != "$present" ]]; then
  echo "The backup contains files that SHA256SUMS does not list. Nothing was changed." >&2
  exit 1
fi

echo
echo "Backup: $(basename "$dir")"
sed -n 's/^  "\(finished_at\|counts\|nightloom\)": /  \1: /p' "$dir/manifest.json"
echo
echo "This REPLACES ALL current Nightloom data (PostgreSQL, Neo4j and the bucket"
echo "$NIGHTLOOM_S3_BUCKET) with the contents of this backup. It cannot be undone."
printf 'Type "restore" to continue: '
read -r answer || answer=""
if [[ "$answer" != "restore" ]]; then
  echo "Aborted; nothing was changed."
  exit 1
fi

# --- 3. stop the application, start the stores --------------------------------
log "Stopping api, worker and web."
"${compose[@]}" stop api worker web >/dev/null 2>&1 || true
log "Starting postgres, valkey and seaweedfs."
"${compose[@]}" up -d --wait postgres valkey seaweedfs
"${compose[@]}" stop neo4j >/dev/null 2>&1 || true

# --- 4a. PostgreSQL -------------------------------------------------------------
log "Restoring PostgreSQL (drop and recreate $POSTGRES_DB)."
pg() { "${compose[@]}" exec -T postgres "$@"; }
pg dropdb -U "$POSTGRES_USER" --if-exists --force "$POSTGRES_DB"
pg createdb -U "$POSTGRES_USER" -O "$POSTGRES_USER" "$POSTGRES_DB"
pg pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --exit-on-error --no-owner \
  --role="$POSTGRES_USER" <"$dir/postgres.dump"

# --- 4b. Neo4j (offline load into the stopped database) -------------------------
log "Restoring Neo4j."
# (neo4j-admin's per-percent progress lines are dropped.)
"${compose[@]}" run --rm --no-deps -T neo4j \
  neo4j-admin database load --from-stdin --overwrite-destination=true neo4j \
  <"$dir/neo4j.dump" 2>&1 | sed -u '/^Files: [0-9]*\/[0-9]*, data:/d'
"${compose[@]}" up -d --wait neo4j

# --- 4c. object storage ---------------------------------------------------------
log "Restoring bucket $NIGHTLOOM_S3_BUCKET."
"${compose[@]}" run --rm --no-deps -T --user "$(id -u):$(id -g)" -e XDG_CACHE_HOME=/tmp \
  -v "$dir/s3:/backup:ro" s3-tools sync --metadata /backup "store:$NIGHTLOOM_S3_BUCKET"

# --- 5. start everything, migrate, check readiness ------------------------------
log "Starting the stack."
"${compose[@]}" up -d --wait
log "Applying migrations (a backup from an older version is brought up to date)."
"${compose[@]}" run --rm migrate

log "Checking readiness."
ready=false
for _ in $(seq 1 30); do
  if body=$("${compose[@]}" exec -T api python -c "
import sys, urllib.request
try:
    response = urllib.request.urlopen('http://127.0.0.1:8000/api/v1/ready', timeout=5)
except Exception as error:
    sys.exit(f'not ready: {error}')
print(response.read().decode())
" 2>&1); then
    ready=true
    break
  fi
  sleep 2
done
if [[ "$ready" != true ]]; then
  echo "Restored, but GET /api/v1/ready did not answer 200: $body" >&2
  exit 1
fi
echo "GET /api/v1/ready: $body"
echo "Restore complete from $(basename "$archive")."
