#!/usr/bin/env bash
# Points DuckDNS at the Azure Load Balancer public IP and keeps a Let's Encrypt certificate
# for <subdomain>.duckdns.org (DNS-01 through the DuckDNS TXT API). Run on VM1 only.
# Certificates are copied to TLS_DIR; TLS_RELOAD_CMD runs only when they change.
# Usage: duckdns-update.sh
# Config: /etc/medilo/duckdns.env (root, 600; never committed). Variables:
#   DUCKDNS_TOKEN          account token (regenerate it if it was ever pasted in chat or logs)
#   DUCKDNS_SUBDOMAIN      subdomain only, e.g. medilo-backend
#   LB_IP                  Azure Load Balancer public IPv4 (confirm in the portal first)
#   LE_EMAIL               Let's Encrypt account contact
#   LE_STAGING             1 = staging certificates (test first), 0 = production
#   TLS_DIR                certificate copies directory (default /etc/medilo/tls)
#   TLS_RELOAD_CMD         optional, runs only when the certificate changes
#   DNS_PROPAGATION_WAIT   optional seconds to wait for the TXT record (default 60)
# Cron (root, daily; certbot renews only inside its 30-day window):
#   17 3 * * * /opt/medicalbooking/deploy/duckdns/duckdns-update.sh >> /var/log/medilo-duckdns.log 2>&1
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=duckdns-lib.sh
. "$script_dir/duckdns-lib.sh"

duckdns_load_env
: "${DUCKDNS_SUBDOMAIN:?DUCKDNS_SUBDOMAIN must be set (for example medilo-backend)}"
: "${LB_IP:?LB_IP must be set to the Azure Load Balancer public IP}"
: "${LE_EMAIL:?LE_EMAIL must be set for the certificate account}"
export DUCKDNS_SUBDOMAIN

if ! [[ "$LB_IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
  echo "LB_IP is not an IPv4 address" >&2
  exit 1
fi

fqdn="${DUCKDNS_SUBDOMAIN}.duckdns.org"
tls_dir="${TLS_DIR:-/etc/medilo/tls}"
live_dir="/etc/letsencrypt/live/${fqdn}"

if ! duckdns_api --data-urlencode "ip=${LB_IP}"; then
  echo "$(date -u +%FT%TZ) DuckDNS update failed for ${fqdn}" >&2
  exit 1
fi
echo "$(date -u +%FT%TZ) DuckDNS ${fqdn} -> ${LB_IP}"

certbot_args=(
  certonly
  --manual
  --preferred-challenges dns
  --manual-public-ip-logging-ok
  --manual-auth-hook "$script_dir/duckdns-auth-hook.sh"
  --manual-cleanup-hook "$script_dir/duckdns-cleanup-hook.sh"
  --cert-name "$fqdn"
  -d "$fqdn"
  --non-interactive
  --agree-tos
  --email "$LE_EMAIL"
  --keep-until-expiring
)
if [ "${LE_STAGING:-0}" = "1" ]; then
  certbot_args+=(--staging)
fi
certbot "${certbot_args[@]}"

changed=0
install -d -m 0750 "$tls_dir"
for pair in "fullchain.pem:0644" "privkey.pem:0600"; do
  file="${pair%%:*}"
  mode="${pair##*:}"
  if ! cmp -s "$live_dir/$file" "$tls_dir/$file" 2>/dev/null; then
    install -m "$mode" "$live_dir/$file" "$tls_dir/$file"
    changed=1
  fi
done

if [ "$changed" = 1 ]; then
  echo "$(date -u +%FT%TZ) certificate installed in ${tls_dir}"
  if [ -n "${TLS_RELOAD_CMD:-}" ]; then
    bash -c "$TLS_RELOAD_CMD"
  fi
else
  echo "$(date -u +%FT%TZ) certificate unchanged"
fi
