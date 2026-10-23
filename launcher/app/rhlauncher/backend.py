"""Talks to the Docker server stack and the client scripts."""
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time

from .config import ROOT, SERVER_SCRIPT

PORTS = {"Login API": 3000, "Auth (USA)": 18070, "Gateway": 50001, "Lobby": 50006, "Dungeon": 50016}
SERVICES = [("sql", "Database (SQL Server)"), ("api", "Login API"), ("game", "Game servers (Wine)")]
CLIENT_UNIT = "rusty-hearts-client.scope"


def _env():
    e = dict(os.environ)
    e["RH_NO_POPUPS"] = "1"
    return e


def port_open(port, timeout=0.4, host="127.0.0.1"):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def mem_available_gib():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1048576
    except OSError:
        pass
    return None


# ------------------------------------------- the other container engine
# On a PC with both Docker and Podman (Bazzite), a server started by one engine keeps its ports busy for
# the other. This finds and stops RH containers of the engine the launcher is NOT using.
OTHER_ENGINE = "Docker" if os.environ.get("RH_PODMAN") == "1" else "Podman"


def _other_engine_cmd():
    if OTHER_ENGINE == "Podman":
        return shutil.which("podman")
    shim = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "runtime", "podman-shim")
    shim = os.path.realpath(shim)
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if d and os.path.realpath(d) != shim:
            c = os.path.join(d, "docker")
            if os.path.isfile(c) and os.access(c, os.X_OK):
                return c
    return None


def other_engine_running():
    """Names of running Rusty Hearts containers in the other engine ([] if none or not installed)."""
    cmd = _other_engine_cmd()
    if not cmd:
        return []
    try:
        r = subprocess.run([cmd, "ps", "--filter", "name=rusty-hearts-offline-", "--format", "{{.Names}}"],
                           capture_output=True, text=True, timeout=15)
    except Exception:
        return []
    return [n for n in r.stdout.split() if n.startswith("rusty-hearts-offline-")] if r.returncode == 0 else []


def stop_other_engine(progress=None):
    names = other_engine_running()
    if not names:
        return f"The {OTHER_ENGINE} server is already off."
    if progress:
        progress(f"Stopping the {OTHER_ENGINE} server (your characters are saved)…")
    r = subprocess.run([_other_engine_cmd(), "stop", "-t", "60"] + names, capture_output=True, text=True,
                       timeout=180)
    if r.returncode:
        raise RuntimeError(f"Stopping the {OTHER_ENGINE} server failed: " + (r.stderr or r.stdout).strip()[-300:])
    return f"{OTHER_ENGINE} server turned off."


class ServerState:
    """Polls the stack every few seconds in a background thread."""

    def __init__(self):
        self.running = []          # compose services running
        self.ports = {}            # name -> bool
        self.ready = False
        self.client = False
        self.other = []            # RH containers running in the other engine (Docker vs Podman)
        self.docker_ok = True
        self.busy = None           # "Starting" / "Stopping" while a task runs
        self.checked = 0
        self._lock = threading.Lock()
        self._kick = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def refresh_soon(self):
        self._kick.set()

    @property
    def state(self):
        if self.busy:
            return self.busy
        if self.ready:
            return "Online"
        if self.running:
            return "Not ready"
        return "Offline"

    def _poll(self):
        net = net_load()
        if net["mode"] == "join":
            # Playing on another PC's server: just check that it answers.
            ip = net["join_ip"]
            ports = {name: bool(ip) and port_open(p, 1.5, ip) for name, p in PORTS.items() if p in (3000, 50001,
                                                                                                    50006, 50016)}
            client = subprocess.run(["systemctl", "--user", "is-active", "--quiet", CLIENT_UNIT]).returncode == 0
            with self._lock:
                self.running, self.ports, self.docker_ok, self.other = [], ports, True, []
                self.ready = bool(ports) and all(ports.values())
                self.client, self.checked = client, time.time()
            return
        try:
            r = subprocess.run(["bash", SERVER_SCRIPT, "status-json"], capture_output=True, text=True,
                               timeout=40, env=_env())
            m = re.search(r"\{.*\}", r.stdout)
            data = json.loads(m.group(0)) if m else {}
            running = data.get("running", "").split()
            docker_ok = r.returncode == 0 and bool(m)
        except Exception:
            running, docker_ok, data = [], False, {}
        pub = net["public_ip"]
        ports = {name: port_open(p) or (pub != "127.0.0.1" and port_open(p, host=pub)) for name, p in PORTS.items()}
        client = subprocess.run(["systemctl", "--user", "is-active", "--quiet", CLIENT_UNIT]).returncode == 0
        other = other_engine_running()
        with self._lock:
            self.running, self.ports, self.docker_ok = running, ports, docker_ok
            self.ready = bool(data.get("ready")) and all(ports.values())
            self.client = client
            self.other = other
            self.checked = time.time()

    def _loop(self):
        while True:
            self._poll()
            self._kick.wait(4)
            self._kick.clear()


