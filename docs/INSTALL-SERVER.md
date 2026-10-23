# Installing the Rusty Hearts server on Linux (for RH Launcher)

This guide sets up the community **RustyHeartenstein** Rusty Hearts server pack (from RaGEZONE) on Linux,
so RH Launcher can start it with one button. It runs the original Windows server programs in Docker
(Wine), with Microsoft SQL Server for Linux and JuniorDark's Rusty Hearts API.

**No game files are included in this project.** You download the server pack, the server VM and the
game client yourself from the community release (see *Where to get the files*).

---

## What you need

| | |
|---|---|
| Linux PC (x86-64) | Tested on Bazzite (KDE). Other distros should work. |
| Memory | **16 GB free RAM** when the server starts (32 GB installed recommended). |
| Disk space | About **60 GB** free while installing (the VM disk is unpacked for a moment, then deleted), about 20 GB afterwards plus the downloads. |
| Docker | Docker Engine **with the Compose plugin** (`docker compose version` must work). On Bazzite use the developer image (bazzite-dx), which includes Docker. Your user must be allowed to run `docker`. |
| Steam + Proton | Install **Proton Experimental** (Steam → Library → Tools) or GE-Proton. |
| Tools | Nothing else: RH Launcher brings its own Python and 7-Zip. |

## Where to get the files

All three come from the RaGEZONE Rusty Hearts release (the "RustyHeartenstein" server pack, April 2025)
and the community setup guide that goes with it:

- RaGEZONE Rusty Hearts forum: https://forum.ragezone.com (Rusty Hearts section, RustyHeartenstein release)
- Community setup guide (Windows / Hyper-V): https://docs.google.com/document/d/1X-fAsj4iDx5t4P9dz17ViR--8V22cvpdhdKV7k0z3hI/edit

Download:

1. **Server pack**: `RH Server (RustyHeartenstein) 04292025`
2. **Server VM** (Hyper-V, "RustyHearts WS"). Only needed once, to copy Windows database drivers out of it.
3. **Game client**: the **USA** client. RH Launcher was tested with **Revision 33581 / Resource 88185**
   (shown in red at the top-left of the game screen).

---

## Easy install (recommended)

