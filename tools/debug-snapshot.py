#!/usr/bin/env python3
"""Read-only snapshot of stuck server processes (DBC, Guild, Lobby).

Captures each process's console text and a Wine debugger backtrace of all its
threads (attach -> bt -> detach; no breakpoints, no memory writes). Output is
scrubbed: any line containing a fragment of the SQL password or a connection
string is replaced. Saved to logs/debug-snapshot-<UTC>.txt (mode 600).
"""
from datetime import datetime, timezone
from pathlib import Path
import os, re, shutil, subprocess

ROOT = Path(__file__).resolve().parent.parent
GAME = 'rusty-hearts-offline-game-1'
TARGETS = ['DBCServer_Release_x64.exe', 'GuildServer_Release_USA_x64.exe', 'GameServer_Release_USA_x64.exe']


def docker():
    d = shutil.which('docker') or ('/run/host/usr/bin/docker' if Path('/run/host/usr/bin/docker').exists() else None)
    if not d:
        raise SystemExit('Docker not found.')
    return d


def run(args, timeout=60, stdin=None):
    try:
        r = subprocess.run(args, input=stdin, capture_output=True, timeout=timeout)
        return (r.stdout + r.stderr).decode('utf-8', 'replace')
    except subprocess.TimeoutExpired:
        return f'[timed out after {timeout}s]\n'


def scrubber():
    secret = ''
    env = ROOT / 'docker/.env'
    if env.exists():
        for l in env.read_text().splitlines():
            if l.startswith('RH_SQL_PASSWORD='):
                secret = l.split('=', 1)[1]
    pieces = {secret[i:i + 5] for i in range(max(0, len(secret) - 4))} if len(secret) >= 5 else set()
    def scrub(text):
        out, skip_next = [], False
        for line in text.splitlines():
            bad = (skip_next or any(p in line for p in pieces) or
                   re.search(r'(?i)provider=|catalog=|password|pwd=|\b[PTR]\[', line))
            skip_next = bool(re.search(r'(?i)provider=|password=', line))  # console wraps long lines
            out.append('[line removed: may contain credentials]' if bad else line)
        return '\n'.join(out) + '\n'
    return scrub


def main():
    os.umask(0o077)
    d = docker()
    scrub = scrubber()
    parts = []
    procs = run([d, 'exec', '-e', 'DISPLAY=:99', GAME, 'sh', '-c', 'echo "info process" | timeout 20 winedbg 2>/dev/null'])
    parts.append('== Wine process list\n' + procs)
    for name in TARGETS:
        m = re.search(r'([0-9a-fA-F]{8})\s+\d+\s+.*' + re.escape(name), procs)
        dump = f'/tmp/rh-snap-{name}.txt'
        run([d, 'exec', '-e', 'DISPLAY=:99', GAME, 'wine', '/opt/rh/console-input.exe', '--dump', name,
             'Z:' + dump.replace('/', '\\')], timeout=30)
        parts.append(f'== {name} console (tail)\n' + '\n'.join(run([d, 'exec', GAME, 'cat', dump]).splitlines()[-120:]))
        if not m:
            parts.append(f'== {name}: not found in Wine process list\n')
            continue
        pid = int(m.group(1), 16)
        cmds = 'info thread\nbt all\ndetach\nquit\n'
        bt = run([d, 'exec', '-i', '-e', 'DISPLAY=:99', GAME, 'sh', '-c', f'timeout 45 winedbg {pid}'],
                 timeout=60, stdin=cmds.encode())
        # keep only this process's section to limit noise
        parts.append(f'== {name} (wine pid {pid}) threads/backtraces\n' + bt[-200000:])
    text = scrub('\n'.join(parts))
    out = ROOT / 'logs' / ('debug-snapshot-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.txt')
    out.write_text(text)
    print(f'Debug snapshot saved: {out}')


if __name__ == '__main__':
    main()