class Job:
    """Runs a launcher.sh command, collecting output lines for the progress log."""

    def __init__(self, args):
        self.args = args
        self.lines = []
        self.proc = None
        self.code = None

    def run(self, on_line=None):
        self.proc = subprocess.Popen(["bash", SERVER_SCRIPT] + self.args, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, env=_env(), start_new_session=True,
                                     cwd=ROOT)
        for line in self.proc.stdout:
            line = line.rstrip()
            if line:
                self.lines.append(line)
                del self.lines[:-200]
                if on_line:
                    on_line(line)
        self.code = self.proc.wait()
        return self.code

    def cancel(self):
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, 15)
            except Exception:
                pass


def start_client():
    """Starts the game (blocking scope) in the background; returns the Popen."""
    log = open(os.path.join(ROOT, "logs", "rhlauncher-client.log"), "w")
    return subprocess.Popen(["bash", SERVER_SCRIPT, "client"], stdout=log, stderr=subprocess.STDOUT,
                            env=_env(), start_new_session=True, cwd=ROOT)


def stop_client():
    subprocess.run(["systemctl", "--user", "stop", CLIENT_UNIT], capture_output=True)


def screen_sizes(backend):
    """(native, choices) for the Display tab, using the same rules as tools/display-common.sh."""
    script = ('. "$1/tools/display-common.sh"; if [ "$2" = wayland ]; then n=$(physical_size); '
              'else n=$(native_size); fi; echo "$n"; size_choices "$n"')
    try:
        out = subprocess.run(["bash", "-c", script, "x", ROOT, backend], capture_output=True, text=True,
                             timeout=10).stdout.split()
    except Exception:
        out = []
    out = [s for s in out if re.match(r"^\d+x\d+$", s)]
    if not out:
        return "1920x1080", ["1920x1080", "1280x720"]
    return out[0], out[1:] or [out[0]]


def gamescope_available():
    return shutil.which("gamescope") is not None


# ---------------------------------------------------------------- accounts
import urllib.request
import urllib.error

SQL_CONTAINER = "rusty-hearts-offline-sql-1"
API = "http://127.0.0.1:3000"
USERNAME_RE = re.compile(r"^[a-z0-9]{6,16}$")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._+-]{1,64}@[A-Za-z0-9.-]{1,180}\.[A-Za-z]{2,24}$")

RESULT_TEXT = {
    "AccountCreated": "Account created.",
    "AccountExists": "An account with that email already exists.",
    "EmailExists": "An account with that email already exists.",
    "UsernameExists": "That username is already taken.",
    "InvalidVerificationCode": "The sign-up code didn't match. Please try again.",
    "ExpiredVerificationCode": "The sign-up code expired. Please try again.",
    "TooManyAttempts": "Too many sign-up attempts. Wait a few minutes and try again.",
}


def _docker():
    d = shutil.which("docker")
    if d:
        return [d]
    if os.path.exists("/run/host/usr/bin/docker"):
        return ["/run/host/usr/bin/docker"]
    if shutil.which("host-spawn"):
        return ["host-spawn", "docker"]
    raise RuntimeError("Docker was not found.")


