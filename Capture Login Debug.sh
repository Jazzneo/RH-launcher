#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if ! command -v docker >/dev/null && command -v host-spawn >/dev/null; then
  exec host-spawn /usr/bin/bash "$ROOT/Capture Login Debug.sh" "$@"
fi
exec python3 "$ROOT/docker/diagnostics.py"
