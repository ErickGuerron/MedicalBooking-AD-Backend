#!/usr/bin/env bash
# certbot --manual-cleanup-hook: clears the DuckDNS TXT record after validation.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=duckdns-lib.sh
. "$script_dir/duckdns-lib.sh"

duckdns_load_env
DUCKDNS_SUBDOMAIN="${CERTBOT_DOMAIN%.duckdns.org}"
export DUCKDNS_SUBDOMAIN

if ! duckdns_api --data-urlencode "clear=true"; then
  echo "DuckDNS TXT clear failed for ${CERTBOT_DOMAIN}" >&2
  exit 1
fi
