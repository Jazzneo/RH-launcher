"""First-time setup: find the player's downloaded Rusty Hearts files and install everything.

The player downloads three archives themselves (from the RaGEZONE release) and the launcher does the rest:
  server pack  (server.7z    - "RH Server (RustyHeartenstein) 04292025")  -> server-package/
  server VM    (server-vm.7z - a Hyper-V .vhdx)    -> the Windows database drivers only (read-only)
  game client  (client.7z    - rustyhearts.exe + .pck files)               -> gameclient/
then: server settings, the Wine COM fix for the DBC servers, and the Docker images.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading

from .config import ROOT, LAUNCHER_DIR, DATA_DIR
from . import backend

SEVENZIP = os.path.join(LAUNCHER_DIR, "runtime", "bin", "7zzs")
PY = sys.executable or "python3"
SERVER_NAME = "RH Server (RustyHeartenstein) 04292025"
PKG = os.path.join(ROOT, "server-package")
SERVER_DIR = os.path.join(PKG, SERVER_NAME)
COMPOSE = ["compose", "-f", os.path.join(ROOT, "docker", "compose.yaml")]
IMAGES = ["mcr.microsoft.com/mssql/server:2022-latest", "rusty-hearts-offline-api", "rusty-hearts-offline-game"]
# The RaGEZONE forum, where the "RustyHeartenstein" release is posted (see docs/INSTALL-SERVER.md)
DOWNLOAD_PAGE = "https://forum.ragezone.com"
FORUM_PAGE = "https://forum.ragezone.com"
CACHE = os.path.join(DATA_DIR, "archives.json")
# The client archives lack D3DCompiler_43.dll (32-bit); Wine's built-in one can't compile the game's effects
# ("E5033: Unknown target profile '__fx_2_0__'"). It comes from Microsoft's DirectX June 2010 runtime.
DX_URL = ("https://download.microsoft.com/download/8/4/A/84A35BF1-DAFE-4AE8-82AF-AD2AE20B6B14/"
          "directx_Jun2010_redist.exe")
DX_CAB = "Jun2010_D3DCompiler_43_x86.cab"
D3DCOMPILER_SHA256 = "2f23182ec6f4889397ac4bf03d62536136c5bdba825c7d2c4ef08c827f3a8a1c"
ARCHIVE_EXT = (".7z", ".zip", ".rar", ".vhdx")
GIB = 1 << 30
# SteamOS / Steam Deck: no Docker, Podman instead (RHLauncher.sh sets RH_PODMAN=1 and puts a "docker"
# stand-in on PATH). Setup turns on Podman's Docker-compatible socket and downloads Docker Compose.
PODMAN = os.environ.get("RH_PODMAN") == "1"
COMPOSE_VERSION = "2.32.4"
COMPOSE_URL = (f"https://github.com/docker/compose/releases/download/v{COMPOSE_VERSION}/"
               "docker-compose-linux-x86_64")
COMPOSE_SHA256 = "ed1917fb54db184192ea9d0717bcd59e3662ea79db48bff36d3475516c480a6b"
COMPOSE_BIN = os.path.join(ROOT, "runtime", "podman", "docker-compose")


def podman_socket():
    return os.path.join(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}", "podman", "podman.sock")


class Cancelled(Exception):
    pass


# ------------------------------------------------------------------------------ archives
def _7z(args, **kw):
    if not os.path.isfile(SEVENZIP):
        raise RuntimeError("The bundled 7-Zip is missing (launcher/runtime/bin/7zzs). Re-download the launcher.")
    if not os.access(SEVENZIP, os.X_OK):
        os.chmod(SEVENZIP, 0o755)
    return subprocess.run([SEVENZIP] + args, capture_output=True, text=True, **kw)


def list_archive(path):
    """[(name, size, is_dir)] of an archive's members."""
    r = _7z(["l", "-ba", "-slt", "-sccUTF-8", path], timeout=120)
    out, cur = [], {}
    for line in (r.stdout + "\n").splitlines():
        if not line.strip():
            if "Path" in cur:
                out.append((cur["Path"].replace("\\", "/"), int(cur.get("Size") or 0),
                            "D" in cur.get("Attributes", "") or cur.get("Folder") == "+"))
            cur = {}
        elif " = " in line:
            k, v = line.split(" = ", 1)
            cur[k] = v
    return out