1. Download RH Launcher (https://github.com/Jazzneo/RH-launcher, green **Code** button → *Download ZIP*) and extract it, for example to
   `~/Games/RH-launcher`.
2. Download the three files above (`server.7z`, `server-vm.7z`, `client.7z`) into your **Downloads**
   folder. Don't unpack them.
3. Start `launcher/RHLauncher.sh`. It opens on the **Setup** tab, which shows the files it found and
   what this PC still needs (Docker, Proton, memory, disk space).
4. Press **Install everything**. The launcher then, by itself:
   - unpacks the server pack into `server-package/`,
   - unpacks the VM disk for a moment, copies the 18 Windows database drivers out of it (read-only,
     with its built-in reader `tools/winvhdx.py`), and deletes the unpacked disk again,
   - unpacks the game client into `gameclient/` and points the game at your own server,
   - writes the server settings (`docker/prepare.py`) and applies the Wine fix for the database
     relay (`tools/patch-dbc-com.py`),
   - downloads SQL Server for Linux and builds the two Docker images.

   The first install takes about 20-40 minutes. If something stops it, fix the problem and press
   Install again: finished steps are skipped. The log is `logs/rhlauncher-setup.log`.
5. **Accounts** tab: create your game account. **Play**: press PLAY.

The files aren't in Downloads? Use **Pick a folder…** on the Setup tab. Already have the game client
installed somewhere? Set it on the **Game Client** tab and Setup won't need `client.7z`.

The steps below do the same thing by hand.

---

## Manual install (advanced)

### Step 1: Get RH Launcher

Download https://github.com/Jazzneo/RH-launcher (green **Code** button → *Download ZIP*) and extract it, for example to
`~/Games/RH-launcher`. All commands below are run from that folder:

```bash
cd ~/Games/RH-launcher
```

### Step 2: Put the server pack in place

Extract the server pack into `server-package/` so these exist:

```
server-package/RH Server (RustyHeartenstein) 04292025/Server/
server-package/RH Server (RustyHeartenstein) 04292025/DB Backup/RustyHearts_Backup_20250429175454/RustyHearts.bak
                                                                                          (+ RustyHearts_Account, _Auth, _Log, GMRustyHearts .bak)
```

### Step 3: Unpack the server VM

Extract the server VM anywhere with free space. You need the disk file:

```
RustyHearts WS/Virtual Hard Disks/RustyHearts WinServer 2022.vhdx
```

### Step 4: Copy the Windows database drivers out of the VM

The game servers talk to SQL Server through Microsoft's ADO / OLE DB drivers, which Wine does not
include. This copies the 18 driver files (and three driver folders) out of the VM disk. It opens the
disk **read-only**; nothing in it is changed, and it only needs the Python that comes with RH Launcher:

```bash
launcher/runtime/python/bin/python3 tools/winvhdx.py drivers \
    "/path/to/RustyHearts WS/Virtual Hard Disks/RustyHearts WinServer 2022.vhdx" .
```

It ends with `Done: 3 folders and 18 driver files installed.` You can delete the VM files afterwards.
(The older `tools/extract-windows-drivers.sh` does the same with libguestfs.)

### Step 5: Prepare and build the server

```bash
python3 docker/prepare.py
docker compose -f docker/compose.yaml build
```

`prepare.py` copies the server to `runtime/linux/server`, writes its settings for this PC (localhost),
and creates a random SQL password in `docker/.env` (keep that file private). The build downloads
Ubuntu, Wine, SQL Server and Node.js images, so it takes a while the first time.

### Step 6: Apply the Wine fix for the database relay (DBC)

Under Wine, the server's database relay never turns on Windows COM for its worker threads, so the
character list and guild data never load (you get stuck at "waiting" after picking the world).
This patch fixes it (23 bytes in unused space, originals kept in `runtime/linux/server/_original_exe/`):

```bash
python3 tools/patch-dbc-com.py apply
python3 tools/patch-dbc-com.py status     # both should say "patched"
```

### Step 7: Start RH Launcher

```bash
launcher/RHLauncher.sh
```

1. **Game Client** tab → **Find my game automatically** (or **Browse…** to the folder with `rustyhearts.exe`).
2. **Accounts** tab → create an account. If the server is off, the launcher offers to turn it on.
   The very first start restores the databases from the backups, so it takes a few minutes.
3. **Play** → press **PLAY** (or START on a controller) and log in with your new account.

**Settings → Add to Steam** puts RH Launcher in your Steam library (works in Gaming Mode too).

---

## Troubleshooting

| Problem | Fix |
|---|---|
| "Refusing startup: … GiB RAM available" | Close memory-heavy apps. The server needs 16 GB free to start. |
| "Docker is not available" | Start Docker (`sudo systemctl enable --now docker`) and make sure your user can run `docker ps`. |
| "Port … is already in use" | Another program uses that port (1433, 3000, 18070-18090, 50001-50091). Close it or reboot. |
| Stuck at "waiting" after choosing the world | Step 6 wasn't applied: run `python3 tools/patch-dbc-com.py status`. |
| "The game server stopped during startup" | Look at `logs/launcher-failure-*.txt`. |
| Mouse cursor jumps around in game | Display tab → Graphics: **Proton native Wayland**. |
| White screen / crash with Vulkan | Display tab → Renderer: **OpenGL**. |
| "Proton not found" | Install Proton Experimental in Steam (Library → Tools). |

Logs are in `logs/`: `rhlauncher.log` (launcher), `steam-0.log` (game client / Proton),
`launcher-failure-*.txt` (server start problems).

Your characters live in the Docker volume `rusty-hearts-offline_sql-data`. **Never** run
`docker compose down -v`: that deletes them.

---

## Credits

- **JuniorDark**: Rusty Hearts API, Rusty Hearts Launcher, Rusty Hearts Server Manager (all 0BSD),
  the RustyHearts server VM and RustyHearts-MIPTool. https://github.com/JuniorDark
  This project includes a modified copy of the API (offline account/login changes) and the Server
  Manager's option templates.
- **RaGEZONE community**: the RustyHeartenstein server pack and the Rusty Hearts setup guide.
- **Stairway Games / Perfect World**: Rusty Hearts. This project is not affiliated with them.
- Wine, Valve Proton, DXVK, gamescope, Microsoft SQL Server for Linux (Docker image), pygame-ce.
- **7-Zip** by Igor Pavlov (GNU LGPL, unRAR restriction): the `7zzs` binary bundled in `launcher/runtime/bin/`,
  unmodified, from https://github.com/ip7z/7zip/releases (license: `launcher/runtime/bin/7zip-License.txt`).
