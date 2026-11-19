#!/usr/bin/env bash
# Backs up a running Nightloom stack (`make backup`).
#
# Creates NIGHTLOOM_BACKUP_DIR/nightloom-<UTC timestamp>.tar.gz (default
# directory: backups/) containing:
#   postgres.dump   pg_dump custom format (users, cases, jobs, audit log)
#   neo4j.dump      neo4j-admin database dump of the graph database
#   s3/             every object in the evidence bucket, copied over the S3 API
#   manifest.json   versions, timestamps, counts and image tags
#   SHA256SUMS      checksums of all the files above (checked by restore)
# and keeps the newest NIGHTLOOM_BACKUP_KEEP archives (default 7). .env is
# never included: keep a copy of it somewhere safe yourself.
#
# The API and the worker are stopped for the duration, so PostgreSQL, Neo4j
# and the bucket are captured at the same moment (the web UI shows an error
# meanwhile). Neo4j Community Edition can only be dumped while stopped, so it
# is stopped too. Whatever happens, the services that were running are
# started again (trap). Valkey (job queue, rate-limit counters) is not backed
# up: it holds nothing that must survive.
#
# Environment: NIGHTLOOM_COMPOSE (the `docker compose ...` command; the
# Makefile passes the same one as every other target) and ENV_FILE.
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"