def identify(path):
    """{'kind': server|vm|client|vhdx, ...} or None."""
    if path.lower().endswith(".vhdx"):
        try:
            with open(path, "rb") as f:
                if f.read(8) == b"vhdxfile":
                    return {"kind": "vhdx", "member": None, "size": os.path.getsize(path)}
        except OSError:
            pass
        return None
    try:
        members = list_archive(path)
    except Exception:
        return None
    total = sum(s for _, s, _ in members)
    for name, size, d in members:
        if not d and name.split("/")[-1].lower() == DX_CAB.lower():
            return {"kind": "dxredist", "member": name, "size": total}
    for name, size, d in members:
        low = name.lower()
        if not d and low.endswith(".vhdx"):
            return {"kind": "vm", "member": name, "size": size}
    for name, size, d in members:
        parts = name.split("/")
        if not d and parts[-1].lower() == "rustyhearts.exe":
            return {"kind": "client", "member": name, "size": total}
    for name, size, d in members:
        parts = name.split("/")
        if len(parts) >= 3 and parts[1] == "Server" and parts[-1].lower() == "dbcserver_release_x64.exe":
            return {"kind": "server", "member": parts[0], "size": total}
    return None


def search_dirs():
    home = os.path.expanduser("~")
    user = os.environ.get("USER", "")
    dirs = [(os.path.join(ROOT, "downloads"), 2), (os.path.join(home, "Downloads"), 3),
            (os.path.join(home, "Desktop"), 2), (home, 1), (os.path.join(home, "Games"), 2)]
    for base in (f"/run/media/{user}", "/run/media", "/media", "/mnt"):
        dirs.append((base, 3))
    xdg = os.environ.get("XDG_DOWNLOAD_DIR")
    if xdg:
        dirs.insert(1, (xdg, 3))
    return [(d, depth) for d, depth in dirs if os.path.isdir(d)]


def _load_cache():
    try:
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(c):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump(c, f)
    except OSError:
        pass


def find_archives(progress=None, extra=None):
    """{'server': path, 'vm': path, 'client': path, 'vhdx': path} for what was found (newest wins)."""
    cache, seen, found = _load_cache(), set(), {}
    skip = {"node_modules", ".git", "compatdata", "steamapps", "pfx", "client-prefix", "runtime", "server-package"}
    candidates = []

    def walk(d, depth):
        try:
            entries = list(os.scandir(d))
        except OSError:
            return
        for e in entries:
            try:
                if e.is_file() and (e.name.lower().endswith(ARCHIVE_EXT) or
                                    (e.name.lower().startswith("directx") and e.name.lower().endswith(".exe"))):
                    if e.name.lower().endswith(".vhdx") or e.stat().st_size > 50 * 1024 * 1024:
                        candidates.append(e.path)
                elif depth > 1 and e.is_dir(follow_symlinks=False) and not e.name.startswith(".") \
                        and e.name not in skip:
                    walk(e.path, depth - 1)
            except OSError:
                pass

    for d, depth in ([(extra, 2)] if extra else []) + search_dirs():
        if progress:
            progress(f"Looking in {d}…")
        walk(d, depth)
    for p in candidates:
        real = os.path.realpath(p)
        if real in seen:
            continue
        seen.add(real)
        try:
            st = os.stat(real)
        except OSError:
            continue
        key = f"{real}|{st.st_size}|{int(st.st_mtime)}"
        if key in cache:
            info = cache[key]
        else:
            if progress:
                progress(f"Checking {os.path.basename(real)}…")
            info = identify(real)
            cache[key] = info
        if info:
            prev = found.get(info["kind"])
            if not prev or os.path.getmtime(prev) < st.st_mtime:
                found[info["kind"]] = real
                found[info["kind"] + "_info"] = info
    _save_cache(cache)
    return found


# ------------------------------------------------------------------------------- status
def server_files_ok():
    srv = os.path.join(SERVER_DIR, "Server")
    bak = os.path.join(SERVER_DIR, "DB Backup")
    return (os.path.isfile(os.path.join(srv, "DBCServer_Release_x64.exe")) and os.path.isdir(bak)
            and any(n.endswith(".bak") for _, _, fs in os.walk(bak) for n in fs))