def sql(query, timeout=30):
    """Run T-SQL inside the SQL container (its own SA password never leaves it). Returns rows of fields."""
    script = ('SQLCMDPASSWORD="$MSSQL_SA_PASSWORD" /opt/mssql-tools18/bin/sqlcmd -S 127.0.0.1 -U sa -C -b '
              '-h -1 -W -s "|" -i /dev/stdin')
    r = subprocess.run(_docker() + ["exec", "-i", SQL_CONTAINER, "sh", "-c", script], input=query,
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError("Database query failed: " + (r.stderr or r.stdout).strip()[-300:])
    return [ln.split("|") for ln in r.stdout.splitlines() if ln.strip() and not ln.startswith("(")]


WORLD_ID = 10101
ZEN_MAX = 2000000000          # the server reads the balance as a 32-bit int
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,50}$")   # existing account names (older ones may differ from new rules)


def list_accounts():
    # Zen: the shop looks the balance up by the id the login API returns (AccountID since our API change),
    # so prefer that row and fall back to the username row.
    rows = sql("SET NOCOUNT ON; SELECT a.WindyCode, a.Email, CONVERT(varchar(16), a.CreatedAt, 120), "
               f"COALESCE(ci.Zen, cn.Zen, 0) FROM RustyHearts_Account.dbo.AccountTable a "
               f"LEFT JOIN RustyHearts_Account.dbo.CashTable ci ON ci.WindyCode = CAST(a.AccountID AS varchar(20)) "
               f"AND ci.WorldId = {WORLD_ID} "
               f"LEFT JOIN RustyHearts_Account.dbo.CashTable cn ON cn.WindyCode = a.WindyCode AND cn.WorldId = {WORLD_ID} "
               "ORDER BY a.CreatedAt;")
    out = []
    for r in rows:
        if r and r[0]:
            zen = r[3].strip() if len(r) > 3 else "0"
            out.append({"username": r[0], "email": r[1] if len(r) > 1 else "", "created": r[2] if len(r) > 2 else "",
                        "zen": int(zen) if zen.lstrip("-").isdigit() else 0})
    return out


def add_zen(username, amount):
    """Add Zen to an account (both possible balance rows). Returns the new balance."""
    if not NAME_RE.match(username or ""):
        raise ValueError("Unexpected account name.")
    amount = int(amount)
    if not 1 <= amount <= ZEN_MAX:
        raise ValueError("Amount out of range.")
    q = f"""SET NOCOUNT ON;
DECLARE @u varchar(255) = '{username}', @amt bigint = {amount}, @w int = {WORLD_ID}, @max bigint = {ZEN_MAX};
DECLARE @id varchar(255) = (SELECT CAST(AccountID AS varchar(20)) FROM RustyHearts_Account.dbo.AccountTable WHERE WindyCode = @u);
IF @id IS NULL BEGIN SELECT 'NOACCOUNT'; RETURN; END
DECLARE @k TABLE (k varchar(255) COLLATE Chinese_PRC_CI_AS);
INSERT INTO @k SELECT DISTINCT v FROM (VALUES (@u), (@id)) t(v);
UPDATE c SET Zen = CASE WHEN ISNULL(c.Zen, 0) + @amt > @max THEN @max ELSE ISNULL(c.Zen, 0) + @amt END
  FROM RustyHearts_Account.dbo.CashTable c JOIN @k ON c.WindyCode = k WHERE c.WorldId = @w;
INSERT INTO RustyHearts_Account.dbo.CashTable (WindyCode, WorldId, Zen)
  SELECT k, @w, CASE WHEN @amt > @max THEN @max ELSE @amt END FROM @k
  WHERE NOT EXISTS (SELECT 1 FROM RustyHearts_Account.dbo.CashTable c WHERE c.WindyCode = k AND c.WorldId = @w);
SELECT Zen FROM RustyHearts_Account.dbo.CashTable WHERE WindyCode = @id AND WorldId = @w;"""
    rows = sql(q)
    if rows and rows[0] and rows[0][0].strip() == "NOACCOUNT":
        raise ValueError("That account wasn't found.")
    return int(rows[-1][0]) if rows and rows[-1] and rows[-1][0].strip().isdigit() else None


