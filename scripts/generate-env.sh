#!/usr/bin/env bash
# Creates .env for a production deployment from .env.example (`make secrets`).
#
#   scripts/generate-env.sh [--edge caddy|cloudflared] [--domain NAME]
#                           [--acme-email ADDRESS] [--small] [--force]
#                           [--example PATH] [--output PATH]
#
# - Refuses to touch an existing .env; --force first copies it to
#   .env.backup-<UTC timestamp> (mode 600) and prints a warning.
# - Every `change-me...` placeholder becomes a strong random value (hex, so it
#   is safe inside URLs). The same placeholder gets the same value everywhere,
#   so NIGHTLOOM_DATABASE_URL, NIGHTLOOM_VALKEY_URL and
#   NIGHTLOOM_NEO4J_PASSWORD keep matching the passwords they repeat.
# - Sets NIGHTLOOM_ENV=production, NIGHTLOOM_COOKIE_SECURE=true and the edge
#   (NIGHTLOOM_EDGE and the matching COMPOSE_PROFILES), plus the domain, ACME
#   email and small-host switch when given.
# - Writes the file with mode 600 and prints the names of the values it
#   generated, never the values.
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
example="$root/.env.example"
output="$root/.env"
edge=caddy
domain=""
acme_email=""
small=false
force=false

usage() {
  sed -n '2,8p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while (($# > 0)); do
  case $1 in
    --edge) edge=${2:?--edge needs a value}; shift 2 ;;
    --domain) domain=${2:?--domain needs a value}; shift 2 ;;
    --acme-email) acme_email=${2:?--acme-email needs a value}; shift 2 ;;
    --small) small=true; shift ;;
    --force) force=true; shift ;;
    --example) example=${2:?--example needs a path}; shift 2 ;;
    --output) output=${2:?--output needs a path}; shift 2 ;;
    -h | --help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

case $edge in
  caddy) profile=https ;;
  cloudflared) profile=tunnel ;;
  *) echo "--edge must be caddy or cloudflared, not '$edge'." >&2; exit 2 ;;
esac
# These end up unquoted in .env; refuse anything that is not a plain value.
if [[ -n "$domain" && ! "$domain" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]]; then
  echo "--domain must be a host name such as nightloom.example.org." >&2
  exit 2
fi
if [[ -n "$acme_email" && ! "$acme_email" =~ ^[^[:space:]\"\'\#\$\`\\]+@[A-Za-z0-9.-]+$ ]]; then
  echo "--acme-email must be an email address." >&2
  exit 2
fi
if [[ ! -f "$example" ]]; then
  echo "Template not found: $example" >&2
  exit 1
fi

if [[ -e "$output" ]]; then
  if [[ "$force" != true ]]; then
    echo "$output already exists; not overwriting it." >&2
    echo "Its secrets are already in your databases: replacing them locks the stack out of" >&2
    echo "existing data. Use --force only if you know what you are doing (it keeps a backup)." >&2
    exit 1
  fi
  backup="$output.backup-$(date -u +%Y%m%dT%H%M%SZ)"
  (umask 077 && cp -p "$output" "$backup")
  chmod 600 "$backup"
  echo "WARNING: overwriting $output (--force). The old file is in $backup." >&2
  echo "WARNING: data created with the old passwords will not be reachable with the new ones." >&2
fi

random_hex() { # BYTES
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex "$1"
  else
    python3 -c "import secrets, sys; print(secrets.token_hex(int(sys.argv[1])))" "$1"
  fi
}

declare -A generated=() # placeholder -> random value
generated_names=()
out_lines=()

while IFS= read -r line || [[ -n "$line" ]]; do
  case $line in
    NIGHTLOOM_ENV=*) line="NIGHTLOOM_ENV=production" ;;
    "# NIGHTLOOM_COOKIE_SECURE=true") line="NIGHTLOOM_COOKIE_SECURE=true" ;;
    "# NIGHTLOOM_EDGE="*) line="NIGHTLOOM_EDGE=$edge" ;;
    "# COMPOSE_PROFILES="*) line="COMPOSE_PROFILES=$profile" ;;
    "# NIGHTLOOM_DOMAIN="*) [[ -n "$domain" ]] && line="NIGHTLOOM_DOMAIN=$domain" ;;
    "# NIGHTLOOM_ACME_EMAIL="*) [[ -n "$acme_email" ]] && line="NIGHTLOOM_ACME_EMAIL=$acme_email" ;;
    "# NIGHTLOOM_SMALL_HOST="*) [[ "$small" == true ]] && line="NIGHTLOOM_SMALL_HOST=true" ;;
  esac
  # Replace each placeholder in an assignment (comments are left alone).
  if [[ "$line" =~ ^([A-Z_][A-Z0-9_]*)= ]]; then
    name=${BASH_REMATCH[1]}
    replaced=false
    while [[ "$line" =~ (change-me[A-Za-z0-9_-]*) ]]; do
      placeholder=${BASH_REMATCH[1]}
      if [[ -z "${generated[$placeholder]:-}" ]]; then
        generated[$placeholder]=$(random_hex 32)
      fi
      line=${line//"$placeholder"/"${generated[$placeholder]}"}
      replaced=true
    done
    [[ "$replaced" == true ]] && generated_names+=("$name")
  fi
  out_lines+=("$line")
done <"$example"

# Written next to the target and moved into place, never world-readable.
tmp=$(umask 077 && mktemp "$(dirname "$output")/.env.tmp.XXXXXX")
trap 'rm -f "$tmp"' EXIT
printf '%s\n' "${out_lines[@]}" >"$tmp"
chmod 600 "$tmp"
mv -f "$tmp" "$output"
trap - EXIT

echo "Created $output (mode 600) for a production deployment (edge: $edge, profile: $profile)."
if ((${#generated_names[@]} > 0)); then
  echo "Generated random values for:"
  printf '  %s\n' "${generated_names[@]}"
fi
echo "Also set: NIGHTLOOM_ENV=production, NIGHTLOOM_COOKIE_SECURE=true, NIGHTLOOM_EDGE=$edge, COMPOSE_PROFILES=$profile."
if [[ "$edge" == caddy && (-z "$domain" || -z "$acme_email") ]]; then
  echo "Next: set NIGHTLOOM_DOMAIN and NIGHTLOOM_ACME_EMAIL in $output (or rerun with --domain/--acme-email and --force)."
fi
if [[ "$edge" == cloudflared ]]; then
  echo "Next: set TUNNEL_TOKEN in $output (docs/DEPLOYMENT.md#cloudflare-tunnel)."
fi
echo "Keep a copy of $output somewhere safe: backups never include it."
