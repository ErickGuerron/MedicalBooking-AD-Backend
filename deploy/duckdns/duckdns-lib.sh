# Shared DuckDNS helpers. Sourced by duckdns-update.sh and the certbot hooks; never executed directly.
# Secrets come from the env file only, never from arguments or the repository.

duckdns_load_env() {
  local env_file="${DUCKDNS_ENV_FILE:-/etc/medilo/duckdns.env}"
  if [ ! -f "$env_file" ]; then
    echo "missing env file: $env_file" >&2
    return 1
  fi
  set -a
  # shellcheck disable=SC1090
  . "$env_file"
  set +a
  : "${DUCKDNS_TOKEN:?DUCKDNS_TOKEN must be set in $env_file}"
}

# Calls the DuckDNS update endpoint for DUCKDNS_SUBDOMAIN.
# Extra arguments are curl --data-urlencode pairs (ip=..., txt=..., clear=true).
# The token is sent as a form parameter and never printed.
duckdns_api() {
  local response
  response="$(curl -fsS --get --max-time 20 "https://www.duckdns.org/update" \
    --data-urlencode "domains=${DUCKDNS_SUBDOMAIN}" \
    --data-urlencode "token=${DUCKDNS_TOKEN}" \
    "$@")" || return 1
  [ "$response" = "OK" ]
}
