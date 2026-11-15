#!/usr/bin/env bash
# Follows the newest release image of the backend (tag X.Y.Z on Docker Hub) and rolls it out to
# this VM. Development tags (develop, develop-<sha>) are never deployed.
#
# Each run:
#   1. finds the highest X.Y.Z tag (semantic order, not date order);
#   2. stops when that image is already running on this VM;
#   3. with --migrate, applies `alembic upgrade head` once (run it on one VM only: both VMs share
#      the database). A failed migration stops the run before any replica is touched;
#   4. recreates replica-a, then replica-b, and waits for each to be healthy. If a replica is not
#      healthy, every replica already updated goes back to the previous image and the run fails.
#
# Migrations move forward only: a rollback restores the previous image, not the schema.
#
# Usage: release-deploy.sh <compose-file> [--migrate]
# Cron, every 15 minutes (VM1 migrates, VM2 does not):
#   */15 * * * * /opt/medicalbooking/deploy/release-deploy.sh /opt/medicalbooking/deploy/vm1/compose.yaml --migrate >> $HOME/logs/release-deploy.log 2>&1
#   */15 * * * * /opt/medicalbooking/deploy/release-deploy.sh /opt/medicalbooking/deploy/vm2/compose.yaml >> $HOME/logs/release-deploy.log 2>&1
set -euo pipefail

compose_file="${1:?usage: release-deploy.sh <compose-file> [--migrate]}"
migrate=false
case "${2:-}" in
  "") ;;
  --migrate) migrate=true ;;
  *) echo "usage: release-deploy.sh <compose-file> [--migrate]" >&2; exit 2 ;;
esac

repo="${MEDILO_IMAGE_REPO:-meliodasdev/medicalbooking-backend}"
state_dir="${MEDILO_STATE_DIR:-$HOME/.local/state/medilo}"
wait_timeout="${MEDILO_WAIT_TIMEOUT:-180}"
docker_bin="${MEDILO_DOCKER:-docker}"
curl_bin="${MEDILO_CURL:-curl}"
replicas=(replica-a replica-b)

log() {
  echo "$(date -u +%FT%TZ) $*"
}

# Highest X.Y.Z tag across the first pages of the Docker Hub tag list. Prints nothing when no
# release exists yet. Fails when Docker Hub cannot be read.
latest_release() {
  local url="https://hub.docker.com/v2/repositories/${repo}/tags?page_size=100"
  local page pages=0 tags_file
  tags_file="$(mktemp)"
  while [ -n "$url" ] && [ "$pages" -lt 10 ]; do
    page="$("$curl_bin" -fsSL "$url")"
    printf '%s' "$page" | python3 -c '
import json, sys
for result in json.load(sys.stdin).get("results", []):
    print(result["name"])
' >> "$tags_file"
    url="$(printf '%s' "$page" | python3 -c 'import json, sys; print(json.load(sys.stdin).get("next") or "")')"
    pages=$((pages + 1))
  done
  python3 -c '
import re, sys
releases = [line.strip() for line in open(sys.argv[1]) if re.fullmatch(r"\d+\.\d+\.\d+", line.strip())]
if releases:
    print(max(releases, key=lambda version: tuple(int(part) for part in version.split("."))))
' "$tags_file"
  rm -f "$tags_file"
}

mkdir -p "$state_dir"
exec 9>"$state_dir/lock"
if ! flock -n 9; then
  log "another run is active, skipping"
  exit 0
fi

if ! target="$(latest_release)"; then
  log "ERROR: could not read release tags from Docker Hub"
  exit 1
fi
if [ -z "$target" ]; then
  log "no X.Y.Z release published yet"
  exit 0
fi

image="${repo}:${target}"
"$docker_bin" pull "$image" >/dev/null
digest_ref="$("$docker_bin" image inspect --format '{{index .RepoDigests 0}}' "$image")"

current_file="$state_dir/current"
current="$(cat "$current_file" 2>/dev/null || true)"
if [ "$current" = "$digest_ref" ]; then
  log "up to date: $target"
  exit 0
fi
log "new release $target ($digest_ref)"

if [ "$migrate" = true ]; then
  log "applying migrations for $target"
  if ! MEDICALBOOKING_IMAGE="$digest_ref" "$docker_bin" compose -f "$compose_file" \
      --profile migrate run --rm migrate; then
    log "ERROR: migrations failed; replicas were not touched"
    exit 1
  fi
  log "migrations applied"
fi

updated=()

# The image running before this run is the rollback target. It is written to state only after
# every replica is healthy on the new release, so state always names what the VM serves.
roll_back() {
  if [ -z "$current" ]; then
    log "ERROR: no running image recorded; replicas left as they are"
    exit 1
  fi
  log "rolling back to $current"
  for service in "${updated[@]}"; do
    if ! MEDICALBOOKING_IMAGE="$current" "$docker_bin" compose -f "$compose_file" up -d \
        --no-deps --wait --wait-timeout "$wait_timeout" "$service"; then
      log "ERROR: $service did not become healthy on the previous image"
    fi
  done
  exit 1
}

for service in "${replicas[@]}"; do
  updated+=("$service")
  if ! MEDICALBOOKING_IMAGE="$digest_ref" "$docker_bin" compose -f "$compose_file" up -d \
      --no-deps --wait --wait-timeout "$wait_timeout" "$service"; then
    log "ERROR: $service is not healthy on $target"
    roll_back
  fi
  log "$service healthy on $target"
done

printf '%s\n' "$digest_ref" > "$current_file"
"$docker_bin" image prune -f >/dev/null
log "deployed $target"