def delete_account(username):
    """Delete an account's login, Zen and sign-up codes. Backs up RustyHearts_Account first.
    Characters in the game database are left untouched. Returns the backup file name."""
    if not NAME_RE.match(username or ""):
        raise ValueError("Unexpected account name.")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    # The data folder is owned by SQL Server's own user (the backup/ folder is root-owned: access denied).
    bak = f"/var/opt/mssql/data/RustyHearts_Account-before-delete-{username}-{stamp}.bak"
    sql("SET NOCOUNT ON; "
        f"BACKUP DATABASE RustyHearts_Account TO DISK = N'{bak}' WITH COPY_ONLY, INIT;", timeout=180)
    q = f"""SET NOCOUNT ON; SET XACT_ABORT ON; USE RustyHearts_Account;
DECLARE @u varchar(255) = '{username}';
DECLARE @id varchar(32), @email varchar(255);
SELECT @id = CAST(AccountID AS varchar(32)), @email = Email FROM dbo.AccountTable WHERE WindyCode = @u;
IF @id IS NULL BEGIN SELECT 'NOACCOUNT'; RETURN; END
BEGIN TRAN;
DELETE FROM dbo.CashTable WHERE WindyCode IN (@u, @id);
IF OBJECT_ID('dbo.VerificationCode') IS NOT NULL AND COL_LENGTH('dbo.VerificationCode', 'Email') IS NOT NULL
  EXEC sp_executesql N'DELETE FROM dbo.VerificationCode WHERE Email = @e', N'@e varchar(255)', @e = @email;
DELETE FROM dbo.AccountTable WHERE WindyCode = @u;
COMMIT;
SELECT 'DELETED';"""
    rows = sql(q)
    res = rows[-1][0].strip() if rows and rows[-1] else ""
    if res == "NOACCOUNT":
        raise ValueError("That account wasn't found.")
    if res != "DELETED":
        raise RuntimeError("The database didn't confirm the delete.")
    return os.path.basename(bak)


def _post(path, data, timeout=20):
    req = urllib.request.Request(API + path, data=json.dumps(data).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode() or "{}")
        except Exception:
            return {"success": False, "result": f"HTTP {e.code}"}


def validate_account(username, email, password, confirm):
    if not USERNAME_RE.match(username or ""):
        return "Username: 6 to 16 lowercase letters and numbers only."
    if email and not EMAIL_RE.match(email):
        return "Email doesn't look right (example: me@offline.local). You can also leave it empty."
    if not (8 <= len(password or "") <= 16):
        return "Password: 8 to 16 characters."
    if password != confirm:
        return "The two passwords don't match."
    return None


# Finds the table that holds sign-up codes (whatever its exact layout) and reads the newest code for an email.
_CODE_QUERY = """SET NOCOUNT ON; USE RustyHearts_Account;
DECLARE @t sysname = (SELECT TOP 1 c.TABLE_NAME FROM INFORMATION_SCHEMA.COLUMNS c
  WHERE c.COLUMN_NAME = 'VerificationCode' AND c.TABLE_SCHEMA = 'dbo' AND EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS e
  WHERE e.TABLE_NAME = c.TABLE_NAME AND e.TABLE_SCHEMA = 'dbo' AND e.COLUMN_NAME = 'Email')
  ORDER BY CASE WHEN c.TABLE_NAME = 'VerificationCode' THEN 0 ELSE 1 END);
IF @t IS NULL BEGIN SELECT 'NOTABLE'; RETURN; END
DECLARE @q nvarchar(max) = N'SELECT TOP 1 CAST(VerificationCode AS varchar(32)) FROM dbo.' + QUOTENAME(@t)
  + N' WHERE Email = @e';
IF COL_LENGTH('dbo.' + @t, 'ExpirationTime') IS NOT NULL SET @q += N' ORDER BY ExpirationTime DESC';
EXEC sp_executesql @q, N'@e varchar(255)', @e = '@@EMAIL@@';"""


