"""The launcher's pages: Play, Setup, Accounts, Server, Game Client, Display, Settings."""
import os
import threading
import time

import pygame

from . import ui, backend, integration, installer
from .ui import C, text, rrect, panel
from .app import CONTENT, Progress
from .config import load_display, save_display, LOG_DIR, in_gaming_mode

X0, Y0, CW, CH = CONTENT.x, CONTENT.y, CONTENT.w, CONTENT.h

MODE_LABELS = {"windowed": "Windowed", "borderless": "Borderless", "fullscreen": "Fullscreen"}
BACKEND_LABELS = {"wayland": "Proton native Wayland", "x11": "X11 + gamescope"}


def check_row(surf, x, y, ok, label, detail, w=420):
    col = C["green"] if ok is True else C["yellow"] if ok is None else C["red"]
    pygame.draw.circle(surf, col, (x + 11, y + 13), 11)
    text(surf, "✓" if ok else "!", (x + 11, y + 14), 14, C["bg"], True, "center")
    text(surf, label, (x + 32, y + 2), 18, C["text"], True, max_w=w - 40)
    text(surf, detail, (x + 32, y + 26), 15, C["muted"], max_w=w - 40)


class Screen:
    name = title = ""
    default_focus = None

    def __init__(self, app):
        self.app = app
        self.last_focus = None

    @property
    def s(self):
        return self.app.settings

    def build(self):
        return []

    def draw(self, surf):
        pass

    def on_button(self, b):
        pass

    def badge(self):
        return None


def display_summary():
    d = load_display()
    size = "screen size" if d["MODE"] == "borderless" else d["SIZE"]
    return MODE_LABELS[d["MODE"]], f"{size} · {BACKEND_LABELS[d['BACKEND']]}"


# ===================================================================== PLAY
class PlayScreen(Screen):
    name, title = "play", "Play"
    default_focus = "play"

    def build(self):
        app = self.app
        ws = []
        hero = pygame.Rect(X0, Y0, CW, 236)
        if app.game_running:
            ws.append(ui.Button("play", (hero.right - 330, hero.y + 70, 300, 96), "STOP GAME", app.stop_game,
                                "danger", sub="Game is running", size=28))
        else:
            busy = app.server.busy is not None
            ws.append(ui.Button("play", (hero.right - 330, hero.y + 70, 300, 96), "PLAY", app.play, "primary",
                                sub=("Server is online" if app.server.ready else "Starts server + game"),
                                size=34, enabled=not busy))
        cw = (CW - 32) // 3
        cy = Y0 + 236 + 18
        srv = app.server
        on = bool(srv.running or srv.ready)
        ws.append(ui.Button("card_server", (X0 + 20, cy + 150, cw - 40, 52),
                            "Turn server off" if on else "Turn server on", app.toggle_server,
                            "danger" if on else "success", size=19, enabled=srv.busy is None))
        ws.append(ui.Button("card_display", (X0 + cw + 16 + 20, cy + 150, cw - 40, 52), "Change display",
                            lambda: app.go("display"), "ghost", size=19))
        ws.append(ui.Button("card_logs", (X0 + 2 * (cw + 16) + 20, cy + 150, cw - 40, 52), "Server details",
                            lambda: app.go("server"), "ghost", size=19))
        return ws

    def draw(self, surf):
        app = self.app
        hero = pygame.Rect(X0, Y0, CW, 236)
        panel(surf, hero, C["panel"], 20)
        # decorative crimson glow + gold rule
        glow = pygame.Surface(hero.size, pygame.SRCALPHA)
        pygame.draw.circle(glow, (176, 28, 44, 40), (hero.w - 180, 118), 220)
        surf.blit(glow, hero)
        text(surf, "Rusty Hearts", (hero.x + 36, hero.y + 34), 44, C["text"], True)
        pygame.draw.line(surf, C["gold"], (hero.x + 38, hero.y + 94), (hero.x + 300, hero.y + 94), 2)
        text(surf, "Offline server · world Ragezone", (hero.x + 36, hero.y + 108), 20, C["muted"])
        st = app.server.state
        msg = {"Online": "Everything is running. Press PLAY or START.",
               "Offline": "Press PLAY: the server starts first (about a minute), then the game.",
               "Starting": "Starting the server…", "Stopping": "Stopping the server…",
               "Not ready": "The server is starting up or only partly running."}.get(st, "")
        ui.text_block(surf, msg, (hero.x + 36, hero.y + 150), hero.w - 420, 18, C["muted"])

        cw = (CW - 32) // 3
        cy = Y0 + 236 + 18
        cards = [("Server", st, {"Online": C["green"], "Offline": C["dim"]}.get(st, C["yellow"]),
                  "Database, login and game servers" if st == "Online" else
                  "Turn it on here or just press PLAY"),
                 ("Display",) + (display_summary()[0], C["blue"], display_summary()[1]),
                 ("Game", "Running" if app.game_running else "Not running",
                  C["accent"] if app.game_running else C["dim"],
                  "Close the game window to stop" if app.game_running else "Starts with PLAY")]
        for i, (title, value, col, sub) in enumerate(cards):
            r = pygame.Rect(X0 + i * (cw + 16), cy, cw, 222)
            panel(surf, r, C["panel"], 18)
            text(surf, title, (r.x + 22, r.y + 20), 16, C["muted"], True)
            ui.status_dot(surf, col, (r.x + 30, r.y + 70), 7, value in ("Starting", "Stopping"))
            text(surf, value, (r.x + 48, r.y + 70), 23, C["text"], True, "midleft", r.w - 70)
            ui.text_block(surf, sub, (r.x + 22, r.y + 98), r.w - 44, 15, C["dim"])

    def on_button(self, b):
        if b == "y":
            self.app.toggle_server()


