#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
# ---- Game client folder: runtime/client.conf (CLIENT_DIR=...), set in RH Launcher's Game Client tab ----
CLIENT_DIR="${RH_CLIENT_DIR:-$(sed -n 's/^CLIENT_DIR=//p' "$ROOT/runtime/client.conf" 2>/dev/null | tail -n1 || true)}"
[[ -n "$CLIENT_DIR" ]] || CLIENT_DIR="$ROOT/gameclient"
CLIENT_EXE="$(find "$CLIENT_DIR" -maxdepth 1 -type f -iname 'rustyhearts.exe' 2>/dev/null | head -n1 || true)"

# ---- Proton: RH_PROTON, else Proton Experimental, else newest GE-Proton, else newest Valve Proton ----
steam_roots() {
  local r
  for r in "${STEAM_ROOT:-}" "$HOME/.local/share/Steam" "$HOME/.steam/root" "$HOME/.steam/steam" \
           "$HOME/.var/app/com.valvesoftware.Steam/.local/share/Steam" "$HOME/.var/app/com.valvesoftware.Steam/data/Steam"; do
    if [[ -n "$r" && -d "$r/steamapps" ]]; then readlink -f "$r"; fi
  done | awk '!seen[$0]++'
  return 0
}
steam_libraries() {
  local r
  while read -r r; do
    echo "$r"
    sed -n 's/^[[:space:]]*"path"[[:space:]]*"\(.*\)"/\1/p' "$r/steamapps/libraryfolders.vdf" 2>/dev/null
  done < <(steam_roots) | awk 'NF && !seen[$0]++'
  return 0
}
find_proton() {
  local lib r p
  [[ -n "${RH_PROTON:-}" && -x "${RH_PROTON}" ]] && { echo "$RH_PROTON"; return; }
  while read -r lib; do
    [[ -x "$lib/steamapps/common/Proton - Experimental/proton" ]] && { echo "$lib/steamapps/common/Proton - Experimental/proton"; return; }
  done < <(steam_libraries)
  while read -r r; do
    p=$(ls -d "$r"/compatibilitytools.d/*/proton 2>/dev/null | grep -i 'ge-proton\|proton-ge' | sort -V | tail -n1)
    [[ -n "$p" && -x "$p" ]] && { echo "$p"; return; }
  done < <(steam_roots; echo "$HOME/.steam/root")
  while read -r lib; do
    p=$(ls -d "$lib"/steamapps/common/Proton\ [0-9]*/proton 2>/dev/null | sort -V | tail -n1)
    [[ -n "$p" && -x "$p" ]] && { echo "$p"; return; }
  done < <(steam_libraries)
}
PROTON="$(find_proton || true)"
STEAM_ROOT="$(steam_roots | head -n1 || true)"; STEAM_ROOT="${STEAM_ROOT:-$HOME/.local/share/Steam}"

if [[ "${1:-}" == --check ]]; then   # used by RH Launcher's Game Client tab
  echo "CLIENT_DIR=$CLIENT_DIR"; echo "CLIENT_EXE=$CLIENT_EXE"; echo "PROTON=$PROTON"; exit 0
fi
if [[ -z "$CLIENT_EXE" ]]; then
  echo "Rusty Hearts client not found in: $CLIENT_DIR (pick it on the launcher's Game Client tab)." >&2
  exit 2
fi
if [[ -z "$PROTON" || ! -x "$PROTON" ]]; then
  echo "Proton not found. Install Proton Experimental from Steam (Library > Tools) or GE-Proton, or set RH_PROTON." >&2
  exit 1
fi
echo "Client: $CLIENT_DIR"; echo "Proton: $PROTON"
mkdir -p "$ROOT/runtime/client-prefix" "$ROOT/logs"
export STEAM_COMPAT_CLIENT_INSTALL_PATH="$STEAM_ROOT"
export STEAM_COMPAT_DATA_PATH="$ROOT/runtime/client-prefix"
export SteamAppId=0 SteamGameId=0
export PROTON_LOG=1 PROTON_LOG_DIR="$ROOT/logs"
# Renderer: RENDERER=vulkan (DXVK) or opengl (WineD3D) in runtime/client-display.conf.
# DXVK used to fail on this host under XWayland + virtual desktop; OpenGL is the safe default.
_r=$(sed -n 's/^RENDERER=//p' "$ROOT/runtime/client-display.conf" 2>/dev/null | tail -n1 || true)
if [[ -z "${PROTON_USE_WINED3D:-}" ]]; then
  [[ "$_r" == vulkan ]] && export PROTON_USE_WINED3D=0 || export PROTON_USE_WINED3D=1
fi
if [[ "$PROTON_USE_WINED3D" == 0 ]]; then
  export DXVK_LOG_LEVEL="${DXVK_LOG_LEVEL:-info}" DXVK_LOG_PATH="$ROOT/logs"
