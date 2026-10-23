#!/usr/bin/env bash
# Choose Rusty Hearts display mode and resolution (saved for future launches).
set -uo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
. "$ROOT/tools/display-common.sh"
load_display_conf
native=$(native_size)
gui() { [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]] && { command -v kdialog >/dev/null || command -v zenity >/dev/null; }; }
mode_label() { case "$1" in windowed) echo 'Windowed (movable window)';; borderless) echo 'Borderless (fills screen, no border)';; fullscreen) echo 'Fullscreen (scaled to screen)';; esac; }

pick_mode() {
  if gui && command -v kdialog >/dev/null; then
    kdialog --title 'Rusty Hearts display' --radiolist "Display mode (current: $(mode_label "$RH_MODE"))" \
      windowed "$(mode_label windowed)" $([[ $RH_MODE == windowed ]] && echo on || echo off) \
      borderless "$(mode_label borderless)" $([[ $RH_MODE == borderless ]] && echo on || echo off) \
      fullscreen "$(mode_label fullscreen)" $([[ $RH_MODE == fullscreen ]] && echo on || echo off) 2>/dev/null
  elif gui; then
    zenity --list --radiolist --title='Rusty Hearts display' --text='Display mode' --column=' ' --column=Mode --column=Description \
      $([[ $RH_MODE == windowed ]] && echo TRUE || echo FALSE) windowed "$(mode_label windowed)" \
      $([[ $RH_MODE == borderless ]] && echo TRUE || echo FALSE) borderless "$(mode_label borderless)" \
      $([[ $RH_MODE == fullscreen ]] && echo TRUE || echo FALSE) fullscreen "$(mode_label fullscreen)" \
      --print-column=2 2>/dev/null
  else
    echo "1) $(mode_label windowed)  2) $(mode_label borderless)  3) $(mode_label fullscreen)" >&2
    read -rp 'Mode: ' n
    case "$n" in 1) echo windowed;; 2) echo borderless;; 3) echo fullscreen;; esac
  fi
}

pick_size() {
  local mode="$1" list=() s
  mapfile -t list < <(size_choices "$native")
  if gui && command -v kdialog >/dev/null; then
    local args=()
    for s in "${list[@]}"; do
      local lbl="$s"; [[ "$s" == "$native" ]] && lbl="$s (your screen)"
      args+=("$s" "$lbl" "$([[ $s == "$RH_SIZE" ]] && echo on || echo off)")
    done
    kdialog --title 'Rusty Hearts display' --radiolist "Resolution for $(mode_label "$mode")" "${args[@]}" 2>/dev/null
  elif gui; then
    local args=()
    for s in "${list[@]}"; do args+=("$([[ $s == "$RH_SIZE" ]] && echo TRUE || echo FALSE)" "$s"); done
    zenity --list --radiolist --title='Rusty Hearts display' --text="Resolution (your screen: $native)" --column=' ' --column=Resolution "${args[@]}" --print-column=2 2>/dev/null
  else
    local i=1; for s in "${list[@]}"; do echo "$i) $s" >&2; ((i++)); done
    read -rp 'Resolution: ' n; [[ "$n" =~ ^[0-9]+$ ]] && echo "${list[$((n-1))]:-}"
  fi
}

mode=$(pick_mode) || exit 0
[[ -n "$mode" ]] || exit 0
if [[ "$mode" == borderless ]]; then
  size="$native"   # borderless always uses the screen's own size
else
  size=$(pick_size "$mode") || exit 0
  [[ -n "$size" ]] || exit 0
fi
save_display_conf "$mode" "$size"
msg="Saved: $(mode_label "$mode") at $size."
if [[ "$mode" == fullscreen ]] && ! command -v gamescope >/dev/null; then
  msg+=$'\n\ngamescope was not found, so Fullscreen will open as Borderless at your screen size.'
fi
echo "$msg"
if gui; then
  if command -v kdialog >/dev/null; then kdialog --title 'Rusty Hearts display' --msgbox "$msg" 2>/dev/null
  else zenity --info --title='Rusty Hearts display' --text="$msg" 2>/dev/null; fi
fi