# =================================================================== SERVER
class ServerScreen(Screen):
    name, title = "server", "Server"
    default_focus = "srv_toggle"

    def __init__(self, app):
        super().__init__(app)
        self.log_i = 0

    def badge(self):
        st = self.app.server.state
        return C["green"] if st == "Online" else C["yellow"] if st in ("Starting", "Stopping", "Not ready") else None

    def restart(self):
        self.app.stop_server(then=lambda: self.app.start_server())

    def stop_other(self):
        app = self.app

        def done(res, err):
            app.server.refresh_soon()
            if err:
                app.dialog(f"Couldn't turn off the {backend.OTHER_ENGINE} server", str(err))
            else:
                app.log(res)
                app.toast(res, "ok")
        app.run_task(f"Turning off the {backend.OTHER_ENGINE} server", backend.stop_other_engine, done)

    def build(self):
        app, srv = self.app, self.app.server
        on = bool(srv.running or srv.ready)
        idle = srv.busy is None
        ws = [ui.Button("srv_toggle", (X0, Y0, 260, 64), "Turn server off" if on else "Turn server on",
                        app.toggle_server, "danger" if on else "success", size=21, enabled=idle),
              ui.Button("srv_restart", (X0 + 276, Y0, 180, 64), "Restart", self.restart, "ghost",
                        enabled=idle and on),
              ui.Button("srv_logs", (X0 + 472, Y0, 220, 64), "Open logs folder",
                        lambda: integration.open_folder(LOG_DIR) or app.toast("No file manager found", "warn"),
                        "ghost")]
        if srv.other:
            ws.append(ui.Button("srv_other", (X0 + 708, Y0, CW - 708, 64), f"Turn off {backend.OTHER_ENGINE} server",
                                self.stop_other, "danger", size=18, enabled=idle))
        items = list(reversed(app.activity)) or []
        self.log_i = min(self.log_i, max(0, len(items) - 1))
        ws.append(ui.ListView("srv_activity", (X0 + 470, Y0 + 96, CW - 470, CH - 104), items, self.log_i,
                              lambda i: setattr(self, "log_i", i), None, 34,
                              lambda s, r, it, sel, f: text(s, it, (r.x + 14, r.centery), 15,
                                                            C["text"] if sel else C["muted"], False, "midleft",
                                                            r.w - 24),
                              "No activity yet"))
        return ws

    def draw(self, surf):
        srv = self.app.server
        box = pygame.Rect(X0, Y0 + 96, 450, CH - 104)
        panel(surf, box, C["panel"], 16)
        text(surf, "Status", (box.x + 20, box.y + 16), 16, C["muted"], True)
        y = box.y + 50
        if not srv.docker_ok:
            check_row(surf, box.x + 20, y, None, "Docker", "Not reachable yet (checking…)", 420)
            y += 62
        for key, label in backend.SERVICES:
            ok = key in srv.running
            check_row(surf, box.x + 20, y, True if ok else (None if srv.busy else False), label,
                      "Running" if ok else "Stopped", 420)
            y += 58
        for name, port in backend.PORTS.items():
            ok = srv.ports.get(name)
            check_row(surf, box.x + 20, y, True if ok else (None if srv.running else False),
                      f"{name} (port {port})", "Answering" if ok else "Not answering", 420)
            y += 58
        if srv.checked:
            text(surf, f"Checked {int(time.time() - srv.checked)}s ago", (box.x + 20, box.bottom - 30), 14, C["dim"])
        text(surf, "Activity", (X0 + 470, Y0 + 76), 15, C["muted"], True)


