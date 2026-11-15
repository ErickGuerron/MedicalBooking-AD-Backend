#!/usr/bin/env bash
# Replicate now: pick up rows changed outside the API (seeds, manual SQL), then deliver the outbox.
# Use it after a seed or a manual change instead of waiting for the 15-minute schedule.
#
# Local (from the backend directory):   scripts/sync_now.sh
# VM (inside the image):                RUNNER="docker compose -f deploy/vm1/compose.yaml run --rm --entrypoint python migrate" scripts/sync_now.sh
#
# RUNNER is the command that starts Python with the backend environment. It defaults to uv.
set -euo pipefail

read -r -a runner <<< "${RUNNER:-uv run python}"

"${runner[@]}" -m medicalbooking.workers.sync_external_writes
"${runner[@]}" -m medicalbooking.workers.outbox_dispatcher