def create_account(username, email, password, progress=None):
    """Offline sign-up through the server's own API. Returns (ok, message)."""
    email = email or f"{username}@offline.local"
    if not USERNAME_RE.match(username or "") or not EMAIL_RE.match(email):
        return False, "Invalid username or email."      # also keeps quotes out of the SQL below
    say = progress or (lambda m: None)
    say("Asking the server for a sign-up code…")
    r = _post("/launcher/SendVerificationEmailAction", {"email": email})
    # The API stores the code and then answers success:false/"EmailSent" (it compares against "Success"),
    # so "EmailSent" means the code is ready.
    if not r.get("success") and r.get("result") != "EmailSent":
        res = r.get("result") or r.get("message") or "unknown error"
        return False, RESULT_TEXT.get(res, f"The server refused the sign-up code: {res}")
    say("Reading the code from your server's database…")
    rows = sql(_CODE_QUERY.replace("@@EMAIL@@", email))
    code = rows[-1][0].strip() if rows and rows[-1] else ""
    if not code.isdigit():
        return False, "Couldn't read the sign-up code from the database."
    say("Creating the account…")
    r = _post("/launcher/SignupAction", {"username": username, "email": email, "password": password,
                                          "verificationCode": code})
    res = r.get("result") or r.get("message") or "unknown error"
    if r.get("success") and res == "AccountCreated":
        return True, f"Account '{username}' is ready. Use it on the game's login screen."
    return False, RESULT_TEXT.get(res, f"Sign-up failed: {res}")


# ------------------------------------------------------------- game client
CLIENT_CONF = os.path.join(ROOT, "runtime", "client.conf")
SKIP_DIRS = {".cache", "Trash", "node_modules", "compatdata", "shadercache", "proc", "sys", "dev", ".git",
             "__pycache__", "client-prefix", "pfx", "windows", "steamrt", "ubuntu12_32", "ubuntu12_64"}


def find_exe(folder):
    try:
        for e in os.scandir(folder):
            if e.is_file() and e.name.lower() == "rustyhearts.exe":
                return e.path
    except OSError:
        pass
    return None


def inspect_client(folder):
    """{'ok', 'exe', 'pck', 'problems'} for a candidate client folder."""
    info = {"ok": False, "exe": None, "pck": 0, "problems": []}
    if not folder or not os.path.isdir(folder):
        info["problems"].append("Folder not found")
        return info
    info["exe"] = find_exe(folder)
    try:
        info["pck"] = sum(1 for n in os.listdir(folder) if n.lower().endswith(".pck"))
    except OSError:
        pass
    if not info["exe"]:
        info["problems"].append("rustyhearts.exe is not in this folder")
    if info["pck"] == 0:
        info["problems"].append("No game data (.pck files) in this folder")
    info["ok"] = not info["problems"]
    return info


def has_d3dcompiler(folder):
    """The game needs Microsoft's D3DCompiler_43.dll next to it (Wine's own can't build its effects)."""
    try:
        return any(n.lower() == "d3dcompiler_43.dll" for n in os.listdir(folder))
    except OSError:
        return False


def load_client_dir():
    try:
        with open(CLIENT_CONF, encoding="utf-8") as f:
            for line in f:
                if line.startswith("CLIENT_DIR="):
                    v = line[11:].strip()
                    if v:
                        return v
    except OSError:
        pass
    bundled = os.path.join(ROOT, "gameclient")
    return bundled if os.path.isdir(bundled) else ""


def save_client_dir(folder):
    os.makedirs(os.path.dirname(CLIENT_CONF), exist_ok=True)
    with open(CLIENT_CONF, "w", encoding="utf-8") as f:
        f.write("CLIENT_DIR=%s\n" % folder)


def steam_libraries():
    """Steam library folders (each holds steamapps/)."""
    roots = [os.path.expanduser(p) for p in ("~/.local/share/Steam", "~/.steam/root", "~/.steam/steam",
                                              "~/.var/app/com.valvesoftware.Steam/.local/share/Steam")]
    libs = []
    for r in roots:
        vdf = os.path.join(r, "steamapps", "libraryfolders.vdf")
        if os.path.isdir(os.path.join(r, "steamapps")):
            libs.append(os.path.realpath(r))
        try:
            with open(vdf, encoding="utf-8", errors="replace") as f:
                libs += [os.path.realpath(m) for m in re.findall(r'"path"\s+"([^"]+)"', f.read())]
        except OSError:
            pass
    out = []
    for l in libs:
        if l not in out and os.path.isdir(l):
            out.append(l)
    return out


