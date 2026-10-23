import argparse
import fcntl
import os
import signal
import sys

from .app import App
from .config import APP_VERSION, DATA_DIR

_lock = None


def single_instance():
    global _lock
    os.makedirs(DATA_DIR, exist_ok=True)
    _lock = open(os.path.join(DATA_DIR, "launcher.lock"), "w")
    try:
        fcntl.flock(_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def main():
    ap = argparse.ArgumentParser(prog="RHLauncher", description="Rusty Hearts Offline launcher")
    ap.add_argument("--fullscreen", action="store_true")
    ap.add_argument("--windowed", action="store_true")
    ap.add_argument("--version", action="version", version=APP_VERSION)
    a = ap.parse_args()
    if not single_instance():
        print("RH Launcher is already running.", file=sys.stderr)
        return 1
    app = App()

    def on_term(_s, _f):
        app.running = False
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, on_term)
    fs = True if a.fullscreen else False if a.windowed else None
    try:
        app.run(fs)
    except KeyboardInterrupt:
        pass
    finally:
        # Steam "Exit game" / logout: stop the server if the user wants that
        if not app.quitting and app.settings.get("stop_server_on_exit") and (app.server.running or app.server.ready):
            from . import backend
            print("Stopping server…")
            backend.Job(["server-stop"]).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
