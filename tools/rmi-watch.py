#!/usr/bin/env python3
"""Watch whether the character-list request leaves the lobby and reaches a DBC.

  rmi-watch.py start   attach winedbg to lobby + both DBCs, set watch points, run in background
  rmi-watch.py stop    end the watch and write logs/rmi-watch-<UTC>.txt

Each watch point only counts hits (time + address); no memory is written and
nothing about the game state is changed. Hits are capped, after which the
watch point is disabled. Stopping tries a clean detach; if the debugger can't
be interrupted it is killed, which also ends the watched server processes
(only used right before the launcher stops the whole server anyway).
"""
from datetime import datetime, timezone
from pathlib import Path
import json, os, re, select, shutil, signal, subprocess, sys, time

ROOT = Path(__file__).resolve().parent.parent
GAME = 'rusty-hearts-offline-game-1'
STATE = ROOT / 'runtime/rmi-watch.json'
PROMPT = 'Wine-dbg>'

# Addresses for the shipped binaries at their preferred base 0x140000000
# (found from the RMI switch tables; see agent.MD). Rebased automatically.
TARGETS = {
    'GameServer_Release_USA_x64.exe': [
        ('lobby SENDS REQ_SAVE_SERVER_INFO', [0x14022b6a0, 0x14022b7e0], 20),
        ('lobby SENDS REQ_CHARACTER_LIST',   [0x140224bf0, 0x140224d00], 20),
    ],
    'DBCServer_Release_x64.exe': [
        ('game DBC RECEIVES REQ_SAVE_SERVER_INFO', [0x140020a7b], 20),
        ('game DBC RECEIVES REQ_CHARACTER_LIST',   [0x1400175f0], 20),
        ('game DBC worker PICKS UP a job',         [0x1400027b4], 60),
    ],
    'DBCServer_LOG_Release_x64.exe': [
        ('log DBC RECEIVES REQ_SAVE_SERVER_INFO', [0x140020fea], 20),
        ('log DBC RECEIVES REQ_CHARACTER_LIST',   [0x1400179da], 20),
        ('log DBC worker PICKS UP a job',         [0x1400027b4], 60),
    ],
}
PREFERRED = 0x140000000


def docker():
    d = shutil.which('docker') or ('/run/host/usr/bin/docker' if Path('/run/host/usr/bin/docker').exists() else None)
    if not d:
        raise SystemExit('Docker not found.')
    return d


def now():
    return datetime.now(timezone.utc).strftime('%H:%M:%S.%f')[:-3]