ENV_FILE=${ENV_FILE:-.env}
[[ "$ENV_FILE" == /* ]] || ENV_FILE="./$ENV_FILE"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "No $ENV_FILE: run this from the deployment's checkout." >&2
  exit 1
fi
read -r -a compose <<<"${NIGHTLOOM_COMPOSE:-docker compose -f deploy/compose.yaml --env-file $ENV_FILE}"
# No progress bars: this also runs from cron.
compose+=(--progress quiet)

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

keep=${NIGHTLOOM_BACKUP_KEEP:-7}
if ! [[ "$keep" =~ ^[1-9][0-9]*$ ]]; then
  echo "NIGHTLOOM_BACKUP_KEEP must be a positive number, not '$keep'." >&2
  exit 1
fi
backup_root=${NIGHTLOOM_BACKUP_DIR:-backups}
mkdir -p "$backup_root"
backup_root=$(cd "$backup_root" && pwd)

log() { printf '[backup %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

running() { # SERVICE -> true if its container is running
  [[ -n "$("${compose[@]}" ps --status running --quiet "$1" 2>/dev/null)" ]]
}

wait_healthy() { # SERVICE [SECONDS]
  local service=$1 deadline=$((SECONDS + ${2:-180})) id state
  while ((SECONDS < deadline)); do
    id=$("${compose[@]}" ps --quiet "$service" 2>/dev/null || true)
    if [[ -n "$id" ]]; then
      state=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$id" 2>/dev/null || true)
      [[ "$state" == healthy || "$state" == running ]] && return 0
    fi
    sleep 2
  done
  echo "$service did not become healthy in time." >&2
  return 1
}

for service in postgres neo4j seaweedfs; do
  if ! running "$service"; then
    echo "$service is not running. Start the stack first (make up)." >&2
    exit 1
  fi
done

umask 077
started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
name="nightloom-$(date -u +%Y%m%dT%H%M%SZ)"
work="$backup_root/$name"
mkdir "$work" "$work/s3"

# --- stop writers; restart everything on exit, success or not ---------------
stopped_apps=()
neo4j_stopped=false
finished=false

cleanup() {
  local status=$?
  set +e
  if [[ "$neo4j_stopped" == true ]]; then
    log "Starting neo4j again."
    "${compose[@]}" start neo4j >/dev/null && wait_healthy neo4j
  fi
  if ((${#stopped_apps[@]} > 0)); then
    log "Starting ${stopped_apps[*]} again."
    "${compose[@]}" start "${stopped_apps[@]}" >/dev/null
  fi
  if [[ "$finished" != true ]]; then
    rm -rf -- "${work:?}" "${work:?}.sums" "${work:?}.tar.gz.partial"
    echo "Backup FAILED (exit $status); nothing was kept." >&2
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

for service in api worker; do
  if running "$service"; then
    stopped_apps+=("$service")
  fi
done
if ((${#stopped_apps[@]} > 0)); then
  log "Stopping ${stopped_apps[*]} so nothing writes during the backup."
  "${compose[@]}" stop "${stopped_apps[@]}" >/dev/null
fi

psql() { # SQL -> single value
  "${compose[@]}" exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -AtX -c "$1"
}
cypher() { # QUERY -> single value
  "${compose[@]}" exec -T -e NEO4J_USERNAME=neo4j -e NEO4J_PASSWORD neo4j \
    cypher-shell --format plain "$1" | tail -n 1 | tr -d '"'
}
s3() { # rclone ARGS... (as the invoking user, so the files are ours)
  "${compose[@]}" run --rm --no-deps -T --user "$(id -u):$(id -g)" \
    -e XDG_CACHE_HOME=/tmp -v "$work/s3:/backup" s3-tools "$@"
}

# --- facts for the manifest (graph counts need neo4j running) ---------------
log "Collecting versions and counts."
postgres_version=$(psql "SHOW server_version")
schema_revision=$(psql "SELECT version_num FROM alembic_version" 2>/dev/null || echo unknown)
count_users=$(psql "SELECT count(*) FROM users")
count_cases=$(psql "SELECT count(*) FROM cases")
count_jobs=$(psql "SELECT count(*) FROM jobs")
count_audit=$(psql "SELECT count(*) FROM audit_events")
neo4j_version=$(cypher "CALL dbms.components() YIELD name, versions WHERE name = 'Neo4j Kernel' RETURN versions[0]")
count_entities=$(cypher "MATCH (n:Entity) RETURN count(n)")
count_relationships=$(cypher "MATCH (:Entity)-[r]->(:Entity) RETURN count(r)")

# --- PostgreSQL ---------------------------------------------------------------
log "Dumping PostgreSQL."
"${compose[@]}" exec -T postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc \
  >"$work/postgres.dump"

# --- object storage -------------------------------------------------------------
log "Copying bucket $NIGHTLOOM_S3_BUCKET."
s3 copy --metadata "store:$NIGHTLOOM_S3_BUCKET" /backup
count_objects=$(find "$work/s3" -type f | wc -l | tr -d ' ')

# --- Neo4j (Community Edition: offline dump) ------------------------------------
log "Stopping neo4j for an offline dump."
"${compose[@]}" stop neo4j >/dev/null
neo4j_stopped=true
"${compose[@]}" run --rm --no-deps -T neo4j \
  neo4j-admin database dump --to-stdout neo4j >"$work/neo4j.dump" \
  2> >(sed -u '/^Files: [0-9]*\/[0-9]*, data:/d' >&2)
log "Starting neo4j again."
"${compose[@]}" start neo4j >/dev/null
wait_healthy neo4j
neo4j_stopped=false

if ((${#stopped_apps[@]} > 0)); then
  log "Starting ${stopped_apps[*]} again."
  "${compose[@]}" start "${stopped_apps[@]}" >/dev/null
  stopped_apps=()
fi

# --- manifest and checksums ---------------------------------------------------
json_string() { # VALUE -> JSON string (backslashes, quotes and control characters escaped)
  local value=${1//\\/\\\\}
  value=${value//\"/\\\"}
  printf '"%s"' "$(printf '%s' "$value" | tr -d '\000-\037')"
}
json_number() { [[ "$1" =~ ^[0-9]+$ ]] && printf '%s' "$1" || printf 'null'; }

images=()
while IFS= read -r image; do
  [[ -n "$image" ]] && images+=("$(json_string "$image")")
done < <("${compose[@]}" config --images | sort -u)
app_version=$(sed -n 's/^version = "\(.*\)"$/\1/p' apps/api/pyproject.toml | head -n 1)
git_commit=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)
finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)

{
  printf '{\n'
  printf '  "format": 1,\n'
  printf '  "name": %s,\n' "$(json_string "$name")"
  printf '  "started_at": %s,\n' "$(json_string "$started_at")"
  printf '  "finished_at": %s,\n' "$(json_string "$finished_at")"
  printf '  "nightloom": {"version": %s, "git_commit": %s, "schema_revision": %s},\n' \
    "$(json_string "$app_version")" "$(json_string "$git_commit")" "$(json_string "$schema_revision")"
  printf '  "versions": {"postgres": %s, "neo4j": %s},\n' \
    "$(json_string "$postgres_version")" "$(json_string "$neo4j_version")"
  printf '  "counts": {"users": %s, "cases": %s, "jobs": %s, "audit_events": %s, "entities": %s, "relationships": %s, "objects": %s},\n' \
    "$(json_number "$count_users")" "$(json_number "$count_cases")" "$(json_number "$count_jobs")" \
    "$(json_number "$count_audit")" "$(json_number "$count_entities")" \
    "$(json_number "$count_relationships")" "$(json_number "$count_objects")"
  printf '  "bucket": %s,\n' "$(json_string "$NIGHTLOOM_S3_BUCKET")"
  printf '  "images": [%s]\n' "$(IFS=,; echo "${images[*]}")"
  printf '}\n'
} >"$work/manifest.json"

(cd "$work" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum) >"$work.sums"
mv "$work.sums" "$work/SHA256SUMS"

# --- archive and rotation -------------------------------------------------------
log "Writing $name.tar.gz."
tar -C "$backup_root" -czf "$work.tar.gz.partial" "$name"
mv "$work.tar.gz.partial" "$work.tar.gz"
rm -rf -- "${work:?}"
finished=true

mapfile -t archives < <(find "$backup_root" -maxdepth 1 -name 'nightloom-*.tar.gz' -type f | LC_ALL=C sort)
if ((${#archives[@]} > keep)); then
  for old in "${archives[@]:0:${#archives[@]}-keep}"; do
    log "Removing old backup $(basename "$old") (keeping the newest $keep)."
    rm -f -- "$old"
  done
fi

size=$(du -h "$work.tar.gz" | cut -f1)
echo "Backup complete: $work.tar.gz ($size)"
echo "  $count_cases cases, $count_entities entities, $count_audit audit events, $count_objects objects."
echo "Copy it off this machine (it holds everything needed to restore except .env)."