# ================================================================== DISPLAY
class DisplayScreen(Screen):
    name, title = "display", "Display"
    default_focus = "d_backend"

    def __init__(self, app):
        super().__init__(app)
        self._sizes = {}

    def sizes(self, be):
        if be not in self._sizes:
            self._sizes[be] = backend.screen_sizes(be)
        return self._sizes[be]

    def build(self):
        d = load_display()
        be_keys = ["wayland", "x11"]
        mode_keys = ["windowed", "borderless", "fullscreen"]
        native, choices = self.sizes(d["BACKEND"])
        sizes = [native] + [c for c in choices if c != native]
        if d["SIZE"] not in sizes:
            sizes.append(d["SIZE"])

        def set_(k, v):
            d[k] = v
            save_display(d)

        ws = [ui.Selector("d_backend", (X0, Y0, CW, 76), "Graphics",
                          [BACKEND_LABELS[k] + (" (recommended)" if k == "wayland" else "") for k in be_keys],
                          be_keys.index(d["BACKEND"]), lambda i: set_("BACKEND", be_keys[i])),
              ui.Selector("d_render", (X0, Y0 + 92, CW, 76), "Renderer",
                          ["Vulkan (DXVK) - faster, try first", "OpenGL (WineD3D) - safest"],
                          0 if d["RENDERER"] == "vulkan" else 1,
                          lambda i: set_("RENDERER", "vulkan" if i == 0 else "opengl")),
              ui.Selector("d_mode", (X0, Y0 + 184, CW, 76), "Display mode", [MODE_LABELS[k] for k in mode_keys],
                          mode_keys.index(d["MODE"]), lambda i: set_("MODE", mode_keys[i]))]
        if d["MODE"] == "borderless":
            ws.append(ui.Selector("d_size", (X0, Y0 + 276, CW, 76), "Resolution",
                                  [f"{native} (your screen, used for Borderless)"], 0, lambda i: None, enabled=False))
        else:
            labels = [s + ("  (your screen)" if s == native else "") for s in sizes]
            ws.append(ui.Selector("d_size", (X0, Y0 + 276, CW, 76), "Resolution", labels, sizes.index(d["SIZE"]),
                                  lambda i: set_("SIZE", sizes[i])))
        return ws

    def draw(self, surf):
        d = load_display()
        info = pygame.Rect(X0, Y0 + 368, CW, CH - 376)
        panel(surf, info, C["panel"], 16)
        tips = {
            "wayland": "Proton native Wayland runs the game without XWayland, so your desktop's display scaling "
                       "can't confuse the mouse. If the game doesn't start this way, the launcher falls back to "
                       "X11 automatically.",
            "x11": "X11 + gamescope runs the game in its own small display at exactly this resolution. "
                   + ("gamescope was found." if backend.gamescope_available() else
                      "gamescope was NOT found, so a plain Proton window is used."),
        }
        modes = {"windowed": "A normal window at the size you pick.",
                 "borderless": "Fills your screen at its own resolution, no border.",
                 "fullscreen": "Runs at the size you pick and is scaled to fill the screen. Smaller sizes run faster."}
        y = ui.text_block(surf, tips[d["BACKEND"]], (info.x + 24, info.y + 22), info.w - 48, 18, C["muted"])
        y = ui.text_block(surf, modes[d["MODE"]], (info.x + 24, y + 14), info.w - 48, 18, C["text"])
        if d["RENDERER"] == "vulkan":
            y = ui.text_block(surf, "Vulkan: if the game fails to start with it, the launcher retries with OpenGL "
                                    "by itself. A white screen or crash later on? Switch to OpenGL here.",
                              (info.x + 24, y + 14), info.w - 48, 16, C["gold"])
        if in_gaming_mode():
            y = ui.text_block(surf, "In Steam Gaming Mode the game always fills the screen.", (info.x + 24, y + 14),
                              info.w - 48, 17, C["gold"])
        ui.text_block(surf, "Changes apply the next time the game starts.", (info.x + 24, y + 14), info.w - 48, 16,
                      C["dim"])


# ================================================================= SETTINGS
class SettingsScreen(Screen):
    name, title = "settings", "Settings"
    default_focus = "set_after_game"

    def build(self):
        s, app = self.s, self.app
        fs_opts = [None, True, False]
        fs_lbl = ["Automatic (fullscreen in Gaming Mode)", "Always fullscreen", "Always in a window"]
        cur = fs_opts.index(s.get("fullscreen")) if s.get("fullscreen") in fs_opts else 0

        def add_steam():
            ok, msg = integration.add_to_steam()
            app.dialog("Added to Steam" if ok else "Add to Steam", msg)

        def menu():
            integration.write_desktop_file()
            app.toast("Added 'RH Launcher' to your applications menu", "ok")

        return [
            ui.Toggle("set_after_game", (X0, Y0, CW, 72), "Turn the server off when the game closes",
                      s.get("stop_server_after_game"), lambda v: s.set("stop_server_after_game", v),
                      sub="Off: the server stays on, so the next PLAY is instant"),
            ui.Toggle("set_on_exit", (X0, Y0 + 86, CW, 72), "Turn the server off when the launcher quits",
                      s.get("stop_server_on_exit"), lambda v: s.set("stop_server_on_exit", v),
                      sub="Recommended: the server uses a lot of memory"),
            ui.Selector("set_fs", (X0, Y0 + 172, CW, 76), "Launcher window (applies next start)", fs_lbl, cur,
                        lambda i: s.set("fullscreen", fs_opts[i])),
            ui.Button("set_steam", (X0, Y0 + 270, 300, 64), "Add to Steam", add_steam, "primary", size=21),
            ui.Button("set_menu", (X0 + 316, Y0 + 270, 320, 64), "Add to applications menu", menu, "ghost", size=20),
            ui.Button("set_autologin", (X0 + 652, Y0 + 270, 300, 64), "Auto-login…",
                      lambda: app.open_modal(AutoLoginDialog(app)), "ghost", size=20,
                      sub=("On" if backend.load_autologin()["enabled"] else "Off")),
        ]

    def draw(self, surf):
        r = pygame.Rect(X0, Y0 + 356, CW, CH - 364)
        panel(surf, r, C["panel"], 16)
        ui.text_block(surf, "Controls: D-pad / stick to move, A to select, B to go back, LB/RB to switch tabs, "
                            "START to play, SELECT to quit. Mouse, touch and keyboard work too.",
                      (r.x + 24, r.y + 20), r.w - 48, 17, C["muted"])


