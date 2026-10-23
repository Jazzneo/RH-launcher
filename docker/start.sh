#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if ! command -v docker >/dev/null && command -v host-spawn >/dev/null; then
  exec host-spawn /usr/bin/bash "$ROOT/docker/start.sh" "$@"
fi
cd "$ROOT"
exec 9>"$ROOT/runtime/docker-launcher.lock"
flock -n 9 || { echo 'A Docker game launcher is already running.' >&2; exit 1; }
COMPOSE=(docker compose -f "$ROOT/docker/compose.yaml")
python3 docker/preflight.py
python3 docker/prepare.py
"${COMPOSE[@]}" up -d --no-build --pull never sql
watchdog_pid=
cleanup() {
  status=$?
  trap - EXIT
  if [[ -n "$watchdog_pid" ]]; then kill "$watchdog_pid" 2>/dev/null || true; fi
  if ((status)); then
    python3 "$ROOT/docker/diagnostics.py" || true
  fi
  "${COMPOSE[@]}" stop || true
  exit "$status"
}
trap cleanup EXIT
(
  while sleep 2; do
    available_kb="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
    if ((available_kb < 2097152)); then
      echo 'Stopping Rusty Hearts: less than 2 GiB RAM remains available.' >&2
      systemctl --user stop rusty-hearts-client.scope 2>/dev/null || true
      "${COMPOSE[@]}" stop
      exit
    fi
  done
) &
watchdog_pid=$!
python3 docker/restore.py
"${COMPOSE[@]}" up -d --no-build --pull never api game
echo 'Waiting for the game server. Inspect docker compose logs if it stops.'
ready=false
for ((i=0;i<200;i++)); do
  if "${COMPOSE[@]}" exec -T game test -f /tmp/rh-ready 2>/dev/null; then ready=true; break; fi
  state="$("${COMPOSE[@]}" ps --all --format '{{.Service}} {{.State}}')"
  if grep -q 'game exited' <<< "$state"; then "${COMPOSE[@]}" logs --tail 30 game; exit 1; fi
  sleep 5
done
[[ "$ready" == true ]] || { echo 'Server startup timed out.' >&2; exit 1; }
curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:3000/launcher/GetGatewayAction/info > logs/gateway-info.txt
export RH_API_URL=http://127.0.0.1:3000
systemd-run --user --scope --unit=rusty-hearts-client --quiet -p MemoryMax=2G -p MemorySwapMax=0 -p TasksMax=256 -- "$ROOT/tools/run-client.sh"
python3 "$ROOT/docker/diagnostics.py" || true
echo 'Game closed. Stopping the local server and preserving database data.'