fi
export WINEDLLOVERRIDES="d3dx9_43=n,b;d3dcompiler_43=n,b;msvcr100=n,b;msvcp100=n,b${RH_EXTRA_DLL_OVERRIDES:+;$RH_EXTRA_DLL_OVERRIDES}"
cd "$CLIENT_DIR"
case "${RH_REGION:-usa}" in
  usa)
    service_hash=ada53304c5b9e4a839615b6e8f908eb6
    server_argument="server=${RH_API_URL:-http://127.0.0.1:3000}/launcher/GetGatewayAction"
    ;;
  jpn)
    service_hash=5e4ac03d7827ca48ed305a0d80cd5aa1
    server_argument="-serverurl${RH_API_URL:-http://127.0.0.1:3000}/launcher/GetGatewayAction/info"
    ;;
  *) echo 'RH_REGION must be usa or jpn.' >&2; exit 1 ;;
esac
printf '%s\r\n\r\nSTAIRWAY GAMES\r\n' "$service_hash" > Service.dat
# ---- Display mode / resolution (chosen in the launcher: tools/display-settings.sh) ----
. "$ROOT/tools/display-common.sh"
load_display_conf
mode="$RH_MODE"; window_size="${RH_WINDOW_SIZE:-$RH_SIZE}"; backend="${RH_BACKEND_OVERRIDE:-$RH_BACKEND}"
[[ "$window_size" =~ ^[0-9]+x[0-9]+$ ]] || { echo 'Resolution must look like 1280x720.' >&2; exit 1; }
native="$(native_size)"
use_gamescope=
command -v gamescope >/dev/null && [[ "${RH_NO_GAMESCOPE:-0}" != 1 ]] && use_gamescope=1
[[ "$mode" == borderless ]] && window_size="$native"
w=${window_size%x*}; h=${window_size#*x}; nw=${native%x*}; nh=${native#*x}
echo "Display: $mode at ${window_size} (screen ${native})"

# The game always runs windowed inside Wine's desktop; the desktop window
# provides windowed / borderless / scaled-fullscreen behaviour.
write_game_options() {  # width height fullscreen(0|1)
python3 - "$CLIENT_DIR/game_option_v1_3.xml" "$1" "$2" "$3" <<'PY'
import re, sys
path, w, h, fs = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
import os
if not os.path.exists(path):
    sys.exit(0)          # the game creates it on first start
raw = open(path, 'rb').read().decode('utf-8', 'replace')
def fix(m):
    t = m.group(0)
    for k, v in (('width', w), ('height', h), ('fullscreen', fs)):
        t = re.sub(r'\b%s="[^"]*"' % k, '%s="%s"' % (k, v), t)
    return t
new = re.sub(r'<tab02\b[^>]*/>', fix, raw, count=1)
# Joystick input off unless RH_USE_JOYSTICK=1 (stray controller/motion-sensor axes move the cursor).
import os
new = re.sub(r'use_joystick="[^"]*"', 'use_joystick="%s"' % ('1' if os.environ.get('RH_USE_JOYSTICK') == '1' else '0'), new, count=1)
if new != raw:
    open(path, 'wb').write(new.encode('utf-8'))
PY
}
write_game_options "$w" "$h" 0

# Mouse: stop Wine DirectInput from warping the pointer. On Wayland/XWayland
# (especially with display scaling) the warps fight the compositor and the
# cursor jumps around while the game has focus. RH_MOUSE_WARP=enable|disable|force
python3 - "$STEAM_COMPAT_DATA_PATH/pfx/user.reg" "${RH_MOUSE_WARP:-disable}" <<'PY'
import re, sys, time, os
path, value = sys.argv[1], sys.argv[2]
if value not in ('enable', 'disable', 'force') or not os.path.exists(path):
    sys.exit(0)
text = open(path, encoding='utf-8', errors='surrogateescape').read()
sec = '[Software\\\\Wine\\\\DirectInput]'
line = '"MouseWarpOverride"="%s"' % value
i = text.find(sec + ' ')
if i < 0:
    text = text.rstrip('\n') + '\n\n%s %d\n%s\n' % (sec, int(time.time()), line)
else:
    j = text.find('\n\n', i); j = len(text) if j < 0 else j
    block = text[i:j]
    if re.search(r'^"MouseWarpOverride"=', block, re.M):
        block = re.sub(r'^"MouseWarpOverride"=.*$', line, block, flags=re.M)
    else:
        block = block + '\n' + line
    text = text[:i] + block + text[j:]
open(path, 'w', encoding='utf-8', errors='surrogateescape').write(text)
PY

# ---- Launch ----
# gamescope gives the game its own X server at the exact resolution: no
# XWayland fractional-scaling mismatch (cursor jumping), and a normal decorated
# window in Windowed mode. Without it: Windowed = plain game window (KDE draws
# the border), Borderless/Fullscreen = Wine desktop forced fullscreen.
desktop_launch=("$PROTON" waitforexitandrun explorer.exe "/desktop=RustyHearts,$window_size" "$CLIENT_EXE" "$server_argument")
direct_launch=("$PROTON" waitforexitandrun "$CLIENT_EXE" "$server_argument")

# Runs "$@". If Vulkan (DXVK) exits with an error within 15 s, retries once with OpenGL.
# Sets LAST_RC and LAST_FAST (1 = ended within 15 s) for the callers' own fallbacks.
# Optional auto-login (RH Launcher > Settings > Auto-login): a tiny helper inside the same Proton prefix
# waits for the game window and types the saved username/password, only while the game has focus.
AUTOLOGIN_PID=
start_autologin() {
  [[ -z "$AUTOLOGIN_PID" ]] || return 0
  local conf="$ROOT/runtime/rhlauncher/autologin.conf" cred="$ROOT/runtime/rhlauncher/autologin.cred"
  local exe="$ROOT/tools/autologin/autologin.exe" delay
  [[ -f "$conf" && -f "$cred" && -f "$exe" ]] || return 0
  grep -qx 'ENABLED=1' "$conf" || return 0
  delay="$(sed -n 's/^DELAY=\([0-9][0-9]*\)$/\1/p' "$conf" | head -n1 || true)"
  echo "Auto-login: typing the saved login ${delay:-20} s after the game window appears (log: logs/autologin.log)."
  ( sleep 4; "$PROTON" runinprefix "$exe" "Z:${cred//\//\\}" "${delay:-20}" \
      "Z:${ROOT//\//\\}\\logs\\autologin.log" >/dev/null 2>&1 || true ) &
  AUTOLOGIN_PID=$!
}

run_game() {
  local start=$SECONDS rc=0
  start_autologin
  "$@" || rc=$?
  if (( rc != 0 && SECONDS - start < 15 )) && [[ "$PROTON_USE_WINED3D" == 0 ]]; then
    echo "Vulkan (DXVK) start failed (code $rc); retrying with OpenGL (WineD3D)." >&2
    export PROTON_USE_WINED3D=1
    start=$SECONDS; rc=0
    "$@" || rc=$?
  fi
  LAST_RC=$rc; LAST_FAST=$(( SECONDS - start < 15 ? 1 : 0 ))
  return 0
}
echo "Renderer: $([[ "$PROTON_USE_WINED3D" == 0 ]] && echo 'Vulkan (DXVK)' || echo 'OpenGL (WineD3D)')"

fallback() {
  local helper_pid=
  if [[ "$mode" == windowed ]]; then
    echo "Launching as a normal window (${window_size})."
    run_game "${direct_launch[@]}"; return
  fi
  if command -v xdotool >/dev/null && command -v wmctrl >/dev/null; then
    ( for ((t=0;t<40;t++)); do
        while read -r id; do [[ -n "$id" ]] && wmctrl -i -r "$id" -b add,fullscreen 2>/dev/null; done \
          < <(xdotool search --onlyvisible --name '^RustyHearts - Wine Desktop$' 2>/dev/null || true)
        sleep 1
      done ) & helper_pid=$!
  fi
  echo "Launching borderless at ${native}."
  run_game "$PROTON" waitforexitandrun explorer.exe "/desktop=RustyHearts,$native" "$CLIENT_EXE" "$server_argument"
  [[ -z "$helper_pid" ]] || kill "$helper_pid" 2>/dev/null || true
}

# Steam Gaming Mode is already a gamescope session: plain Proton, no nesting.
if in_gaming_mode; then
  echo "Gaming Mode: launching directly (${window_size})."
  run_game "${direct_launch[@]}"
  exit 0
fi

# Proton's native Wayland driver: no XWayland, so no fractional-scaling cursor
# trouble. No virtual desktop exists there, so borderless/fullscreen use the
# game's own fullscreen (Wine scales non-native sizes).
if [[ "$backend" == wayland ]]; then
  phys="$(physical_size)"
  case "$mode" in
    windowed)   write_game_options "$w" "$h" 0; wl_size="$window_size" ;;
    borderless) write_game_options "${phys%x*}" "${phys#*x}" 1; wl_size="$phys" ;;
    fullscreen) write_game_options "$w" "$h" 1; wl_size="$window_size" ;;
  esac
  echo "Launching with Proton native Wayland: $mode at $wl_size (panel $phys)"
  PROTON_ENABLE_WAYLAND=1 run_game "${direct_launch[@]}"
  if (( LAST_RC != 0 && LAST_FAST )); then
    echo "Wayland launch exited early (code $LAST_RC); falling back to X11." >&2
    write_game_options "$w" "$h" 0
  else
    exit 0
  fi
fi

if [[ -n "$use_gamescope" ]]; then
  case "$mode" in
    windowed)   gs=(gamescope -W "$w" -H "$h" -w "$w" -h "$h") ;;
    borderless) gs=(gamescope -W "$nw" -H "$nh" -w "$nw" -h "$nh" -f) ;;
    fullscreen) gs=(gamescope -W "$nw" -H "$nh" -w "$w" -h "$h" -f) ;;
  esac
  echo "Launching through gamescope: ${gs[*]}"
  start=$SECONDS
  run_game "${gs[@]}" -- "${desktop_launch[@]}"
  # gamescope failed to start (not a normal game exit): fall back
  if (( LAST_RC != 0 && LAST_FAST )); then
    echo "gamescope exited early (code $LAST_RC); falling back without it." >&2
    fallback
  fi
else
  echo 'gamescope not found; using the fallback launch.' >&2
  fallback
fi
