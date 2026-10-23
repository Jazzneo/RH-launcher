"""Paths and persistent launcher settings."""
import json
import os
import threading

APP_NAME = "RHLauncher"
APP_VERSION = "1.0.1"

# Rusty heart/launcher/app/rhlauncher/config.py -> Rusty heart/
LAUNCHER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ROOT = os.path.dirname(LAUNCHER_DIR)
ASSETS_DIR = os.path.join(LAUNCHER_DIR, "assets")
DATA_DIR = os.path.join(ROOT, "runtime", "rhlauncher")
LOG_DIR = os.path.join(ROOT, "logs")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
DISPLAY_CONF = os.path.join(ROOT, "runtime", "client-display.conf")
SERVER_SCRIPT = os.path.join(ROOT, "docker", "launcher.sh")

DEFAULTS = {
    "stop_server_after_game": False,   # turn the server off when the game closes
    "stop_server_on_exit": True,       # turn the server off when the launcher quits
    "fullscreen": None,                # None = auto (fullscreen in Steam Gaming Mode)
}


class Settings:
    def __init__(self):
        self._lock = threading.RLock()
        self.data = dict(DEFAULTS)
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                stored = json.load(f)
            if isinstance(stored, dict):
                self.data.update(stored)
        except FileNotFoundError:
            pass
        except Exception as e:
            print("[settings] unreadable, using defaults:", e)

    def get(self, key, default=None):
        with self._lock:
            return self.data.get(key, DEFAULTS.get(key, default))

    def set(self, key, value):
        with self._lock:
            self.data[key] = value
            os.makedirs(DATA_DIR, exist_ok=True)
            tmp = SETTINGS_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp, SETTINGS_FILE)


# ------------------------------------------------------------ display.conf
DISPLAY_DEFAULTS = {"MODE": "windowed", "SIZE": "1280x720", "BACKEND": "wayland", "RENDERER": "opengl"}


def load_display():
    d = dict(DISPLAY_DEFAULTS)
    try:
        with open(DISPLAY_CONF, encoding="utf-8") as f:
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    if k in d:
                        d[k] = v
    except FileNotFoundError:
        pass
    if d["MODE"] not in ("windowed", "borderless", "fullscreen"):
        d["MODE"] = "windowed"
    if d["BACKEND"] not in ("wayland", "x11"):
        d["BACKEND"] = "wayland"
    if d["RENDERER"] not in ("vulkan", "opengl"):
        d["RENDERER"] = "opengl"
    return d


def save_display(d):
    os.makedirs(os.path.dirname(DISPLAY_CONF), exist_ok=True)
    with open(DISPLAY_CONF, "w", encoding="utf-8") as f:
        f.write("MODE=%s\nSIZE=%s\nBACKEND=%s\nRENDERER=%s\n" % (d["MODE"], d["SIZE"], d["BACKEND"], d["RENDERER"]))


def in_gaming_mode():
    env = os.environ
    return bool(env.get("SteamGamepadUI") or env.get("GAMESCOPE_WAYLAND_DISPLAY")
                or env.get("XDG_CURRENT_DESKTOP", "").lower() == "gamescope")
