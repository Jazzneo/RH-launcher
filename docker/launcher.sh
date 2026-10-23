#!/usr/bin/env bash
# Rusty Hearts all-in-one launcher: start server -> play -> stop server.
# Usage: launcher.sh [play|trace|stop|status]   (no argument = menu)
set -uo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if ! command -v docker >/dev/null && command -v host-spawn >/dev/null; then
  exec host-spawn /usr/bin/bash "$ROOT/docker/launcher.sh" "$@"
fi
cd "$ROOT"
COMPOSE=(docker compose -f "$ROOT/docker/compose.yaml")
TITLE='Rusty Hearts Offline'

# ---------- small UI helpers (KDE kdialog, GNOME zenity, or plain terminal) ----------
have() { command -v "$1" >/dev/null 2>&1; }
gui() { [[ "${RH_NO_POPUPS:-0}" != 1 ]] && [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]] && { have kdialog || have zenity; }; }
say()  { echo; echo "== $*"; }
info() {
  echo "$*"
  if gui; then
    if have kdialog; then kdialog --title "$TITLE" --msgbox "$*" 2>/dev/null
    else zenity --info --title="$TITLE" --text="$*" 2>/dev/null; fi
  fi
}
fail() { echo "ERROR: $*" >&2; if gui; then
    if have kdialog; then kdialog --title "$TITLE" --error "$*" 2>/dev/null
    else zenity --error --title="$TITLE" --text="$*" 2>/dev/null; fi; fi; exit 1; }
menu() {
  local choice=
  if gui && have kdialog; then
    choice=$(kdialog --title "$TITLE" --menu "What do you want to do?" \
      play 'Play (start server + game, stop server when game closes)' \
      trace 'Play + record login trace (for debugging)' \
      display 'Display settings (window / borderless / fullscreen, resolution)' \
      stop 'Stop server' status 'Show status' 2>/dev/null) || exit 0
  elif gui; then
    choice=$(zenity --list --title="$TITLE" --text="What do you want to do?" --column=Action --column=Description \
      play 'Play (start server + game, stop server when game closes)' \
      trace 'Play + record login trace (for debugging)' \
      display 'Display settings (window / borderless / fullscreen, resolution)' \
      stop 'Stop server' status 'Show status' --hide-column=1 --print-column=1 2>/dev/null) || exit 0
  else
    echo "1) Play   2) Play + login trace   3) Display settings   4) Stop server   5) Status   q) Quit" >&2
    read -rp "Choose: " n
    case "$n" in 1) choice=play;; 2) choice=trace;; 3) choice=display;; 4) choice=stop;; 5) choice=status;; *) exit 0;; esac
  fi
  echo "$choice"
}

# ---------- state ----------
running_services() { "${COMPOSE[@]}" ps --services --filter status=running 2>/dev/null; }
# All services use host networking, so check the real ports from here.
ports_up() {
  local port
  for port in 3000 18070 50001 50006 50016; do
    timeout 2 bash -c "exec 3<>/dev/tcp/127.0.0.1/$port" 2>/dev/null || return 1
  done
}
# /tmp/rh-ready survives a container stop/start, so never trust it alone.
server_ready() {
  local s; s=$(running_services)
  grep -qx sql <<<"$s" && grep -qx api <<<"$s" && grep -qx game <<<"$s" &&
    "${COMPOSE[@]}" exec -T game test -f /tmp/rh-ready >/dev/null 2>&1 && ports_up
}
client_running() { systemctl --user is-active --quiet rusty-hearts-client.scope 2>/dev/null; }

status() {
  local s; s=$(running_services | tr '\n' ' ')
  local msg="Server containers running: ${s:-none}"
  if server_ready; then msg+=$'\nServer: READY'; else msg+=$'\nServer: not ready / stopped'; fi
  if client_running; then msg+=$'\nGame client: running'; else msg+=$'\nGame client: not running'; fi
  info "$msg"
}

stop_all() {
  say 'Stopping game client (if open) and server...'
  systemctl --user stop rusty-hearts-client.scope 2>/dev/null || true
  "${COMPOSE[@]}" stop
  echo 'Server stopped. Database data is preserved.'
}