def drivers_ok():
    from_list = ("msado15.dll", "msadrh15.dll", "msader15.dll", "msado28.tlb", "msado60.tlb", "msadce.dll",
                 "msdaps.dll", "msdatl3.dll", "oledb32.dll", "oledb32r.dll", "sqloledb.dll", "sqloledb.rll",
                 "dbnetlib.dll", "dpapi.dll", "msdadiag.dll", "msdart.dll", "msvcrt.dll", "sqlsrv32.dll")
    dlls = os.path.join(ROOT, "runtime", "linux", "windows-dlls")
    comp = os.path.join(ROOT, "runtime", "linux", "windows-components")
    return (all(os.path.isfile(os.path.join(dlls, f)) for f in from_list)
            and all(os.path.isfile(os.path.join(comp, f"{n}.tar")) for n in ("ado", "msadc", "oledb")))


def client_ok():
    d = backend.load_client_dir()
    return bool(d and backend.inspect_client(d)["ok"] and backend.has_d3dcompiler(d))


def configured_ok():
    srv = os.path.join(ROOT, "runtime", "linux", "server")
    return os.path.isfile(os.path.join(ROOT, "docker", ".env")) and os.path.isdir(os.path.join(srv, "Option"))


def patched_ok():
    try:
        out = subprocess.run([PY, os.path.join(ROOT, "tools", "patch-dbc-com.py"), "status"], capture_output=True,
                             text=True, timeout=30).stdout
    except Exception:
        return False
    return out.count(": patched") == 2