def search_roots():
    home = os.path.expanduser("~")
    user = os.environ.get("USER", "")
    roots = [(os.path.join(ROOT, "gameclient"), 1)]
    roots += [(os.path.join(lib, "steamapps", "common"), 2) for lib in steam_libraries()]
    for name in ("Games", "games", "Applications", "Downloads", "Desktop", "Documents"):
        roots.append((os.path.join(home, name), 4))
    roots.append((home, 3))
    for base in (f"/run/media/{user}", "/run/media", "/media", "/mnt"):
        roots.append((base, 4))
    return [(r, d) for r, d in roots if os.path.isdir(r)]


def find_clients(should_stop=lambda: False, progress=None, limit=20, max_seconds=60):
    """Folders that contain rustyhearts.exe + .pck files."""
    found, seen, t0 = [], set(), time.time()

    def walk(path, depth):
        if should_stop() or len(found) >= limit or time.time() - t0 > max_seconds:
            return
        real = os.path.realpath(path)
        if real in seen:
            return
        seen.add(real)
        if find_exe(path) and inspect_client(path)["ok"]:
            found.append(real)
            if progress:
                progress(f"Found: {real}")
        if depth <= 0:
            return
        try:
            entries = sorted(os.scandir(path), key=lambda e: e.name.lower())
        except OSError:
            return
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False) and e.name not in SKIP_DIRS and not e.name.startswith("."):
                    walk(e.path, depth - 1)
            except OSError:
                pass

    for root, depth in search_roots():
        if progress:
            progress(f"Searching {root}…")
        walk(root, depth)
    return found


def proton_check():
    """(proton path or '') as run-client.sh would pick it."""
    try:
        out = subprocess.run(["bash", os.path.join(ROOT, "tools", "run-client.sh"), "--check"],
                             capture_output=True, text=True, timeout=20).stdout
        m = re.search(r"^PROTON=(.*)$", out, re.M)
        return m.group(1).strip() if m else ""
    except Exception:
        return ""


# ------------------------------------------------------------- auto-login
AUTOLOGIN_CONF = os.path.join(ROOT, "runtime", "rhlauncher", "autologin.conf")
AUTOLOGIN_CRED = os.path.join(ROOT, "runtime", "rhlauncher", "autologin.cred")
AUTOLOGIN_DELAYS = [10, 15, 20, 30, 45, 60, 90]


def load_autologin():
    """{'enabled', 'delay', 'user', 'has_password'} - the password itself is never returned."""
    d = {"enabled": False, "delay": 20, "user": "", "has_password": False}
    try:
        with open(AUTOLOGIN_CONF, encoding="utf-8") as f:
            for line in f:
                k, _, v = line.strip().partition("=")
                if k == "ENABLED":
                    d["enabled"] = v == "1"
                elif k == "DELAY" and v.isdigit():
                    d["delay"] = int(v)
    except OSError:
        pass
    try:
        with open(AUTOLOGIN_CRED, encoding="utf-8") as f:
            lines = f.read().split("\n")
        d["user"] = lines[0].strip()
        d["has_password"] = len(lines) > 1 and bool(lines[1])
    except OSError:
        pass
    return d


def save_autologin(enabled, delay, user=None, password=None):
    """Save the settings; user/password None = keep the stored ones. The file is readable only by you."""
    os.makedirs(os.path.dirname(AUTOLOGIN_CONF), exist_ok=True)
    with open(AUTOLOGIN_CONF, "w", encoding="utf-8") as f:
        f.write("ENABLED=%d\nDELAY=%d\n" % (1 if enabled else 0, int(delay)))
    if user is None and password is None:
        return
    cur_user, cur_pw = "", ""
    try:
        with open(AUTOLOGIN_CRED, encoding="utf-8") as f:
            lines = f.read().split("\n")
        cur_user, cur_pw = lines[0], (lines[1] if len(lines) > 1 else "")
    except OSError:
        pass
    user = cur_user if user is None else user
    password = cur_pw if password is None else password
    tmp = AUTOLOGIN_CRED + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(user + "\n" + password + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, AUTOLOGIN_CRED)