# Save evidence when startup fails, with the SQL password scrubbed.
capture_failure() {
  local out="$ROOT/logs/launcher-failure-$(date -u +%Y%m%dT%H%M%SZ).txt"
  {
    echo "== containers"; "${COMPOSE[@]}" ps --all 2>&1
    echo; echo "== game container state"; docker inspect --format '{{json .State}}' rusty-hearts-offline-game-1 2>&1
    echo; echo "== game container log (last 250 lines)"; "${COMPOSE[@]}" logs --no-color --tail 250 game 2>&1
    echo; echo "== listening ports (host)"; ss -ltnp 2>/dev/null | grep -E ':(1433|3000|1807[0-9]|1808[0-9]|1809[0-9]|500[0-9][0-9]|510[0-9][0-9]|520[0-9][0-9])\b' || echo none
    echo; echo "== memory"; free -m
  } 2>&1 | python3 -c '
import re,sys,pathlib
t=sys.stdin.read()
env=pathlib.Path(sys.argv[1])
if env.exists():
    for l in env.read_text().splitlines():
        if l.startswith("RH_SQL_PASSWORD=") and l.split("=",1)[1]:
            t=t.replace(l.split("=",1)[1],"[REDACTED]")
t=re.sub(r"(?i)((?:password|pwd)\s*[=:]\s*)[^\s;\x27\x22]+",r"\1[REDACTED]",t)
sys.stdout.write(t)' "$ROOT/docker/.env" > "$out"
  chmod 600 "$out"
  echo "Saved failure details: $out"
}

# Save Wine's err/fixme messages from the server container (password scrubbed).
save_wine_messages() {
  local out="$ROOT/logs/wine-debug-$(date -u +%Y%m%dT%H%M%SZ).txt"
  "${COMPOSE[@]}" logs --no-color --timestamps game 2>&1 | python3 -c '
import re,sys,pathlib
env=pathlib.Path(sys.argv[1]); secret=""
if env.exists():
    for l in env.read_text().splitlines():
        if l.startswith("RH_SQL_PASSWORD="): secret=l.split("=",1)[1]
pieces={secret[i:i+5] for i in range(max(0,len(secret)-4))} if len(secret)>=5 else set()
noise=re.compile(r"keysym|xkbcomp|not fatal to the X server|RtlInitializeCriticalSectionEx")
seen={}
out=[]
for line in sys.stdin:
    if noise.search(line): continue
    if any(p in line for p in pieces) or re.search(r"(?i)password|provider=|pwd=",line):
        line="[line removed: may contain credentials]\n"
    key=re.sub(r"^\S+\s+","",line)            # ignore docker timestamp for duplicate counting
    key=re.sub(r"0x[0-9a-fA-F]+|[0-9a-fA-F]{4,}|\d+","#",key)
    seen[key]=seen.get(key,0)+1
    if seen[key]<=20: out.append(line)
    elif seen[key]==21: out.append("  [further identical messages suppressed]\n")
sys.stdout.write("".join(out))' "$ROOT/docker/.env" > "$out"
  chmod 600 "$out"; echo "Saved Wine messages: $out"
}

start_server() {
  docker info >/dev/null 2>&1 || fail 'Docker is not available. Is the Docker service running?'
  if server_ready; then say 'Server already running - reusing it.'; return 0; fi
  say 'Starting server (this takes a minute or two)...'
  python3 docker/preflight.py || fail 'Preflight check failed (see message above).'
  python3 docker/prepare.py   || fail 'Preparing server settings failed.'
  "${COMPOSE[@]}" up -d --no-build --pull never sql || fail 'SQL Server failed to start.'
  if [[ -n "${TRACE_EARLY:-}" ]]; then
    for ((w=0;w<24;w++)); do python3 tools/lobby-db-trace.py start >/dev/null 2>&1 && { echo 'Login trace armed (covers server startup).'; TRACE_ARMED=1; break; }; sleep 5; done
  fi
  python3 docker/restore.py   || fail 'Database check/restore failed.'
  # A stopped game container keeps stale /tmp files (X display lock, ready
  # marker) that break the next start. Remove only the container; the Wine
  # prefix and database volumes are separate and are kept.
  "${COMPOSE[@]}" rm -f -s game >/dev/null 2>&1 || true
  "${COMPOSE[@]}" up -d --no-build --pull never api game || fail 'Game/API containers failed to start.'
  for ((i=0;i<200;i++)); do
    if server_ready; then
      sleep 5  # let the gateway finish opening logins after the marker appears
      echo 'Server is ready.'
      curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:3000/launcher/GetGatewayAction/info > logs/gateway-info.txt || true
      return 0
    fi
    if "${COMPOSE[@]}" ps --all --format '{{.Service}} {{.State}}' | grep -q 'game exited'; then
      capture_failure; fail 'The game server stopped during startup. Details were saved in the logs folder - tell Claude "it failed".'
    fi
    (( i % 6 == 0 )) && echo "  waiting for game server... ($((i*5))s)"
    sleep 5
  done
  capture_failure; fail 'Server startup timed out. Details were saved in the logs folder.'
}