def docker_state():
    """('ok' | 'podman-setup' | 'missing' | 'denied' | 'stopped', detail)."""
    if PODMAN:
        try:
            r = subprocess.run(["podman", "version", "--format", "{{.Client.Version}}"], capture_output=True,
                               text=True, timeout=30)
        except Exception as e:
            return "stopped", f"Podman doesn't work: {e}"
        if r.returncode:
            return "stopped", "Podman doesn't work: " + ((r.stderr or "").strip().splitlines() or ["?"])[-1][:120]
        ver = r.stdout.strip()
        if not os.access(COMPOSE_BIN, os.X_OK) or not os.path.exists(podman_socket()):
            return "podman-setup", f"Podman {ver}: not turned on yet"
        return "ok", f"Podman {ver}, Compose {COMPOSE_VERSION}"
    try:
        cmd = backend._docker()
    except RuntimeError:
        return "missing", "Docker is not installed"
    try:
        r = subprocess.run(cmd + ["info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True,
                           timeout=20)
    except Exception as e:
        return "stopped", str(e)
    if r.returncode == 0:
        c = subprocess.run(cmd + ["compose", "version", "--short"], capture_output=True, text=True, timeout=20)
        if c.returncode:
            return "missing", "Docker Compose plugin is missing"
        return "ok", f"Docker {r.stdout.strip()}, Compose {c.stdout.strip()}"
    err = (r.stderr or "").lower()
    if "permission denied" in err:
        return "denied", "Your user may not use Docker yet"
    return "stopped", "The Docker service isn't running"


def compose_images():
    try:
        r = subprocess.run(backend._docker() + COMPOSE + ["config", "--images"], capture_output=True, text=True,
                           timeout=30, cwd=ROOT)
        names = [l.strip() for l in r.stdout.splitlines() if l.strip()]
        if r.returncode == 0 and names:
            return names
    except Exception:
        pass
    return IMAGES


def images_ok():
    try:
        r = subprocess.run(backend._docker() + ["image", "inspect"] + compose_images(), capture_output=True,
                           timeout=30)
        return r.returncode == 0
    except Exception:
        return False


def mem_total_gib():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / 1048576
    except OSError:
        pass
    return 0


def free_gib(path):
    while path and not os.path.exists(path):
        path = os.path.dirname(path)
    try:
        st = os.statvfs(path or "/")
        return st.f_bavail * st.f_frsize / GIB
    except OSError:
        return 0


def status():
    """Everything the Setup page shows. Cheap except for the docker calls."""
    d_state, d_detail = docker_state()
    return {"server": server_files_ok(), "drivers": drivers_ok(), "client": client_ok(),
            "configured": configured_ok(), "patched": patched_ok() if os.path.isdir(
                os.path.join(ROOT, "runtime", "linux", "server")) else False,
            "docker": d_state, "docker_detail": d_detail, "images": images_ok() if d_state == "ok" else False,
            "mem": mem_total_gib(), "free": free_gib(ROOT), "proton": backend.proton_check()}


def installed(st):
    return all(st[k] for k in ("server", "drivers", "client", "configured", "patched", "images"))


# ------------------------------------------------------------------------------ install
class Installer:
    """Runs the install steps in order; say(msg) for status, log(line) for the detail log."""

    def __init__(self, archives, say, log):
        self.a, self.say, self.log = archives, say, log
        self.proc = None
        self.cancelled = False
        self.fraction = None

    def cancel(self):
        self.cancelled = True
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, 15)
            except Exception:
                pass

    def _run(self, args, cwd=ROOT, pct=False):
        """Run a command, streaming output to the log. 7-Zip progress (NN%) goes to self.fraction."""
        if self.cancelled:
            raise Cancelled()
        self.proc = subprocess.Popen(args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     start_new_session=True, bufsize=0)
        buf, tail = b"", []
        while True:
            ch = self.proc.stdout.read(256)
            if not ch:
                break
            buf += ch
            parts = re.split(rb"[\r\n\x08]+", buf)
            buf = parts.pop()
            for p in parts:
                line = p.decode("utf-8", "replace").strip()
                if not line:
                    continue
                m = re.match(r"^(\d{1,3})%", line)
                if pct and m:
                    self.fraction = int(m.group(1)) / 100
                    continue
                tail.append(line)
                del tail[:-40]
                self.log(line)
        code = self.proc.wait()
        if self.cancelled:
            raise Cancelled()
        if code:
            raise RuntimeError(next((l for l in reversed(tail) if "error" in l.lower()), None)
                               or (tail[-1] if tail else f"exit code {code}"))
        return tail

    def _extract(self, archive, dest, *filters):
        os.makedirs(dest, exist_ok=True)
        self.fraction = 0.0
        self._run([SEVENZIP, "x", "-y", "-aoa", "-bsp1", "-bb0", archive,
                   "-o" + dest] + list(filters), pct=True)
        self.fraction = None

    def _need_space(self, where, gib, what):
        have = free_gib(where)
        if have < gib:
            raise RuntimeError(f"Not enough free disk space for {what}: need about {gib:.0f} GB, "
                               f"{have:.0f} GB free on the drive with {where}.")

    # steps ----------------------------------------------------------------------
    def server_files(self):
        if server_files_ok():
            self.log("Server files: already installed")
            return
        arc = self.a.get("server")
        if not arc:
            raise RuntimeError("The server pack (server.7z) wasn't found. Download it and press 'Look again'.")
        info = self.a.get("server_info") or {}
        if info.get("member") and info["member"] != SERVER_NAME:
            raise RuntimeError(f"That server pack is '{info['member']}'. This launcher works with "
                               f"'{SERVER_NAME}' from RaGEZONE.")
        self._need_space(PKG, (info.get("size", 3 * GIB)) / GIB + 1, "the server files")
        self.say("Unpacking the server files…")
        self._extract(arc, PKG)
        if not server_files_ok():
            raise RuntimeError("The server pack was unpacked but the server files aren't where expected.")

    def drivers(self):
        if drivers_ok():
            self.log("Database drivers: already installed")
            return
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import winvhdx
        disk = self.a.get("vhdx")
        tmp = None
        try:
            if not disk:
                arc = self.a.get("vm")
                if not arc:
                    raise RuntimeError("The server VM (server-vm.7z) wasn't found. Download it and press "
                                       "'Look again'.")
                info = self.a.get("vm_info") or {}
                size = info.get("size", 45 * GIB) / GIB
                base = os.path.join(ROOT, "runtime")
                self._need_space(base, size + 2, "unpacking the server VM disk (it is deleted again afterwards)")
                tmp = tempfile.mkdtemp(prefix="setup-vm-", dir=base)
                self.say(f"Unpacking the server VM disk ({size:.0f} GB, takes a while)…")
                self._extract(arc, tmp, "-ir!*.vhdx")
                hits = [os.path.join(dp, f) for dp, _, fs in os.walk(tmp) for f in fs if f.lower().endswith(".vhdx")]
                if not hits:
                    raise RuntimeError("No .vhdx disk came out of the server VM archive.")
                disk = hits[0]
            self.say("Copying the Windows database drivers from the VM disk…")
            winvhdx.extract_drivers(disk, ROOT, say=self.log)
        finally:
            if tmp:
                self.say("Removing the unpacked VM disk…")
                shutil.rmtree(tmp, ignore_errors=True)

    def client(self):
        cur = backend.load_client_dir()
        arc = self.a.get("client")
        info = self.a.get("client_info") or {}
        if cur and backend.inspect_client(cur)["ok"]:
            inside = os.path.realpath(cur).startswith(os.path.realpath(ROOT) + os.sep)
            room = free_gib(ROOT) >= info.get("size", 10 * GIB) / GIB + 1
            if inside or not arc or not room:
                # A client in this folder, or no client.7z / no room to unpack it: use the one that's set.
                self.log(f"Game client: {cur}" + ("" if inside or not arc else " (not enough space to unpack "
                                                                              "client.7z here)"))
                self.d3dcompiler(cur)
                return
            # The client that's set lives in another folder (e.g. another RH Launcher copy). Unpack this
            # install's own, so it doesn't break when that folder is moved or deleted.
            self.log(f"Game client set to another folder ({cur}); unpacking client.7z into this one")
        if not arc:
            raise RuntimeError("The game client (client.7z) wasn't found. Download it, or set your game folder "
                               "on the Game Client tab.")
        self._need_space(ROOT, info.get("size", 10 * GIB) / GIB + 1, "the game client")
        self.say("Unpacking the game client (about 10 GB)…")
        self._extract(arc, ROOT)
        exe = os.path.join(ROOT, *info.get("member", "gameclient/rustyhearts.exe").split("/"))
        folder = os.path.dirname(exe)
        if not backend.inspect_client(folder)["ok"]:
            raise RuntimeError("The client was unpacked but rustyhearts.exe or its data files are missing.")
        backend.save_client_dir(folder)
        cfg = os.path.join(folder, "Launcher", "Config.ini")
        try:
            with open(cfg, encoding="utf-8", errors="replace", newline="") as f:
                t = f.read()
            t2 = re.sub(r"(?m)^ServerURL=[^\r\n]*", "ServerURL=http://127.0.0.1:3000", t)
            if t2 != t:
                with open(cfg, "w", encoding="utf-8", newline="") as f:
                    f.write(t2)
        except OSError:
            pass
        self.d3dcompiler(folder)

    def d3dcompiler(self, folder):
        """Put Microsoft's D3DCompiler_43.dll (from the DirectX June 2010 runtime) into the client folder."""
        import hashlib
        import urllib.request
        if backend.has_d3dcompiler(folder):
            return
        tmp = tempfile.mkdtemp(prefix="setup-dx-", dir=os.path.join(ROOT, "runtime"))
        try:
            redist = self.a.get("dxredist")
            if not redist:
                self.say("Downloading the DirectX shader compiler from Microsoft (100 MB)…")
                redist = os.path.join(tmp, "directx_Jun2010_redist.exe")
                try:
                    with urllib.request.urlopen(DX_URL, timeout=60) as r, open(redist, "wb") as f:
                        total = int(r.headers.get("Content-Length") or 0)
                        got = 0
                        while True:
                            if self.cancelled:
                                raise Cancelled()
                            chunk = r.read(1 << 20)
                            if not chunk:
                                break
                            f.write(chunk)
                            got += len(chunk)
                            self.fraction = got / total if total else None
                except Cancelled:
                    raise
                except Exception as e:
                    raise RuntimeError("Couldn't download Microsoft's DirectX June 2010 runtime "
                                       f"({e}). Download 'directx_Jun2010_redist.exe' from Microsoft into your "
                                       "Downloads folder, press 'Look again', then Install again.")
                self.fraction = None
            self.say("Adding the DirectX shader compiler to the game folder…")
            self._run([SEVENZIP, "e", "-y", "-o" + tmp, redist, DX_CAB])
            self._run([SEVENZIP, "e", "-y", "-o" + tmp, os.path.join(tmp, DX_CAB), "D3DCompiler_43.dll"])
            dll = os.path.join(tmp, "D3DCompiler_43.dll")
            with open(dll, "rb") as f:
                digest = hashlib.sha256(f.read()).hexdigest()
            if digest != D3DCOMPILER_SHA256:
                raise RuntimeError("The DirectX package had an unexpected D3DCompiler_43.dll; not using it.")
            shutil.copy2(dll, os.path.join(folder, "D3DCompiler_43.dll"))
            self.log("D3DCompiler_43.dll added to the game folder")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def configure(self):
        self.say("Writing the local server settings…")
        self._run([PY, os.path.join(ROOT, "docker", "prepare.py")])

    def patch(self):
        self.say("Applying the Wine fix to the database servers…")
        self._run([PY, os.path.join(ROOT, "tools", "patch-dbc-com.py"), "apply"])
        if not patched_ok():
            raise RuntimeError("The Wine fix could not be applied (see the log).")

    def engine(self):
        """Podman only (Steam Deck): turn on Podman's Docker-compatible socket and get Docker Compose."""
        if not PODMAN:
            self.log("Docker is used directly")
            return
        import hashlib
        import time
        import urllib.request
        self.say("Turning on Podman's Docker-compatible service…")
        self._run(["systemctl", "--user", "enable", "--now", "podman.socket"])
        for _ in range(20):
            if os.path.exists(podman_socket()):
                break
            time.sleep(0.5)
        if not os.path.exists(podman_socket()):
            raise RuntimeError("Podman's service socket didn't appear (systemctl --user status podman.socket).")
        if os.access(COMPOSE_BIN, os.X_OK):
            self.log(f"Docker Compose {COMPOSE_VERSION}: already downloaded")
            return
        self.say(f"Downloading Docker Compose {COMPOSE_VERSION} from GitHub (65 MB)…")
        os.makedirs(os.path.dirname(COMPOSE_BIN), exist_ok=True)
        part = COMPOSE_BIN + ".part"
        h = hashlib.sha256()
        try:
            with urllib.request.urlopen(COMPOSE_URL, timeout=60) as r, open(part, "wb") as f:
                total, got = int(r.headers.get("Content-Length") or 0), 0
                while True:
                    if self.cancelled:
                        raise Cancelled()
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    h.update(chunk)
                    got += len(chunk)
                    self.fraction = got / total if total else None
        except Cancelled:
            raise
        except Exception as e:
            raise RuntimeError(f"Couldn't download Docker Compose ({e}). Check the internet connection.")
        finally:
            self.fraction = None
        if h.hexdigest() != COMPOSE_SHA256:
            os.remove(part)
            raise RuntimeError("The Docker Compose download didn't match its published checksum; not using it.")
        os.chmod(part, 0o755)
        os.replace(part, COMPOSE_BIN)
        self.log(f"Docker Compose {COMPOSE_VERSION} installed")

    def images(self):
        if images_ok():
            self.log("Docker images: already built")
            return
        st, detail = docker_state()
        if st != "ok":
            raise RuntimeError(detail + ". See the Setup page.")
        dk = backend._docker()
        if PODMAN:
            self.say("Downloading SQL Server for Linux and building the servers with Podman (15-30 minutes)…")
        else:
            self.say("Downloading SQL Server for Linux (about 1.5 GB)…")
        self._run(dk + COMPOSE + ["pull", "sql"])
        self.say("Building the login server and the Wine game server (5-15 minutes)…")
        self._run(dk + COMPOSE + ["build", "--progress", "plain"])

    STEPS = [("server_files", "Server files"), ("drivers", "Database drivers"), ("client", "Game client"),
             ("configure", "Server settings"), ("patch", "Wine fix"), ("engine", "Container engine"), ("images", "Docker images")]

    def run_all(self, only=None):
        for i, (fn, label) in enumerate(self.STEPS):
            if only and fn not in only:
                continue
            self.step = (i + 1, len(self.STEPS), label)
            self.log(f"== {label}")
            getattr(self, fn)()
        self.say("Done")
