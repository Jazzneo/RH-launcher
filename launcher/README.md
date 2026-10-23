# RH Launcher – Rusty Hearts Offline

Controller-friendly launcher (Steam Deck style, built like MH Launcher) for the offline
Rusty Hearts server in this folder.

Start: `launcher/RHLauncher.sh` (or the "Rusty Hearts Offline" desktop shortcut).

| Tab | What it does |
| --- | --- |
| Setup | First-time install: finds the downloaded server.7z / server-vm.7z / client.7z (Downloads, SD/USB, or a picked folder) and installs everything with one button; checks Docker, Proton, memory and disk space. |
| Play | PLAY starts the server if needed, then the game. STOP GAME closes it. Turn the server on/off. |
| Accounts | Create game accounts (on-screen keyboard) and see the accounts on the server. |
| Server | Turn on / off / restart, live status of database, login API, game servers and ports, activity log. |
| Game Client | Find the player's own Rusty Hearts client automatically (home, Steam libraries, SD/USB) or browse to it; checks for rustyhearts.exe + .pck files and shows the Proton that will be used. |
| Display | Graphics: Proton native Wayland (default) or X11 + gamescope; Windowed / Borderless / Fullscreen; resolution. |
| Settings | Turn the server off when the game closes / when the launcher quits; launcher fullscreen; Add to Steam. |

Controls: D-pad/stick move, A select, B back, LB/RB tabs, START play, SELECT quit. Mouse, touch and keyboard work too.

How it works: the GUI calls `docker/launcher.sh` (`server-start`, `server-stop`, `status-json`, `client`);
the game is started by `tools/run-client.sh`, which reads `runtime/client-display.conf`.
Python + pygame-ce are bundled in `runtime/python` (from MH Launcher); 7-Zip (`runtime/bin/7zzs`, LGPL) unpacks the downloads; `tools/winvhdx.py` reads the server VM disk without libguestfs. UI toolkit `ui.py` is MH Launcher's (GPL-3.0).
Logs: `logs/rhlauncher.log` (launcher), `logs/rhlauncher-client.log` (client start), `logs/steam-0.log` (Proton).

The client is NOT bundled: its folder is saved in `runtime/client.conf` (`CLIENT_DIR=`). If unset, `gameclient/` next to the launcher is used when present.
Proton is found automatically: RH_PROTON, else Proton - Experimental (any Steam library), else newest GE-Proton (compatibilitytools.d), else newest Valve Proton. Flatpak Steam is supported.
