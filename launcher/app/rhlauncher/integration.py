"""Desktop menu entry and Add to Steam."""
import os
import shutil
import subprocess

from .config import LAUNCHER_DIR, ASSETS_DIR

DESKTOP_FILE = os.path.expanduser("~/.local/share/applications/rhlauncher.desktop")
LAUNCH_SCRIPT = os.path.join(LAUNCHER_DIR, "RHLauncher.sh")


def write_desktop_file():
    os.makedirs(os.path.dirname(DESKTOP_FILE), exist_ok=True)
    with open(DESKTOP_FILE, "w", encoding="utf-8") as f:
        f.write("[Desktop Entry]\nType=Application\nName=RH Launcher\n"
                "Comment=Rusty Hearts Offline: server and game\n"
                f"Exec=\"{LAUNCH_SCRIPT}\"\nPath={LAUNCHER_DIR}\n"
                f"Icon={os.path.join(ASSETS_DIR, 'icon.png')}\nTerminal=false\nCategories=Game;\n")
    os.chmod(DESKTOP_FILE, 0o755)
    return DESKTOP_FILE


def add_to_steam():
    desktop = write_desktop_file()
    tool = shutil.which("steamos-add-to-steam")
    if tool:
        try:
            subprocess.Popen([tool, desktop], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
            return True, "Sent to Steam. Find 'RH Launcher' in your Library under Non-Steam (Steam must be running)."
        except OSError as e:
            return False, f"steamos-add-to-steam failed: {e}"
    return False, ("Created a menu entry. To add it to Steam: Steam → Games → 'Add a Non-Steam Game to My "
                   f"Library…' → pick 'RH Launcher' (or browse to {LAUNCH_SCRIPT}).")


def open_folder(path):
    for tool in ("xdg-open", "kde-open", "gio"):
        if shutil.which(tool):
            args = [tool, "open", path] if tool == "gio" else [tool, path]
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return True
    return False
