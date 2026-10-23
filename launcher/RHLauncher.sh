#!/usr/bin/env bash
# RH Launcher - Rusty Hearts Offline (controller-friendly launcher)
DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
LOGDIR="$DIR/../logs"; mkdir -p "$LOGDIR"
PY="$DIR/runtime/python/bin/python3"
if [ ! -x "$PY" ]; then
  MSG="RH Launcher's Python runtime is missing ($PY)."
  echo "$MSG" >&2
  (command -v kdialog >/dev/null && kdialog --error "$MSG") || (command -v zenity >/dev/null && zenity --error --text="$MSG") || true
  exit 1
fi
cd "$DIR/app" || exit 1
if [ -t 1 ]; then
  exec "$PY" -s -m rhlauncher "$@"
else
  exec "$PY" -s -m rhlauncher "$@" >"$LOGDIR/rhlauncher.log" 2>&1
fi