def forget_autologin():
    save_autologin(False, load_autologin()["delay"])
    try:
        os.remove(AUTOLOGIN_CRED)
    except OSError:
        pass


# ------------------------------------------------- shared database password
ENV_FILE = os.path.join(ROOT, "docker", ".env")
SHARED_ENV = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "rh-launcher",
                          "sql.env")


def _read_password(path):
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("RH_SQL_PASSWORD=") and line.strip()[16:]:
                    return line.strip()[16:]
    except OSError:
        pass
    return None


def _password_works(pw):
    """True/False if the running SQL container accepts pw, None if it can't be tested."""
    try:
        r = subprocess.run(_docker() + ["exec", "-e", "SQLCMDPASSWORD", SQL_CONTAINER, "/opt/mssql-tools18/bin/sqlcmd",
                                        "-S", "127.0.0.1", "-U", "sa", "-C", "-b", "-Q", "SELECT 1"],
                           env=dict(os.environ, SQLCMDPASSWORD=pw), capture_output=True, text=True, timeout=30)
    except Exception:
        return None
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode == 0:
        return True
    return False if "Login failed" in out else None


def _other_env_files(progress=None, max_seconds=40):
    """docker/.env files of other RH Launcher folders (and the shared copy), newest first."""
    found, t0, me = [], time.time(), os.path.realpath(ROOT)
    if os.path.isfile(SHARED_ENV):
        found.append(SHARED_ENV)
    skip = {".cache", ".local", ".var", ".steam", "node_modules", "compatdata", "steamapps", "runtime", "pfx",
            "server-package", "gameclient", "vm", "downloads", ".git", "proc", "sys"}
    user = os.environ.get("USER", "")
    for top in [os.path.expanduser("~"), f"/run/media/{user}", "/mnt", "/media"]:
        if not os.path.isdir(top):
            continue
        if progress:
            progress(f"Looking in {top}…")
        for dirpath, dirnames, filenames in os.walk(top):
            if time.time() - t0 > max_seconds:
                break
            depth = dirpath[len(top):].count(os.sep)
            dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".") and depth < 5]
            if os.path.basename(dirpath) == "docker" and ".env" in filenames and "compose.yaml" in filenames:
                folder = os.path.realpath(os.path.dirname(dirpath))
                if folder != me:
                    found.append(os.path.join(dirpath, ".env"))
    return sorted(set(found), key=lambda p: -os.path.getmtime(p))


def adopt_password(progress=None):
    """Find the password the existing database was made with and use it in this folder. Returns a message."""
    say = progress or (lambda m: None)
    mine = _read_password(ENV_FILE)
    candidates = []
    for path in _other_env_files(say):
        pw = _read_password(path)
        if pw and pw != mine and pw not in [c[1] for c in candidates]:
            candidates.append((path, pw))
    if not candidates:
        raise RuntimeError("No other RH Launcher folder with a saved password was found. Copy docker/.env from "
                           "the folder you set the server up in into this folder's docker/ folder.")
    chosen = None
    say("Checking which password the database accepts…")
    for path, pw in candidates:
        ok = _password_works(pw)
        if ok:
            chosen = (path, pw)
            break
        if ok is None and chosen is None:
            chosen = (path, pw)          # can't test (database not running): newest guess
    if not chosen:
        raise RuntimeError("The database didn't accept any of the saved passwords found on this PC.")
    path, pw = chosen
    text = "RH_SQL_PASSWORD=%s\n" % pw
    for target in (ENV_FILE, SHARED_ENV):
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.exists(target) and target == ENV_FILE:
            shutil.copy2(target, target + ".old")
            os.chmod(target + ".old", 0o600)
        fd = os.open(target + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(target + ".tmp", target)
    where = "your saved password" if path == SHARED_ENV else os.path.dirname(os.path.dirname(path))
    return f"Using the database password from {where}."


# ------------------------------------------------------------ server memory
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("rh_memory_profile", os.path.join(ROOT, "docker", "memory_profile.py"))
_mp = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mp)
MEM_PROFILES, MEM_CHOICES = _mp.PROFILES, _mp.CHOICES


