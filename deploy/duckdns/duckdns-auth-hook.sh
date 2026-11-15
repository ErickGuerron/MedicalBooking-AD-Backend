#!/usr/bin/env bash
# certbot --manual-auth-hook: publishes the DNS-01 challenge as the DuckDNS TXT record.
# certbot provides CERTBOT_DOMAIN and CERTBOT_VALIDATION.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=duckdns-lib.sh
. "$script_dir/duckdns-lib.sh"

duckdns_load_env
DUCKDNS_SUBDOMAIN="${CERTBOT_DOMAIN%.duckdns.org}"
export DUCKDNS_SUBDOMAIN

if ! duckdns_api --data-urlencode "txt=${CERTBOT_VALIDATION}"; then
  echo "DuckDNS TXT update failed for ${CERTBOT_DOMAIN}" >&2
  exit 1
fi

# The DuckDNS TXT record also answers _acme-challenge.<subdomain>.duckdns.org (sub-subdomains share it).
# Wait for public resolvers to pick it up before Let's Encrypt checks it.
sleep "${DNS_PROPAGATION_WAIT:-60}"