# ================================================================= ACCOUNTS
class AccountsScreen(Screen):
    name, title = "accounts", "Accounts"
    default_focus = "acc_user"

    def __init__(self, app):
        super().__init__(app)
        self.f = {"user": "", "email": "", "pw": "", "pw2": ""}
        self.accounts, self.loaded_at, self.loading, self.load_error = [], 0, False, None
        self.sel = 0
        self.zen_i, self.zen_busy = 1, False

    # background refresh of the account list (needs the database container)
    def reload(self):
        if self.loading:
            return
        self.loading = True

        def work():
            try:
                accs, err = backend.list_accounts(), None
            except Exception as e:
                accs, err = None, str(e)

            def done():
                self.loading, self.loaded_at = False, time.time()
                if accs is not None:
                    self.accounts, self.load_error = accs, None
                else:
                    self.load_error = err
            self.app.call_soon(done)
        import threading
        threading.Thread(target=work, daemon=True).start()

    def create(self):
        app = self.app
        u, e = self.f["user"].strip().lower(), self.f["email"].strip()
        problem = backend.validate_account(u, e, self.f["pw"], self.f["pw2"])
        if problem:
            app.dialog("Check the form", problem)
            return

        def go():
            pw = self.f["pw"]

            def done(res, err):
                if err:
                    app.dialog("Couldn't create the account", str(err))
                    return
                ok, msg = res
                if ok:
                    self.f.update({"user": "", "email": "", "pw": "", "pw2": ""})
                    app.log(f"Account '{u}' created")
                    app.dialog("Account created", msg + "\n\nUsername: " + u)
                    self.reload()
                else:
                    app.dialog("Couldn't create the account", msg)
            app.run_task("Creating account", lambda p: backend.create_account(u, e, pw, p), done)

        if app.server.ready:
            go()
        else:
            app.dialog("Turn the server on?", "Accounts are stored by the server, so it has to be running. "
                       "Turn it on now (about a minute)?",
                       [("Cancel", None, "ghost"), ("Turn on & create", lambda: app.start_server(then=go), "primary")])

    def build(self):
        app = self.app
        if app.server.ready and not self.loading and time.time() - self.loaded_at > 30:
            self.reload()
        fw = 460

        def setter(k):
            return lambda v: self.f.__setitem__(k, v)
        ws = [ui.TextField("acc_user", (X0, Y0 + 36, fw, 70), "Username (6-16 letters/numbers)", self.f["user"],
                           lambda v: self.f.__setitem__("user", v.strip().lower()), placeholder="e.g. player01"),
              ui.TextField("acc_email", (X0, Y0 + 120, fw, 70), "Email (optional)", self.f["email"],
                           setter("email"), placeholder="left empty: username@offline.local"),
              ui.TextField("acc_pw", (X0, Y0 + 204, fw, 70), "Password (8-16 characters)", self.f["pw"],
                           setter("pw"), password=True, placeholder="choose a password"),
              ui.TextField("acc_pw2", (X0, Y0 + 288, fw, 70), "Confirm password", self.f["pw2"], setter("pw2"),
                           password=True, placeholder="type it again", on_submit=self.create),
              ui.Button("acc_create", (X0, Y0 + 382, fw, 64), "Create account", self.create, "primary", size=22,
                        enabled=app.server.busy is None),
              ui.Button("acc_refresh", (X0 + fw + 24, Y0 + 382, (CW - fw - 24) // 2 - 8, 64), "Refresh list",
                        self.reload, "ghost", size=20, enabled=app.server.ready and not self.loading)]
        items = self.accounts

        def render(surf, r, a, sel, focused):
            text(surf, a["username"], (r.x + 18, r.y + 10), 19, C["text"] if sel else C["muted"], True,
                 max_w=r.w - 200)
            text(surf, a["email"], (r.x + 18, r.y + 36), 14, C["dim"], max_w=r.w - 200)
            text(surf, f"{a.get('zen', 0):,} Zen", (r.right - 16, r.y + 12), 16, C["gold"], True, anchor="topright")
            text(surf, a["created"], (r.right - 16, r.y + 38), 13, C["dim"], anchor="topright")
        empty = ("Turn the server on to see your accounts" if not app.server.ready else
                 "Loading…" if self.loading else (self.load_error or "No accounts yet"))
        lx, lw = X0 + fw + 24, CW - fw - 24
        ws.append(ui.ListView("acc_list", (lx, Y0 + 36, lw, 240), items,
                              self.sel, lambda i: setattr(self, "sel", i), None, 64, render, empty))
        amounts = [1000, 10000, 100000, 1000000]
        ws.append(ui.Selector("acc_zen_amt", (lx, Y0 + 290, lw // 2 - 8, 76), "Zen to add",
                              [f"{a:,}" for a in amounts], self.zen_i, lambda i: setattr(self, "zen_i", i)))
        target = items[self.sel]["username"] if items else None
        ws.append(ui.Button("acc_zen_add", (lx + lw // 2 + 8, Y0 + 290, lw // 2 - 8, 76), "Add Zen",
                            lambda: self.add_zen(target, amounts[self.zen_i]), "success", size=21,
                            sub=(f"to {target}" if target else "select an account"),
                            enabled=bool(target) and app.server.busy is None and not self.zen_busy))
        ws.append(ui.Button("acc_delete", (lx + lw // 2 + 8, Y0 + 382, lw // 2 - 8, 64), "Delete account",
                            lambda: self.delete(target), "danger", size=20,
                            enabled=bool(target) and app.server.busy is None and not self.zen_busy))
        return ws

    def delete(self, username):
        app = self.app
        if not username:
            return

        def go():
            def done(res, err):
                if err:
                    app.dialog("Couldn't delete the account", str(err))
                    return
                app.log(f"Account '{username}' deleted (backup {res})")
                app.toast(f"Account '{username}' deleted", "ok", 6)
                self.sel = 0
                self.reload()
            app.run_task("Deleting account", lambda p: backend.delete_account(username), done)

        def confirm():
            msg = (f"Delete the account '{username}'?\n\nIts login and Zen are removed, and nobody can log in "
                   "with it any more. Its characters are not deleted, but they can't be played.\n\n"
                   "A backup of the account database is saved first.")
            if app.game_running:
                msg += "\n\nIf this account is logged in right now, close the game first."
            app.dialog("Delete account?", msg, [("Delete", go, "danger"), ("Cancel", None, "ghost")])
        if app.server.ready or "sql" in app.server.running:
            confirm()
        else:
            app.dialog("Turn the server on?", "Accounts are stored by the server's database, so it has to be "
                       "running.", [("Cancel", None, "ghost"), ("Turn on", lambda: app.start_server(), "primary")])

    def add_zen(self, username, amount):
        app = self.app
        if not username:
            return
        self.zen_busy = True

        def done(res, err):
            self.zen_busy = False
            if err:
                app.dialog("Couldn't add Zen", str(err))
                return
            app.log(f"Added {amount:,} Zen to '{username}'")
            app.toast(f"+{amount:,} Zen for {username}" + (f" (now {res:,})" if res is not None else ""), "ok", 6)
            self.reload()

        def go():
            app.run_task("Adding Zen", lambda p: backend.add_zen(username, amount), done)
        if app.server.ready or "sql" in app.server.running:
            go()
        else:
            self.zen_busy = False
            app.dialog("Turn the server on?", "Zen is stored by the server's database, so it has to be running.",
                       [("Cancel", None, "ghost"), ("Turn on", lambda: app.start_server(), "primary")])

    def draw(self, surf):
        text(surf, "Create an account", (X0, Y0 + 4), 17, C["muted"], True)
        text(surf, f"Accounts on this server ({len(self.accounts)})", (X0 + 484, Y0 + 4), 17, C["muted"], True)
        r = pygame.Rect(X0, Y0 + 462, CW, CH - 470)
        panel(surf, r, C["panel"], 16)
        ui.text_block(surf, "Log in to the game with the username and password. Each account has its own "
                            "characters. The password only goes to your own server and isn't saved by the launcher. "
                            "Zen is shared by all characters of an account; reopen the in-game shop to see a new "
                            "balance.",
                      (r.x + 22, r.y + 16), r.w - 44, 16, C["muted"])


# =================================================================== CLIENT
class ClientScreen(Screen):
    name, title = "client", "Game Client"
    default_focus = "cl_find"

    def __init__(self, app):
        super().__init__(app)
        self.found, self.sel, self.proton, self.proton_at = [], 0, None, 0

    def badge(self):
        return None if self.app.client_ok() else C["yellow"]

    def use(self, folder):
        info = backend.inspect_client(folder)
        if not info["ok"]:
            # picked a parent folder? look one level down
            try:
                for e in os.scandir(folder):
                    if e.is_dir() and backend.inspect_client(e.path)["ok"]:
                        folder, info = e.path, backend.inspect_client(e.path)
                        break
            except OSError:
                pass
        if not info["ok"]:
            self.app.dialog("That's not the game folder", "\n".join(info["problems"]) +
                            "\n\nPick the folder that contains rustyhearts.exe and the .pck files.")
            return
        backend.save_client_dir(folder)
        self.app._client_cache = None
        self.app.toast("Game folder set", "ok")

    def find(self):
        app = self.app

        def done(res, err):
            if err:
                app.dialog("Search failed", str(err))
                return
            self.found, self.sel = res, 0
            if not res:
                app.dialog("Game not found", "Rusty Hearts wasn't found in your home folder, Steam libraries or "
                           "SD card / USB drives. Use Browse to pick its folder (the one with rustyhearts.exe).")
            elif len(res) == 1:
                self.use(res[0])
            else:
                app.toast(f"Found {len(res)} copies: pick one in the list", "info", 6)
        app.run_task("Looking for Rusty Hearts", lambda p: backend.find_clients(progress=p), done)

    def build(self):
        app = self.app
        cur = backend.load_client_dir()
        start = cur if cur and os.path.isdir(cur) else os.path.expanduser("~")
        ws = [ui.Button("cl_find", (X0, Y0 + 196, 420, 64), "Find my game automatically", self.find, "primary",
                        size=21),
              ui.Button("cl_browse", (X0 + 436, Y0 + 196, 220, 64), "Browse…",
                        lambda: app.browse("Pick your Rusty Hearts folder", start, self.use), "ghost", size=21)]

        def render(surf, r, item, sel, focused):
            text(surf, item, (r.x + 18, r.centery), 17, C["text"] if sel else C["muted"], sel, "midleft", r.w - 36)
        ws.append(ui.ListView("cl_found", (X0, Y0 + 312, CW, CH - 320), self.found, self.sel,
                              lambda i: setattr(self, "sel", i), lambda i: self.use(self.found[i]), 52, render,
                              "Press 'Find my game automatically' to search"))
        return ws

    def draw(self, surf):
        cur = backend.load_client_dir()
        info = backend.inspect_client(cur) if cur else None
        box = pygame.Rect(X0, Y0, CW, 180)
        panel(surf, box, C["panel"], 16)
        text(surf, "Game folder", (box.x + 22, box.y + 16), 16, C["muted"], True)
        text(surf, cur or "Not set yet", (box.x + 22, box.y + 42), 19, C["text"], True, max_w=box.w - 44)
        if info:
            check_row(surf, box.x + 22, box.y + 80, info["ok"], "Rusty Hearts",
                      f"rustyhearts.exe found, {info['pck']} data files" if info["ok"] else info["problems"][0],
                      box.w // 2 - 30)
        else:
            check_row(surf, box.x + 22, box.y + 80, None, "Rusty Hearts", "Find it or browse to it below",
                      box.w // 2 - 30)
        if time.time() - self.proton_at > 30:
            self.proton_at = time.time()
            import threading
            threading.Thread(target=lambda: setattr(self, "proton", backend.proton_check()), daemon=True).start()
        p = self.proton
        check_row(surf, box.x + box.w // 2, box.y + 80, True if p else (None if p is None else False), "Proton",
                  (os.path.basename(os.path.dirname(p)) if p else "Checking…" if p is None else
                   "Not found: install Proton Experimental from Steam (Library > Tools)"), box.w // 2 - 22)
        text(surf, "Use your own copy of the USA Rusty Hearts client. Its folder holds rustyhearts.exe and "
                   "the .pck data files.", (box.x + 22, box.bottom - 30), 15, C["dim"], max_w=box.w - 44)
        if self.found:
            text(surf, f"Found {len(self.found)} - select one with A", (X0, Y0 + 284), 15, C["muted"], True)


# ==================================================================== SETUP
class SetupScreen(Screen):
    """First-time install: find the downloaded archives and install everything with one button."""
    name, title = "setup", "Setup"
    default_focus = "su_install"

    DOCKER_HELP = {
        "missing": "Docker isn't installed. The server runs in Docker containers, so it's needed once.\n\n"
                   "Bazzite: the developer edition (bazzite-dx) includes Docker - see the Bazzite docs.\n"
                   "Other Linux: install Docker Engine and its Compose plugin from your distribution "
                   "or docs.docker.com.\n\nThen add yourself to the 'docker' group, log out and back in, "
                   "and press 'Check again'.",
        "denied": "Docker is installed, but your user isn't allowed to use it yet. In a terminal run:\n\n"
                  "    sudo usermod -aG docker $USER\n\nthen log out and back in (or reboot) and press "
                  "'Check again'.",
        "stopped": "Docker is installed but its service isn't running. In a terminal run:\n\n"
                   "    sudo systemctl enable --now docker\n\nthen press 'Check again'.",
    }

    def __init__(self, app):
        super().__init__(app)
        self.st, self.st_at, self.st_busy = None, 0, False
        self.found, self.searched = {}, False

    def badge(self):
        return None if (self.st and installer.installed(self.st)) else C["yellow"]

    # background work ------------------------------------------------------------------
    def refresh(self, then=None):
        if self.st_busy:
            return
        self.st_busy = True

        def work():
            try:
                st = installer.status()
            except Exception as e:
                st = None
                print("[setup] status failed:", e)

            def done():
                self.st_busy, self.st_at = False, time.time()
                if st:
                    self.st = st
                    # PLAY sends players here only while server files are missing (the client has its own tab)
                    self.app.setup_done = all(st[k] for k in ("server", "drivers", "configured", "patched"))
                if then:
                    then()
            self.app.call_soon(done)
        threading.Thread(target=work, daemon=True).start()

    def search(self, folder=None, quiet=False):
        def done(res, err):
            if err:
                self.app.dialog("Search failed", str(err))
                return
            self.found, self.searched = res or {}, True
            if not quiet:
                n = sum(1 for k in ("server", "vm", "client", "vhdx") if k in self.found)
                self.app.toast(f"Found {n} of the game files" if n else "No game files found yet",
                               "ok" if n else "warn")
        if quiet:
            def work():
                try:
                    res = installer.find_archives(extra=folder)
                except Exception:
                    res = {}
                self.app.call_soon(lambda: done(res, None))
            threading.Thread(target=work, daemon=True).start()
        else:
            self.app.run_task("Looking for the game files", lambda p: installer.find_archives(p, folder), done)

    def needed(self):
        """Which archives are still needed, given what is already installed."""
        st = self.st or {}
        need = []
        if not st.get("server"):
            need.append("server")
        if not st.get("drivers") and "vhdx" not in self.found:
            need.append("vm")
        cur = backend.load_client_dir()
        if not (cur and backend.inspect_client(cur)["ok"]):     # only the shader DLL missing: no archive needed
            need.append("client")
        return need

    def install(self):
        app, st = self.app, self.st
        if not st:
            return
        missing = [k for k in self.needed() if k not in self.found]
        if missing:
            names = {"server": "server.7z (server pack)", "vm": "server-vm.7z (server VM)",
                     "client": "client.7z (game client)"}
            app.dialog("Some files are missing", "Still needed:\n\n" + "\n".join("•  " + names[k] for k in missing) +
                       "\n\nDownload them (button 'Get the game files'), then press 'Look again'. If you already "
                       "have the game client somewhere, set it on the Game Client tab instead.")
            return
        if st["docker"] not in ("ok", "podman-setup"):
            self.docker_help()
            return
        if st["mem"] and st["mem"] < 13:
            app.dialog("Low memory", f"This system reports {st['mem']:.1f} GB of usable memory. A 16 GB Steam Deck "
                       "normally reports about 14.5 GB; less usually means more memory is set aside for graphics "
                       "(UMA frame buffer in the BIOS - 1 GB is best here).\n\nInstalling works either way. "
                       "Playing may run out of memory; the launcher then stops the server and game safely.",
                       [("Cancel", None, "ghost"), ("Install anyway", self.run_install, "primary")])
            return
        self.run_install()

    def run_install(self):
        app = self.app
        log = []
        modal = Progress(app, "Installing Rusty Hearts", log=log)
        inst = installer.Installer(dict(self.found), lambda m: setattr(modal, "status", m),
                                   lambda line: (log.append(line[:200]), log.__delitem__(slice(None, -200))))
        modal.cancel = inst.cancel
        modal.hints = [("B", "Cancel")]
        modal.source = inst
        app.open_modal(modal)
        app.log("Setup started")

        def worker():
            err = None
            try:
                inst.run_all()
            except installer.Cancelled:
                err = "cancelled"
            except Exception as e:
                import traceback
                traceback.print_exc()
                err = str(e) or e.__class__.__name__
            try:
                os.makedirs(LOG_DIR, exist_ok=True)
                with open(os.path.join(LOG_DIR, "rhlauncher-setup.log"), "w", encoding="utf-8") as f:
                    f.write("\n".join(log) + ("\nERROR: " + err if err else "") + "\n")
            except OSError:
                pass

            def finish():
                app.close_modal(modal)
                app._client_cache = None
                self.refresh()
                if err == "cancelled":
                    app.toast("Setup cancelled. Press Install again to continue where it stopped.", "warn", 7)
                elif err:
                    app.log("Setup failed: " + err)
                    app.dialog("Setup stopped", err + "\n\nThe full log is logs/rhlauncher-setup.log. Fix the "
                               "problem and press Install again: finished steps are skipped.")
                else:
                    app.log("Setup finished")
                    app.dialog("Rusty Hearts is installed",
                               "Everything is ready. Press PLAY: the very first server start also restores the "
                               "game databases, so it takes a few minutes longer than usual.\n\nThen create your "
                               "game account on the Accounts tab.",
                               [("Later", None, "ghost"), ("Go to Play", lambda: app.go("play"), "primary")])
            app.call_soon(finish)
        threading.Thread(target=worker, daemon=True).start()

    PODMAN_HELP = ("Podman (SteamOS's built-in container tool) isn't working. In Desktop Mode, open Konsole and "
                   "run:\n\n    podman info\n\nIf that shows an error, update SteamOS (Podman comes with SteamOS 3.5 "
                   "and later), restart the Deck, and press 'Check again'.")

    def docker_help(self):
        st = (self.st or {}).get("docker", "missing")
        if installer.PODMAN:
            self.app.dialog("Podman is needed", self.PODMAN_HELP, [("Check again", self.refresh, "primary")],
                            width=860)
            return
        self.app.dialog("Docker is needed", self.DOCKER_HELP.get(st, self.DOCKER_HELP["missing"]),
                        [("Check again", self.refresh, "primary")], width=860)

    def open_downloads(self):
        if not integration.open_folder(installer.DOWNLOAD_PAGE):
            self.app.dialog("Get the game files", "Open this page in a browser:\n\n" + installer.DOWNLOAD_PAGE)
            return
        self.app.toast("Download server.7z, server-vm.7z and client.7z into Downloads, then press Look again.",
                       "info", 9)

    # ui ------------------------------------------------------------------------------
    def build(self):
        app = self.app
        if not self.st and not self.st_busy:
            self.refresh()
        elif time.time() - self.st_at > 20 and not app.modals:
            self.refresh()
        if not self.searched:
            self.searched = True
            self.search(quiet=True)
        st = self.st
        done = bool(st and installer.installed(st))
        ws = [ui.Button("su_get", (X0, Y0, 330, 60), "Get the game files", self.open_downloads, "ghost", size=20),
              ui.Button("su_look", (X0 + 346, Y0, 230, 60), "Look again", lambda: self.search(), "ghost", size=20),
              ui.Button("su_browse", (X0 + 592, Y0, 250, 60), "Pick a folder…",
                        lambda: app.browse("Folder with the downloaded files", os.path.expanduser("~/Downloads"),
                                           lambda p: self.search(p)), "ghost", size=20)]
        label, sub, style = ("Install everything", "Unpacks and sets up the server and game", "primary")
        if done:
            label, sub, style = ("Installed", "Press PLAY to start", "success")
        ws.append(ui.Button("su_install", (X0 + CW - 360, Y0 + CH - 100, 360, 92), label,
                            (lambda: app.go("play")) if done else self.install, style, sub=sub, size=26,
                            enabled=bool(st) and app.server.busy is None))
        if st and st["docker"] not in ("ok", "podman-setup"):
            ws.append(ui.Button("su_docker", (X0, Y0 + CH - 100, 300, 92), "Docker help", self.docker_help,
                                "danger", size=22))
        return ws

    def draw(self, surf):
        st, f = self.st, self.found
        colw = (CW - 24) // 3
        top = Y0 + 80
        h = CH - 80 - 116

        def col(i, title):
            r = pygame.Rect(X0 + i * (colw + 12), top, colw, h)
            panel(surf, r, C["panel"], 16)
            text(surf, title, (r.x + 18, r.y + 14), 16, C["muted"], True)
            return r

        # 1. downloaded files
        r = col(0, "1. Your downloaded files")
        y = r.y + 48
        need = self.needed() if st else []
        for key, label in (("server", "Server pack"), ("vm", "Server VM"), ("client", "Game client")):
            path = f.get(key) or (f.get("vhdx") if key == "vm" else None)
            ok = True if path else (True if st and key not in need else (None if not self.searched else False))
            detail = os.path.basename(path) if path else ("not needed: already installed" if st and key not in need
                                                          else "not found yet")
            check_row(surf, r.x + 16, y, ok, label, detail, colw - 24)
            y += 62
        ui.text_block(surf, "Get them from the RaGEZONE 'RustyHeartenstein' release: server.7z, server-vm.7z and "
                            "client.7z. Leave them in Downloads; the launcher finds them.",
                      (r.x + 18, y + 6), colw - 36, 15, C["dim"])

        # 2. install steps
        r = col(1, "2. Installed")
        y = r.y + 48
        rows = [("server", "Server files"), ("drivers", "Database drivers"), ("client", "Game client"),
                ("configured", "Server settings"), ("patched", "Wine fix"), ("images", "Docker images")]
        for key, label in rows:
            ok = None if not st else bool(st[key])
            check_row(surf, r.x + 16, y, ok, label, "Done" if ok else ("Checking…" if ok is None else "To do"),
                      colw - 24)
            y += 56

        # 3. this PC
        r = col(2, "3. This PC")
        y = r.y + 48
        if st:
            dok = True if st["docker"] == "ok" else (None if st["docker"] == "podman-setup" else False)
            check_row(surf, r.x + 16, y, dok, "Podman" if installer.PODMAN else "Docker", st["docker_detail"],
                      colw - 24)
            y += 62
            p = st["proton"]
            check_row(surf, r.x + 16, y, bool(p), "Proton", os.path.basename(os.path.dirname(p)) if p else
                      "Get Proton Experimental in Steam", colw - 24)
            y += 62
            check_row(surf, r.x + 16, y, st["mem"] >= 13 or None, "Memory", f"{st['mem']:.1f} GB usable (16 GB PC/Deck)",
                      colw - 24)
            y += 62
            need_gb = (0 if st["server"] else 3) + (0 if st["client"] else 10) + (0 if st["drivers"] else 45)
            check_row(surf, r.x + 16, y, st["free"] >= need_gb or None, "Disk space",
                      f"{st['free']:.0f} GB free" + (f", setup needs ~{need_gb} GB" if need_gb else ""), colw - 24)
        else:
            text(surf, "Checking…", (r.x + 18, y), 17, C["dim"])
        msg = ("Everything is installed." if st and installer.installed(st) else
               "Press Install: it unpacks your files and sets up the server. The first time takes 20-40 minutes.")
        dx = 320 if st and st["docker"] not in ("ok", "podman-setup") else 0
        ui.text_block(surf, msg, (X0 + dx, Y0 + CH - 92), CW - 400 - dx, 17, C["muted"])


# ============================================================ AUTO-LOGIN
from .app import Modal


class AutoLoginDialog(Modal):
    """Saved login that a helper types into the game's login screen."""
    width, height = 900, 600
    hints = [("A", "Select"), ("B", "Close")]

    def __init__(self, app):
        super().__init__(app)
        d = backend.load_autologin()
        self.enabled, self.user, self.pw = d["enabled"], d["user"], ""
        self.has_pw = d["has_password"]
        delays = backend.AUTOLOGIN_DELAYS
        self.delay_i = delays.index(d["delay"]) if d["delay"] in delays else 2
        self.focus_key = "al_on"

    def save(self):
        if self.enabled and not self.user:
            self.app.toast("Enter the username first", "warn"); return
        if self.enabled and not (self.pw or self.has_pw):
            self.app.toast("Enter the password first", "warn"); return
        backend.save_autologin(self.enabled, backend.AUTOLOGIN_DELAYS[self.delay_i], self.user.strip().lower(),
                               self.pw if self.pw else None)
        self.app.close_modal(self)
        self.app.toast("Auto-login " + ("on: it types your login when the game starts" if self.enabled else "off"),
                       "ok", 6)

    def forget(self):
        backend.forget_autologin()
        self.app.close_modal(self)
        self.app.toast("Saved login removed", "ok")

    def build(self):
        r = self.rect
        x, w = r.x + 36, r.w - 72
        delays = backend.AUTOLOGIN_DELAYS
        return [
            ui.Toggle("al_on", (x, r.y + 90, w, 66), "Log in automatically", self.enabled,
                      lambda v: setattr(self, "enabled", v), sub="Types your username and password for you"),
            ui.TextField("al_user", (x, r.y + 168, w // 2 - 8, 70), "Username", self.user,
                         lambda v: setattr(self, "user", v.strip().lower()), placeholder="your game username"),
            ui.TextField("al_pw", (x + w // 2 + 8, r.y + 168, w // 2 - 8, 70), "Password", self.pw,
                         lambda v: setattr(self, "pw", v), password=True,
                         placeholder="saved (type to change)" if self.has_pw else "your game password"),
            ui.Selector("al_delay", (x, r.y + 252, w, 70), "Wait after the game window opens",
                        [f"{d} seconds" for d in delays], self.delay_i, lambda i: setattr(self, "delay_i", i)),
            ui.Button("al_forget", (x, r.bottom - 84, 240, 58), "Forget login", self.forget, "danger",
                      enabled=bool(self.user or self.has_pw)),
            ui.Button("al_cancel", (r.right - 36 - 200 - 16 - 200, r.bottom - 84, 200, 58), "Cancel",
                      lambda: self.app.close_modal(self), "ghost"),
            ui.Button("al_save", (r.right - 36 - 200, r.bottom - 84, 200, 58), "Save", self.save, "primary"),
        ]

    def draw_body(self, surf):
        r = self.rect
        text(surf, "Auto-login", (r.x + 36, r.y + 30), 28, C["text"], True)
        ui.text_block(surf, "When the game starts, the launcher waits, then types your login and presses Enter - "
                            "only while the game window is in front. If it types too early, pick a longer wait. "
                            "The password is kept in a file on this PC that only your user can read.",
                      (r.x + 36, r.y + 336), r.w - 72, 16, C["muted"])
