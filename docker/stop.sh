#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if ! command -v docker >/dev/null && command -v host-spawn >/dev/null; then
  exec host-spawn /usr/bin/bash "$ROOT/docker/stop.sh"
fi
docker compose -f "$ROOT/docker/compose.yaml" stop
echo 'Stopped the Docker game services. Database volumes are preserved.'
