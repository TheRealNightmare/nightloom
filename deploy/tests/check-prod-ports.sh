#!/usr/bin/env bash
# Checks the production Compose configurations: only the edge publishes ports.
#
#   deploy/tests/check-prod-ports.sh [ENV_FILE]     # default .env.example
#
# Renders compose.yaml + compose.prod.yaml (alone, with each edge profile, and
# with compose.small.yaml) and fails if any service other than Caddy
# publishes a port, if Caddy publishes anything but 80 and 443, or if the
# tunnel variant publishes anything at all. Needs Docker Compose and python3.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."
env_file=${1:-.env.example}
base=(docker compose -f deploy/compose.yaml -f deploy/compose.prod.yaml)
failures=0

check() { # NAME ALLOWED_PUBLISHED(JSON: {"service": ["80/tcp", ...]}) COMPOSE_ARGS...
  local name=$1 allowed=$2
  shift 2
  local result
  if ! result=$(NIGHTLOOM_EDGE=caddy COMPOSE_PROFILES='' "${base[@]}" "$@" --env-file "$env_file" \
    config --format json | python3 -I -c '
import json, sys
config = json.load(sys.stdin)
allowed = json.loads(sys.argv[1])
def describe(port):
    return "%s:%s->%s/%s" % (port.get("host_ip", "0.0.0.0"), port["published"],
                             port["target"], port.get("protocol", "tcp"))
published = {}
for name, service in sorted(config["services"].items()):
    ports = [describe(p) for p in service.get("ports") or [] if p.get("published")]
    if ports:
        published[name] = ports
print("  services: " + ", ".join(sorted(config["services"])))
print("  published: " + (json.dumps(published) if published else "nothing"))
bad = {n: p for n, p in published.items()
       if sorted(x.split(":", 1)[1] for x in p) != sorted(allowed.get(n, []))}
missing = [n for n in allowed if n not in published]
if bad or missing:
    print(f"  UNEXPECTED: {bad or missing}")
    sys.exit(1)
' "$allowed"); then
    echo "FAIL  $name"
    echo "$result"
    failures=$((failures + 1))
  else
    echo "ok    $name"
    echo "$result"
  fi
}

edge='{"caddy": ["80->80/tcp", "443->443/tcp", "443->443/udp"]}'
check "prod (no profile)" '{}'
check "prod + https" "$edge" --profile https
check "prod + tunnel" '{}' --profile tunnel
check "prod + small + https" "$edge" -f deploy/compose.small.yaml --profile https
check "prod + small + tunnel" '{}' -f deploy/compose.small.yaml --profile tunnel

if ((failures > 0)); then
  echo "$failures configuration(s) publish more than the edge."
  exit 1
fi
echo "Only the edge publishes ports in every production configuration."
