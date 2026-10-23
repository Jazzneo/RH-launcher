#!/usr/bin/env bash
# RH Launcher - Rusty Hearts Offline (controller-friendly launcher)
# Works even if the zip was unpacked without file permissions: run it with  bash RHLauncher.sh
DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
ROOT="$(dirname "$DIR")"
LOGDIR="$ROOT/logs"; mkdir -p "$LOGDIR"
PY="$DIR/runtime/python/bin/python3.12"

# Some unzip tools drop the "executable" flag: put it back on everything that needs it.
chmod +x "$PY" "$DIR/runtime/bin/7zzs" "$DIR/RHLauncher.sh" 2>/dev/null
find "$ROOT/docker" "$ROOT/tools" -maxdepth 1 \( -name '*.sh' -o -name '*.py' \) -exec chmod +x {} + 2>/dev/null

# SteamOS / Steam Deck: no Docker, but Podman is built in. Use the "docker" stand-in that drives Podman.
if [ -n "$RH_PODMAN" ] || { ! command -v docker >/dev/null 2>&1 && command -v podman >/dev/null 2>&1; }; then
  chmod +x "$DIR/runtime/podman-shim/docker" 2>/dev/null
  export PATH="$DIR/runtime/podman-shim:$PATH" RH_PODMAN=1
fi

if [ ! -f "$PY" ]; then
  MSG="RH Launcher's Python runtime is missing ($PY). Unpack the whole RH-launcher zip again."
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
