# Shared display helpers for the Rusty Hearts client (sourced by bash scripts).
RH_DISPLAY_CONF="${RH_DISPLAY_CONF:-$ROOT/runtime/client-display.conf}"
RH_COMMON_SIZES="1280x720 1366x768 1600x900 1920x1080 2560x1440 3840x2160"

# Native size of the primary screen (falls back to the first connected one).
native_size() {
  local s=
  if command -v xrandr >/dev/null; then
    s=$(xrandr --current 2>/dev/null | awk '/ connected primary/{for(i=1;i<=NF;i++) if($i ~ /^[0-9]+x[0-9]+\+/){split($i,a,"+");print a[1];exit}}')
    [[ -n "$s" ]] || s=$(xrandr --current 2>/dev/null | awk '/ connected/{for(i=1;i<=NF;i++) if($i ~ /^[0-9]+x[0-9]+\+/){split($i,a,"+");print a[1];exit}}')
  fi
  if [[ -z "$s" ]] && command -v xdpyinfo >/dev/null; then
    s=$(xdpyinfo 2>/dev/null | awk '/dimensions:/{print $2; exit}')
  fi
  [[ "$s" =~ ^[0-9]+x[0-9]+$ ]] || s=1920x1080
  echo "$s"
}

# Real panel resolution (physical pixels) of the first connected display, from the kernel.
physical_size() {
  local st m
  for st in /sys/class/drm/card*-*/status; do
    [[ -r "$st" && "$(cat "$st" 2>/dev/null)" == connected ]] || continue
    m=$(head -n1 "${st%/status}/modes" 2>/dev/null)
    [[ "$m" =~ ^[0-9]+x[0-9]+ ]] && { echo "${BASH_REMATCH[0]}"; return; }
  done
  native_size
}

in_gaming_mode() {
  local d="${XDG_CURRENT_DESKTOP:-}"
  [[ -n "${SteamGamepadUI:-}${GAMESCOPE_WAYLAND_DISPLAY:-}" || "${d,,}" == gamescope ]]
}

load_display_conf() {
  RH_MODE=windowed; RH_SIZE=1280x720; RH_BACKEND=wayland; RH_RENDERER=opengl
  if [[ -f "$RH_DISPLAY_CONF" ]]; then
    local k v
    while IFS='=' read -r k v; do
      case "$k" in MODE) RH_MODE="$v";; SIZE) RH_SIZE="$v";; BACKEND) RH_BACKEND="$v";; RENDERER) RH_RENDERER="$v";; esac
    done < "$RH_DISPLAY_CONF"
  fi
  [[ "$RH_MODE" =~ ^(windowed|borderless|fullscreen)$ ]] || RH_MODE=windowed
  [[ "$RH_SIZE" =~ ^[0-9]+x[0-9]+$ ]] || RH_SIZE=1280x720
  [[ "$RH_BACKEND" =~ ^(wayland|x11)$ ]] || RH_BACKEND=wayland
}

save_display_conf() {
  mkdir -p "$(dirname "$RH_DISPLAY_CONF")"
  printf 'MODE=%s\nSIZE=%s\nBACKEND=%s\nRENDERER=%s\n' "$1" "$2" "${3:-${RH_BACKEND:-wayland}}" "${RH_RENDERER:-opengl}" > "$RH_DISPLAY_CONF"
}

# Sizes that fit on this screen, native first.
size_choices() {
  local native="$1" nw nh w h s
  nw=${native%x*}; nh=${native#*x}
  echo "$native"
  for s in $RH_COMMON_SIZES; do
    w=${s%x*}; h=${s#*x}
    [[ "$s" == "$native" ]] && continue
    (( w <= nw && h <= nh )) && echo "$s"
  done
}
