/* RH Launcher auto-login helper.
 * Runs inside the game's Proton prefix, waits for the Rusty Hearts window, then types the saved
 * username, Tab, password, Enter - only while the game window has keyboard focus.
 *
 *   autologin.exe CREDFILE DELAY_SECONDS LOGFILE
 * CREDFILE: UTF-8, line 1 username, line 2 password (never printed or logged).
 */
#define UNICODE
#define _UNICODE
#include <windows.h>
#include <tlhelp32.h>
#include <stdio.h>
#include <wchar.h>

static FILE *logf;
static void say(const char *fmt, ...) {
    if (!logf) return;
    SYSTEMTIME t; GetLocalTime(&t);
    fprintf(logf, "%02d:%02d:%02d ", t.wHour, t.wMinute, t.wSecond);
    va_list a; va_start(a, fmt); vfprintf(logf, fmt, a); va_end(a);
    fputc('\n', logf); fflush(logf);
}

static DWORD game_pids[64]; static int n_pids;
static void find_game_pids(void) {
    n_pids = 0;
    HANDLE s = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (s == INVALID_HANDLE_VALUE) return;
    PROCESSENTRY32W e; e.dwSize = sizeof(e);
    for (BOOL ok = Process32FirstW(s, &e); ok && n_pids < 64; ok = Process32NextW(s, &e))
        if (!_wcsicmp(e.szExeFile, L"rustyhearts.exe")) game_pids[n_pids++] = e.th32ProcessID;
    CloseHandle(s);
}

static HWND best; static int best_area;
static BOOL CALLBACK each_window(HWND w, LPARAM unused) {
    DWORD pid; GetWindowThreadProcessId(w, &pid);
    int mine = 0;
    for (int i = 0; i < n_pids; ++i) if (game_pids[i] == pid) mine = 1;
    if (!mine || !IsWindowVisible(w)) return TRUE;
    RECT r; GetClientRect(w, &r);
    int area = (r.right - r.left) * (r.bottom - r.top);
    if ((r.right - r.left) >= 600 && (r.bottom - r.top) >= 400 && area > best_area) { best = w; best_area = area; }
    return TRUE;
}

static HWND game_window(void) {
    find_game_pids();
    best = NULL; best_area = 0;
    if (n_pids) EnumWindows(each_window, 0);
    return best;
}

static int focused(HWND w) {
    HWND f = GetForegroundWindow();
    return f == w || GetAncestor(f, GA_ROOT) == w;
}

static int send_vk(WORD vk, int up) {
    INPUT in = {0};
    in.type = INPUT_KEYBOARD;
    in.ki.wVk = vk;
    in.ki.wScan = (WORD)MapVirtualKeyW(vk, MAPVK_VK_TO_VSC);
    in.ki.dwFlags = up ? KEYEVENTF_KEYUP : 0;
    return SendInput(1, &in, sizeof(in)) == 1;
}

static int tap(HWND w, WORD vk, int shift) {
    if (!focused(w)) return 0;
    if (shift) send_vk(VK_SHIFT, 0);
    send_vk(vk, 0); Sleep(25); send_vk(vk, 1);
    if (shift) send_vk(VK_SHIFT, 1);
    Sleep(45);
    return 1;
}

static int type_text(HWND w, const wchar_t *s) {
    for (; *s; ++s) {
        SHORT k = VkKeyScanW(*s);
        if (k == -1) { say("a character can't be typed on this keyboard layout"); return 0; }
        if (!tap(w, LOBYTE(k), (HIBYTE(k) & 1) != 0)) return 0;
    }
    return 1;
}

static int read_creds(const wchar_t *path, wchar_t *user, wchar_t *pass, int n) {
    FILE *f = _wfopen(path, L"rb");
    if (!f) return 0;
    char buf[512] = {0};
    size_t got = fread(buf, 1, sizeof(buf) - 1, f);
    fclose(f);
    buf[got] = 0;
    char *nl = strchr(buf, '\n');
    if (!nl) { SecureZeroMemory(buf, sizeof(buf)); return 0; }
    *nl = 0;
    char *p2 = nl + 1, *e;
    if ((e = strchr(buf, '\r'))) *e = 0;
    if ((e = strchr(p2, '\r'))) *e = 0;
    if ((e = strchr(p2, '\n'))) *e = 0;
    int ok = MultiByteToWideChar(CP_UTF8, 0, buf, -1, user, n) > 1 &&
             MultiByteToWideChar(CP_UTF8, 0, p2, -1, pass, n) > 1;
    SecureZeroMemory(buf, sizeof(buf));
    return ok;
}

int wmain(int argc, wchar_t **argv) {
    if (argc < 3) { fwprintf(stderr, L"usage: autologin.exe CREDFILE DELAY [LOG]\n"); return 2; }
    if (argc >= 4) logf = _wfopen(argv[3], L"a");
    int delay = _wtoi(argv[2]);
    if (delay < 0 || delay > 600) delay = 20;
    wchar_t user[128], pass[128];
    if (!read_creds(argv[1], user, pass, 128)) { say("no saved login (credential file unreadable)"); return 3; }
    say("waiting for the Rusty Hearts window");

    HWND w = NULL;
    for (int i = 0; i < 480 && !(w = game_window()); ++i) Sleep(500);
    if (!w) { say("game window never appeared - giving up"); goto out; }
    RECT r; GetClientRect(w, &r);
    say("game window found (%ldx%ld); waiting %d s for the login screen", r.right, r.bottom, delay);
    for (int i = 0; i < delay * 2; ++i) {
        Sleep(500);
        if (!IsWindow(w)) { say("game closed"); goto out; }
    }
    HWND again = game_window();
    if (again) w = again;

    SetForegroundWindow(w);
    for (int i = 0; i < 60 && !focused(w); ++i) Sleep(500);   /* up to 30 s for the player to click it */
    if (!focused(w)) { say("game window isn't focused - not typing (click the game and use the launcher's retry)"); goto out; }

    /* clear a remembered username, then: user, Tab, password, Enter */
    tap(w, VK_END, 0);
    for (int i = 0; i < 24; ++i) if (!tap(w, VK_BACK, 0)) goto lost;
    if (!type_text(w, user)) goto lost;
    if (!tap(w, VK_TAB, 0)) goto lost;
    Sleep(150);
    for (int i = 0; i < 24; ++i) if (!tap(w, VK_BACK, 0)) goto lost;
    if (!type_text(w, pass)) goto lost;
    Sleep(150);
    if (!tap(w, VK_RETURN, 0)) goto lost;
    say("login typed");
    goto out;
lost:
    say("stopped typing: the game window lost focus");
out:
    SecureZeroMemory(pass, sizeof(pass));
    SecureZeroMemory(user, sizeof(user));
    if (logf) fclose(logf);
    return 0;
}
