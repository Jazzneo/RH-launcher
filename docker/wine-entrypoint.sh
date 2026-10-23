#!/usr/bin/env bash
set -euo pipefail
export DISPLAY=:99
# A container restart keeps /tmp; stale X lock/socket would stop Xvfb.
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99 /tmp/rh-ready
Xvfb :99 -screen 0 1280x720x24 -nolisten tcp -fbdir /tmp &
xvfb_pid=$!
cleanup() { wineserver -k || true; kill "$xvfb_pid" 2>/dev/null || true; }
trap cleanup EXIT
trap 'exit 143' TERM INT
# One-time Wine setup (prefix update, database drivers, COM registration). The prefix lives in the
# wine11-data volume, so this only has to run again when Wine or the driver files change.
SETUP_VERSION=1
setup_marker="$WINEPREFIX/.rh-setup-done"
fingerprint="$( { echo "$SETUP_VERSION"; wine --version 2>/dev/null; \
  sha256sum /opt/rh-components/*.tar /opt/rh-windows/* /opt/rh/sqloledb.reg 2>/dev/null || true; } | sha256sum | cut -c1-64 )"
rh_wine_setup() {
  WINEDLLOVERRIDES='mscoree,mshtml=' timeout 90 wineboot -u
  system_dir="$WINEPREFIX/drive_c/windows/system32"
  for component in ado msadc oledb; do
    case "$component" in ado) folder=ADO ;; msadc) folder=MSADC ;; oledb) folder='OLE DB' ;; esac
    target="$WINEPREFIX/drive_c/Program Files/Common Files/System/$folder"
    mkdir -p "$target"
    tar -xf "/opt/rh-components/$component.tar" -C "$target"
    find "$target" -maxdepth 1 -type f \( -iname '*.dll' -o -iname '*.rll' \) -exec cp -t "$system_dir" {} +
    if [[ -d "$target/en-US" ]]; then
      mkdir -p "$system_dir/en-US"
      cp -a "$target/en-US/." "$system_dir/en-US/"
    fi
  done
  for dll in sqloledb.dll sqloledb.rll oledb32.dll oledb32r.dll dpapi.dll msdart.dll msdatl3.dll msdaps.dll msdadiag.dll msadce.dll dbnetlib.dll sqlsrv32.dll msvcrt.dll; do
    if [[ -f "/opt/rh-windows/$dll" ]]; then
      cp "/opt/rh-windows/$dll" "$WINEPREFIX/drive_c/windows/system32/$dll"
    fi
  done
  ado_dir="$WINEPREFIX/drive_c/Program Files/Common Files/System/ADO"
  mkdir -p "$ado_dir"
  for file in msado15.dll msadrh15.dll msader15.dll msado28.tlb msado60.tlb; do
    cp "/opt/rh-windows/$file" "$ado_dir/$file"
  done
  oledb_dir="$WINEPREFIX/drive_c/Program Files/Common Files/System/OLE DB"
  mkdir -p "$oledb_dir"
  cp /opt/rh-windows/oledb32.dll /opt/rh-windows/oledb32r.dll "$oledb_dir/"
  timeout 30 wine regsvr32 /s 'C:\windows\system32\oledb32.dll'
  timeout 30 wine regsvr32 /s 'C:\windows\system32\msdaps.dll'
  timeout 30 wine regsvr32 /s 'C:\windows\system32\msadce.dll'
  timeout 30 wine regsvr32 /s 'C:\windows\system32\sqloledb.dll'
  timeout 30 wine regedit /S 'Z:\opt\rh\sqloledb.reg'
  timeout 30 wine regsvr32 /s 'C:\Program Files\Common Files\System\ADO\msado15.dll'
  timeout 30 wine regsvr32 /s 'C:\Program Files\Common Files\System\ADO\msadrh15.dll'
}
# Native Microsoft database drivers (used by the registration below and by the game servers).
export WINEDLLOVERRIDES='mscoree,mshtml=;sqloledb,oledb32,msdart,msdatl3,msdaps,msdadiag,msadce,msado15,msadrh15,dbnetlib,sqlsrv32,msvcrt=n,b'
if [[ -f "$setup_marker" && -f "$WINEPREFIX/system.reg" && "$(cat "$setup_marker")" == "$fingerprint" ]]; then
  echo 'Wine setup already done; skipping it.'
else
  echo 'Setting up Wine (first start, or Wine/drivers changed)...'
  rm -f "$setup_marker"
  rh_wine_setup
  echo "$fingerprint" > "$setup_marker"
fi
python3 /opt/rh/configure-server.py
# Probe is intentionally before the game processes: do not start a broken backend.
rm -f /tmp/rh-probe-result.txt /tmp/rh-ready
probe_status=0
timeout 30 wine /opt/rh/db-probe.exe || probe_status=$?
cat /tmp/rh-probe-result.txt 2>/dev/null || true
if ((probe_status)); then exit "$probe_status"; fi
if [[ "${1:-}" == probe ]]; then exit 0; fi
cd /srv/game
agent_ready=false
for attempt in 1 2 3; do
  wine /opt/rh/console-input.exe --launch Agent_Release_x64.exe 1 &
  agent_pid=$!
  sleep 8
  if kill -0 "$agent_pid" 2>/dev/null; then agent_ready=true; break; fi
  wait "$agent_pid" || true
  echo "Agent exited during startup (attempt $attempt/3)."
  sleep 3
done
[[ "$agent_ready" == true ]] || { echo 'Agent startup failed; inspect /tmp/rh-agent-exit.txt.' >&2; exit 1; }
wine /opt/rh/console-input.exe --launch AgentManager_Release_x64.exe &
manager_pid=$!
ready=false
for ((i=0;i<180;i++)); do
  if python3 - <<'PY'
import os, socket
def up(port):
    # In network play the servers may listen on the PC's network address instead of 127.0.0.1.
    for host in ('127.0.0.1', os.environ.get('RH_PUBLIC_IP') or '127.0.0.1'):
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            pass
    return False
if not all(up(p) for p in (50001, 50006, 50016)):
    raise SystemExit(1)
PY
  then ready=true; break; fi
  kill -0 "$agent_pid"
  kill -0 "$manager_pid"
  sleep 5
done
if [[ "$ready" != true ]]; then echo 'Game services did not finish loading.' >&2; exit 1; fi
wine /opt/rh/console-input.exe GameGatewayServer_Release_x64.exe uol
touch /tmp/rh-ready
echo 'Rusty Hearts game services ready.'
wait "$agent_pid"
