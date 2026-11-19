#!/usr/bin/env bash
# Checks which client address the web image's nginx resolves for each
# NIGHTLOOM_EDGE mode, without Caddy, cloudflared or the API.
#
#   deploy/tests/nginx-real-ip.sh [IMAGE]     # default nightloom-web:dev
#
# Starts the image on a throwaway Docker network with a fixed subnet and sends
# requests from small client containers at fixed addresses: one standing in
# for the trusted edge proxy and one for any other container. The address
# nginx resolved is read back from its access log (the same $remote_addr it
# passes to the API in X-Real-IP and X-Forwarded-For). Needs only Docker.
set -euo pipefail

IMAGE=${1:-nightloom-web:dev}
# A subnet unlikely to clash with anything on the host; override if it does.
PREFIX=${NIGHTLOOM_TEST_NET_PREFIX:-10.213.47}
NETWORK="nightloom-realip-test-$$"
WEB="nightloom-realip-web-$$"
CADDY_IP="$PREFIX.11"
CLOUDFLARED_IP="$PREFIX.12"
OTHER_IP="$PREFIX.99"
WEB_IP="$PREFIX.10"
CLIENT_IP="203.0.113.7"
SPOOFED_IP="198.51.100.66"

failures=0
probe=0

cleanup() {
  docker rm -f "$WEB" >/dev/null 2>&1 || true
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker network create --subnet "$PREFIX.0/24" "$NETWORK" >/dev/null

start_web() { # EDGE
  docker rm -f "$WEB" >/dev/null 2>&1 || true
  docker run -d --name "$WEB" --network "$NETWORK" --ip "$WEB_IP" \
    -e NIGHTLOOM_EDGE="$1" -e NIGHTLOOM_CADDY_IP="$CADDY_IP" \
    -e NIGHTLOOM_CLOUDFLARED_IP="$CLOUDFLARED_IP" "$IMAGE" >/dev/null
}

wait_web() {
  for _ in $(seq 1 50); do
    if docker exec "$WEB" wget -q -O /dev/null http://127.0.0.1:8080/healthz 2>/dev/null; then
      return 0
    fi
    sleep 0.2
  done
  echo "nginx did not start:" >&2
  docker logs "$WEB" >&2
  return 1
}

# expect DESCRIPTION FROM_IP EXPECTED_REMOTE_ADDR [HEADER...]
expect() {
  local description=$1 from=$2 expected=$3
  shift 3
  probe=$((probe + 1))
  local path="/realip-probe-$probe"
  local args=()
  for header in "$@"; do
    args+=(--header "$header")
  done
  docker run --rm --network "$NETWORK" --ip "$from" --entrypoint wget "$IMAGE" \
    -q -O /dev/null "${args[@]}" "http://$WEB_IP:8080$path"
  local seen
  seen=$(docker logs "$WEB" 2>/dev/null | awk -v p="GET $path " 'index($0, p) { print $1 }' | tail -n 1)
  if [[ "$seen" == "$expected" ]]; then
    echo "ok    $description: $seen"
  else
    echo "FAIL  $description: expected $expected, nginx saw ${seen:-nothing}"
    failures=$((failures + 1))
  fi
}

echo "== NIGHTLOOM_EDGE=caddy (trusts $CADDY_IP, X-Forwarded-For)"
start_web caddy
wait_web
expect "from Caddy, client appended by Caddy" "$CADDY_IP" "$CLIENT_IP" \
  "X-Forwarded-For: $SPOOFED_IP, $CLIENT_IP"
expect "from Caddy, single address" "$CADDY_IP" "$CLIENT_IP" "X-Forwarded-For: $CLIENT_IP"
expect "from another container, spoofed X-Forwarded-For" "$OTHER_IP" "$OTHER_IP" \
  "X-Forwarded-For: $SPOOFED_IP"
expect "from cloudflared's address, CF-Connecting-IP ignored" "$CLOUDFLARED_IP" "$CLOUDFLARED_IP" \
  "CF-Connecting-IP: $SPOOFED_IP" "X-Forwarded-For: $SPOOFED_IP"

echo "== NIGHTLOOM_EDGE=cloudflared (trusts $CLOUDFLARED_IP, CF-Connecting-IP)"
start_web cloudflared
wait_web
expect "from cloudflared" "$CLOUDFLARED_IP" "$CLIENT_IP" \
  "CF-Connecting-IP: $CLIENT_IP" "X-Forwarded-For: $SPOOFED_IP, $CLIENT_IP"
expect "from another container, spoofed CF-Connecting-IP" "$OTHER_IP" "$OTHER_IP" \
  "CF-Connecting-IP: $SPOOFED_IP"
expect "from Caddy's address, X-Forwarded-For ignored" "$CADDY_IP" "$CADDY_IP" \
  "X-Forwarded-For: $SPOOFED_IP"

echo "== NIGHTLOOM_EDGE=none (development default: nothing trusted)"
start_web none
wait_web
expect "spoofed X-Forwarded-For" "$CADDY_IP" "$CADDY_IP" "X-Forwarded-For: $SPOOFED_IP"
expect "spoofed CF-Connecting-IP" "$CLOUDFLARED_IP" "$CLOUDFLARED_IP" "CF-Connecting-IP: $SPOOFED_IP"

echo "== NIGHTLOOM_EDGE=bogus (must refuse to start)"
start_web bogus
sleep 2
if [[ "$(docker inspect -f '{{.State.Running}}' "$WEB")" == "false" ]]; then
  echo "ok    nginx exited: $(docker logs "$WEB" 2>&1 | grep -m1 -o 'open() .* failed' || echo 'see logs')"
else
  echo "FAIL  nginx started with an unknown NIGHTLOOM_EDGE"
  failures=$((failures + 1))
fi

if ((failures > 0)); then
  echo "$failures check(s) failed."
  exit 1
fi
echo "All real-IP checks passed."
