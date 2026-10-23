#!/usr/bin/env python3
from pathlib import Path
import socket
import subprocess

mem = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
available = int(mem['MemAvailable'].split()[0]) / 1024 / 1024
subprocess.run(['docker', 'info', '--format', '{{.CgroupVersion}}'], check=True, stdout=subprocess.DEVNULL)
root = Path(__file__).resolve().parent.parent
compose = ['docker', 'compose', '-f', str(root / 'docker/compose.yaml')]
running = set(subprocess.check_output(compose + ['ps', '--services', '--filter', 'status=running'], text=True).split())
# A ready stack has already allocated its startup memory. Keep headroom for
# remaining container growth, the capped client, and other desktop applications.
ready = {'sql', 'api', 'game'} <= running and subprocess.run(
    compose + ['exec', '-T', 'game', 'test', '-f', '/tmp/rh-ready'],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15,
).returncode == 0
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory_profile
prof = memory_profile.load(root)
required = prof['ready_gib'] if ready else prof['start_gib']
if available < required:
    raise SystemExit(f'ERROR: Not enough free memory: {available:.1f} GB free, the "{prof["label"]}" server memory setting needs {required} GB. Close other apps, or pick a lower Server memory setting in RH Launcher > Settings.')
owned_ports = set()
for service, ports in {'sql': (1433,), 'api': (3000, 18070, 18080, 18090), 'game': (50001, 50004, 50006, 50016, 50091)}.items():
    if service in running:
        owned_ports.update(ports)
for port in (1433, 3000, 18070, 18080, 18090, 50001, 50004, 50006, 50016, 50091):
    if port in owned_ports:
        continue
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(('127.0.0.1', port))
        except OSError:
            raise SystemExit(f'ERROR: Port {port} is already in use. Is the server already running in another launcher folder, or in Docker/Podman? Turn that one off first.')
print(f'Preflight passed: {available:.1f} GiB available. Runtime containers have hard memory and process limits.')
