#!/usr/bin/env bash
# Arms a temporary SQL trace, lets you make ONE lobby login attempt, then
# prints what the database did. Run while the game is at Server Select.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if ! command -v docker >/dev/null && command -v host-spawn >/dev/null; then
  exec host-spawn /usr/bin/bash "$ROOT/Trace Lobby Login.sh" "$@"
fi
exec python3 "$ROOT/tools/lobby-db-trace.py" run "$@"