def memory_choice():
    return _mp.choice(ROOT)


def memory_resolve(name):
    return _mp.resolve(name)


def memory_save(name):
    _mp.save(ROOT, name)


# ------------------------------------------------------------ network play
_nspec = _ilu.spec_from_file_location("rh_network_conf", os.path.join(ROOT, "docker", "network_conf.py"))
_nc = _ilu.module_from_spec(_nspec)
_nspec.loader.exec_module(_nc)
NET_TCP_PORTS, NET_UDP_PORTS = _nc.CLIENT_TCP_PORTS, _nc.CLIENT_UDP_PORTS
IP_RE = re.compile(r"^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$")


def net_load():
    return _nc.load(ROOT)


def net_save(mode, host_ip_fixed="", join_ip=""):
    _nc.save(ROOT, mode, host_ip_fixed, join_ip)


def lan_ip():
    return _nc.lan_ip()


def test_server(ip):
    """(ok, message) for a server on another PC."""
    if not IP_RE.match(ip or ""):
        return False, "That doesn't look like an address (example: 192.168.1.20)."
    if not port_open(3000, 3, ip):
        return False, (f"Nothing answers on {ip}. On the PC: Settings > Network play > Host, turn the server on, "
                       "and open the firewall ports.")
    missing = [p for p in (50001, 50006, 50016) if not port_open(p, 3, ip)]
    if missing:
        return False, (f"The login server on {ip} answers, but the game servers don't yet (ports "
                       f"{', '.join(map(str, missing))}). Wait until the PC says the server is online, or check its "
                       "firewall.")
    return True, f"The server on {ip} is online."


def firewall_state():
    """('none' | 'open' | 'closed', detail) for firewalld (Bazzite / Fedora)."""
    fc = shutil.which("firewall-cmd")
    if not fc:
        return "none", "No firewall tool found (nothing to open)"
    try:
        if subprocess.run([fc, "--state"], capture_output=True, timeout=10).returncode:
            return "none", "The firewall isn't running"
        closed = []
        for p in NET_TCP_PORTS:
            if subprocess.run([fc, "--query-port", f"{p}/tcp"], capture_output=True, timeout=10).returncode:
                closed.append(f"{p}/tcp")
        for p in NET_UDP_PORTS:
            if subprocess.run([fc, "--query-port", f"{p}/udp"], capture_output=True, timeout=10).returncode:
                closed.append(f"{p}/udp")
    except Exception as e:
        return "closed", f"Couldn't check the firewall: {e}"
    return ("open", "Game ports are open") if not closed else ("closed", f"{len(closed)} game ports are closed")


def open_firewall():
    """Open the game ports in firewalld (asks for your password through the system prompt)."""
    fc = shutil.which("firewall-cmd")
    pk = shutil.which("pkexec")
    if not fc:
        return "No firewall tool found, so nothing needs opening."
    if not pk:
        raise RuntimeError("pkexec is missing. Run in a terminal: sudo firewall-cmd --permanent " +
                           " ".join(f"--add-port={p}/tcp" for p in NET_TCP_PORTS) + " " +
                           " ".join(f"--add-port={p}/udp" for p in NET_UDP_PORTS) + " && sudo firewall-cmd --reload")
    args = [f"--add-port={p}/tcp" for p in NET_TCP_PORTS] + [f"--add-port={p}/udp" for p in NET_UDP_PORTS]
    r = subprocess.run([pk, fc, "--permanent"] + args, capture_output=True, text=True, timeout=300)
    if r.returncode:
        raise RuntimeError("The firewall change was cancelled or failed: " + (r.stderr or r.stdout).strip()[-200:])
    r = subprocess.run([pk, fc] + args, capture_output=True, text=True, timeout=300)   # live, without a reload
    if r.returncode:
        raise RuntimeError("Saved, but couldn't apply it right away: " + (r.stderr or r.stdout).strip()[-200:])
    return "Firewall ports opened."
