#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export STEAM_COMPAT_CLIENT_INSTALL_PATH="${STEAM_ROOT:-$HOME/.local/share/Steam}"
export STEAM_COMPAT_DATA_PATH="$ROOT/runtime/client-prefix"
export SteamAppId=0 SteamGameId=0 PROTON_LOG=0
PROTON="${RH_PROTON:-$STEAM_COMPAT_CLIENT_INSTALL_PATH/steamapps/common/Proton - Experimental/proton}"
"$PROTON" run explorer.exe "/desktop=RustyHearts,${RH_WINDOW_SIZE:-1280x720}" "$ROOT/runtime/client-input.exe" "$@"