class Session:
    def __init__(self, exe, wpid, log):
        self.exe, self.wpid, self.log = exe, wpid, log
        self.p = subprocess.Popen([docker(), 'exec', '-i', '-e', 'DISPLAY=:99', GAME, 'winedbg', str(wpid)],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
        os.set_blocking(self.p.stdout.fileno(), False)
        self.buf = ''
        self.bp = {}      # bp number -> [label, cap, hits(list of times), disabled]
        self.running = False
        self.errors = []

    def send(self, cmd):
        self.p.stdin.write((cmd + '\n').encode()); self.p.stdin.flush()

    def read_chunk(self):
        try:
            data = self.p.stdout.read()
        except (BlockingIOError, TypeError):
            data = None
        if data:
            self.buf += data.decode('utf-8', 'replace')
        return bool(data)

    def until_prompt(self, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            if PROMPT in self.buf:
                out, self.buf = self.buf.split(PROMPT, 1)
                return out
            if self.p.poll() is not None:
                self.read_chunk()
                if PROMPT in self.buf:
                    continue
                raise RuntimeError(f'winedbg exited ({self.exe}): {self.buf[-400:]}')
            select.select([self.p.stdout], [], [], 0.2)
            self.read_chunk()
        raise RuntimeError(f'no winedbg prompt from {self.exe} within {timeout}s: {self.buf[-400:]}')

    def setup(self):
        self.until_prompt(60)  # attached and stopped
        self.send('info share')
        share = self.until_prompt()
        stem = self.exe.lower().rsplit('.', 1)[0]
        base = None
        for line in share.splitlines():
            if stem in line.lower():
                m = re.search(r'([0-9a-fA-F]{8,16})\s*-\s*[0-9a-fA-F]{8,16}', line)
                if m:
                    base = int(m.group(1), 16); break
        if base is None:
            self.errors.append('module base not found; assuming preferred base')
            base = PREFERRED
        delta = base - PREFERRED
        for label, addrs, cap in TARGETS[self.exe]:
            for a in addrs:
                self.send(f'break *0x{a + delta:x}')
                out = self.until_prompt()
                m = re.search(r'Breakpoint (\d+) at', out)
                if not m:
                    self.errors.append(f'could not set {label} at 0x{a + delta:x}: {out.strip()[:200]}')
                    continue
                self.bp[int(m.group(1))] = [label, cap, [], False]
        self.log(f'{self.exe} (wine pid {self.wpid}) base 0x{base:x}: {len(self.bp)} watch points set')
        self.send('cont'); self.running = True

    def pump(self):
        """Handle any breakpoint stops; returns quickly."""
        self.read_chunk()
        while PROMPT in self.buf:
            out, self.buf = self.buf.split(PROMPT, 1)
            m = re.search(r'Stopped on breakpoint (\d+)', out)
            if m and int(m.group(1)) in self.bp:
                n = int(m.group(1)); rec = self.bp[n]
                rec[2].append(now())
                if len(rec[2]) >= rec[1] and not rec[3]:
                    rec[3] = True
                    self.send(f'disable {n}'); self.until_prompt()
            elif out.strip():
                self.log(f'{self.exe}: unexpected stop: {out.strip()[:300]}')
            self.send('cont')

    def stop(self):
        if self.p.poll() is None:
            # try to interrupt winedbg so we can detach cleanly
            subprocess.run([docker(), 'exec', GAME, 'pkill', '-INT', '-f', f'winedbg {self.wpid}'],
                           capture_output=True)
            try:
                deadline = time.time() + 5
                while time.time() < deadline and PROMPT not in self.buf:
                    select.select([self.p.stdout], [], [], 0.2); self.read_chunk()
                if PROMPT in self.buf:
                    self.buf = ''
                    self.send('detach'); self.until_prompt(10); self.send('quit')
                    try:
                        self.p.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        pass
                    self.log(f'{self.exe}: detached cleanly')
            except Exception as e:  # noqa
                self.log(f'{self.exe}: detach failed ({e})')
        if self.p.poll() is None:
            subprocess.run([docker(), 'exec', GAME, 'pkill', '-KILL', '-f', f'winedbg {self.wpid}'], capture_output=True)
            self.p.kill()
            self.log(f'{self.exe}: debugger killed (server process may have ended with it)')


def wine_pids():
    out = subprocess.run([docker(), 'exec', '-e', 'DISPLAY=:99', GAME, 'sh', '-c',
                          'echo "info process" | timeout 20 winedbg 2>/dev/null'],
                         capture_output=True, text=True).stdout
    pids = {}
    for exe in TARGETS:
        m = re.search(r"([0-9a-fA-F]{8})\s+\d+\s+.*'" + re.escape(exe) + "'", out)
        if m:
            pids[exe] = int(m.group(1), 16)
    return pids


def daemon(logpath):
    lines = []
    def log(msg):
        lines.append(f'[{now()}] {msg}')
        Path(logpath).write_text('\n'.join(lines) + '\n')
    stop = {'flag': False}
    signal.signal(signal.SIGTERM, lambda *a: stop.__setitem__('flag', True))
    sessions = []
    for exe, wpid in wine_pids().items():
        try:
            s = Session(exe, wpid, log); s.setup(); sessions.append(s)
        except Exception as e:
            log(f'FAILED to watch {exe}: {e}')
    missing = set(TARGETS) - {s.exe for s in sessions}
    for m in missing:
        log(f'not watching {m} (process not found or attach failed)')
    while not stop['flag']:
        for s in sessions:
            try:
                s.pump()
            except Exception as e:
                log(f'{s.exe}: {e}')
        time.sleep(0.05)
    for s in sessions:
        s.stop()
    # summary
    log('')
    log('=== Watch point hits (UTC times) ===')
    hit = {}
    for s in sessions:
        for err in s.errors:
            log(f'  ! {s.exe}: {err}')
        agg = {}
        for n, (label, cap, times, disabled) in s.bp.items():
            agg.setdefault(label, []).extend(times)
        for label, times in agg.items():
            times.sort()
            hit[label] = len(times)
            log(f'  {label:<42} {len(times):>3}  ' + (f'first {times[0]}  last {times[-1]}' if times else ''))
    log('')
    g = lambda k: hit.get(k)
    send_ctl, send_cl = g('lobby SENDS REQ_SAVE_SERVER_INFO'), g('lobby SENDS REQ_CHARACTER_LIST')
    recv_cl = (g('game DBC RECEIVES REQ_CHARACTER_LIST') or 0) + (g('log DBC RECEIVES REQ_CHARACTER_LIST') or 0)
    if send_ctl is None or send_cl is None:
        log('VERDICT: lobby was not watched - no conclusion.')
    elif send_ctl == 0:
        log('VERDICT: control never fired - the world was not selected while watching (or watch points are wrong).')
    elif send_cl == 0:
        log('VERDICT: lobby got through REQ_SAVE_SERVER_INFO but NEVER SENT REQ_CHARACTER_LIST. The problem is inside the lobby, between the save-server-info step and the character-list request.')
    elif recv_cl == 0:
        log('VERDICT: lobby SENT REQ_CHARACTER_LIST but no DBC received it. Lost in transit / sent to the wrong host.')
    else:
        log('VERDICT: a DBC RECEIVED REQ_CHARACTER_LIST. Compare its worker pick-ups: if none, the DBC drops it before queuing.')
    log('DONE')


def cmd_start():
    os.umask(0o077)
    if STATE.exists():
        cmd_stop()
    logpath = ROOT / 'logs' / ('rmi-watch-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.txt')
    p = subprocess.Popen([sys.executable, __file__, '_daemon', str(logpath)], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=open(str(logpath) + '.err', 'w'))
    STATE.write_text(json.dumps({'pid': p.pid, 'log': str(logpath)}))
    # wait until watch points are set (or failure) so the user only acts once ready
    for _ in range(120):
        time.sleep(1)
        t = logpath.read_text() if logpath.exists() else ''
        if t.count('watch points set') + t.count('not watching') + t.count('FAILED') >= len(TARGETS):
            break
    print(logpath.read_text() if logpath.exists() else 'watcher log not created yet')


def cmd_stop():
    if not STATE.exists():
        print('No watch running.'); return
    st = json.loads(STATE.read_text())
    try:
        os.kill(st['pid'], signal.SIGTERM)
    except ProcessLookupError:
        pass
    log = Path(st['log'])
    for _ in range(60):
        time.sleep(1)
        if log.exists() and 'DONE' in log.read_text():
            break
    STATE.unlink(missing_ok=True)
    print(log.read_text() if log.exists() else 'No watch log written.')


if __name__ == '__main__':
    a = sys.argv[1:] or ['start']
    if a[0] == '_daemon':
        daemon(a[1])
    elif a[0] == 'stop':
        cmd_stop()
    else:
        cmd_start()