play() {
  local trace="${1:-}"
  TRACE_ARMED=; TRACE_EARLY="$trace"
  if [[ -n "$trace" ]]; then
    # Wine error/"not implemented" messages for the server container (trace mode only).
    export RH_WINEDEBUG='+pid,+timestamp,err+all,fixme+all,-fixme+sync,-fixme+ntdll,-fixme+heap,-fixme+xrandr,-fixme+win'
    if server_ready; then say 'Restarting server so debug messages are enabled...'; "${COMPOSE[@]}" stop >/dev/null; fi
  fi
  exec 9>"$ROOT/runtime/docker-launcher.lock"
  if ! flock -n 9; then
    fail 'Another Rusty Hearts launcher window is still open. Close it (or choose "Stop server" first), then try again.'
  fi
  client_running && fail 'The game is already open. Close it first, or choose "Stop server".'
  start_server
  local watchdog=
  ( while sleep 2; do
      kb=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
      if (( kb < 2097152 )); then
        echo 'Low memory (<2 GiB free): stopping Rusty Hearts.' >&2
        systemctl --user stop rusty-hearts-client.scope 2>/dev/null || true
        "${COMPOSE[@]}" stop; exit
      fi
    done ) & watchdog=$!
  if [[ -n "$trace" ]]; then
    say 'Arming login trace...'
    if [[ -z "$TRACE_ARMED" ]]; then
      python3 tools/lobby-db-trace.py start || { echo 'Trace could not start; continuing without it.'; trace=; }
    fi
  fi
  if [[ -n "$trace" ]]; then
    say 'Attaching watch points to lobby and DBC (up to a minute)...'
    python3 tools/rmi-watch.py start || echo 'Watch points could not be set; continuing with the SQL trace only.'
  fi
  say 'Launching game. Close the game window when you are done.'
  [[ -n "$trace" ]] && echo 'Trace mode: log in, click "Ragezone", wait until it fails (~15 s), then close the game.'
  export RH_API_URL=http://127.0.0.1:3000
  systemd-run --user --scope --unit=rusty-hearts-client --quiet \
    -p MemoryMax=2G -p MemorySwapMax=0 -p TasksMax=256 -- "$ROOT/tools/run-client.sh" || true
  echo 'Game closed.'
  if [[ -n "$trace" ]]; then
    say 'Reading login trace...'
    python3 tools/lobby-db-trace.py read || true
    python3 docker/diagnostics.py >/dev/null 2>&1 || true
    save_wine_messages
    say 'Removing watch points...'
    python3 tools/rmi-watch.py stop || true
    say 'Capturing server debug snapshot (about a minute)...'
    python3 tools/debug-snapshot.py || true
    python3 tools/lobby-db-trace.py stop || true
  fi
  python3 docker/diagnostics.py >/dev/null 2>&1 || true
  kill "$watchdog" 2>/dev/null || true
  stop_all
  [[ -n "$trace" ]] && info 'Done. Trace saved in logs/diagnostics (lobby-db-trace-*.txt). Tell Claude "done".'
}

# ---------- non-interactive commands (used by RH Launcher) ----------
status_json() {
  local s ready=false client=false
  s=$(running_services | tr '\n' ' ')
  server_ready && ready=true
  client_running && client=true
  printf '{"running":"%s","ready":%s,"client":%s}\n' "${s% }" "$ready" "$client"
}

server_start_only() {
  exec 9>"$ROOT/runtime/docker-launcher.lock"
  flock -n 9 || fail 'Another launcher is already starting or running the game.'
  start_server
  echo 'SERVER_READY'
}

client_only() {
  server_ready || fail 'The server is not running. Start it first.'
  client_running && fail 'The game is already open.'
  export RH_API_URL=http://127.0.0.1:3000
  say 'Launching game.'
  systemd-run --user --scope --unit=rusty-hearts-client --quiet \
    -p MemoryMax=2G -p MemorySwapMax=0 -p TasksMax=256 -- "$ROOT/tools/run-client.sh"
}

action="${1:-}"
[[ -n "$action" ]] || action=$(menu)
case "$action" in
  play)   play ;;
  trace)  play trace ;;
  stop)   stop_all; info 'Server stopped.' ;;
  status) status ;;
  display) bash "$ROOT/tools/display-settings.sh" ;;
  server-start) server_start_only ;;
  server-stop) stop_all ;;
  status-json) status_json ;;
  client) client_only ;;
  *) exit 0 ;;
esac
if [[ -t 0 ]] && ! gui; then read -rp 'Press Enter to close.' _; fi
