#!/usr/bin/env python3
"""Capture bounded local login diagnostics without modifying game/SQL state."""
from datetime import datetime, timezone
from pathlib import Path
import os
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent.parent
TAIL = 256 * 1024


def main():
    os.umask(0o077)
    docker = shutil.which('docker')
    if not docker and Path('/run/host/usr/bin/docker').exists():
        docker = '/run/host/usr/bin/docker'
    if not docker:
        raise SystemExit('Docker was not found. Run this command from a host terminal.')
    check = subprocess.run([docker, 'info', '--format', '{{.ServerVersion}}'], capture_output=True, text=True, timeout=15)
    if check.returncode:
        raise SystemExit('Cannot access Docker. Run this command from a host terminal:\n' + check.stderr)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    folder = ROOT / 'logs/diagnostics' / stamp
    folder.mkdir(parents=True)
    secrets = []
    env = ROOT / 'docker/.env'
    if env.exists():
        secrets = [line.split('=', 1)[1] for line in env.read_text().splitlines() if line.startswith('RH_SQL_PASSWORD=')]

    def save(name, value):
        for secret in secrets:
            if secret:
                value = value.replace(secret, '[REDACTED]')
        value = re.sub(r'(?is)(<(?:auth_token|password|original_password)>).*?(</[^>]+>)', r'\1[REDACTED]\2', value)
        value = re.sub(r'(?i)(\b(?:P|T|R)\[)\s*[^\]]*(\])', r'\1[REDACTED]\2', value)
        value = re.sub(r'(?i)((?:password|passwd|pwd|auth_token|authorization)\s*[=:]\s*)[^\s,;<>]+', r'\1[REDACTED]', value)
        (folder / name).write_text(value)

    def run(name, args, timeout=20):
        try:
            result = subprocess.run(args, capture_output=True, timeout=timeout)
            value = (result.stdout + b'\n' + result.stderr)[-TAIL:].decode('utf-8', errors='replace')
            save(name, f'exit_code={result.returncode}\n{value}')
        except (OSError, subprocess.TimeoutExpired) as error:
            save(name, f'Capture failed: {type(error).__name__}\n')

    run('containers.txt', [docker, 'ps', '-a', '--filter', 'name=rusty-hearts-offline-', '--format', '{{.Names}} {{.Status}}'])
    for service in ('sql', 'api', 'game'):
        container = f'rusty-hearts-offline-{service}-1'
        run(f'{service}-state.txt', [docker, 'inspect', '--format', '{{json .State}}', container])
        run(f'{service}-docker.log', [docker, 'logs', '--timestamps', '--tail', '500', container])
    game = 'rusty-hearts-offline-game-1'
    run('game-processes.txt', [docker, 'exec', game, 'ps', '-eo', 'pid,comm'])
    run('game-network.txt', [docker, 'exec', game, 'sh', '-c', 'cat /proc/net/tcp /proc/net/tcp6'])
    run('api-auth.log', [docker, 'exec', 'rusty-hearts-offline-api-1', 'sh', '-c', 'tail -n 250 /app/logs/auth-*.log'])
    # Execute checks inside the API container, which shares the host network.
    probe = '''
const net = require('net');
(async () => {
for (const port of [1433,3000,18070,18080,18090,50001,50006,50016]) {
 await new Promise(resolve => {
  const s = net.connect({host:'127.0.0.1',port}); let done=false;
  const end = status => {if(done)return;done=true;console.log(JSON.stringify({port,status}));s.destroy();resolve();};
  s.setTimeout(2000);s.on('connect',()=>end('listening'));s.on('error',()=>end('unreachable'));s.on('timeout',()=>end('timeout'));
 });
}
for (const path of ['/launcher/GetGatewayAction','/launcher/GetGatewayAction/info','/launcher/GetGatewayAction/status','/launcher/agreement']) {
 try {const r=await fetch('http://127.0.0.1:3000'+path,{signal:AbortSignal.timeout(3000)});const body=await r.text();console.log(JSON.stringify({path,status:r.status,bytes:body.length,...(path.includes('GetGatewayAction')?{body}:{} )}));}
 catch(e){console.log(JSON.stringify({path,error:e.name}));}
}
})();'''
    run('endpoint-checks.txt', [docker, 'exec', 'rusty-hearts-offline-api-1', 'node', '-e', probe], timeout=40)
    for label, process in [('gateway','GameGatewayServer_Release_x64.exe'),('lobby','GameServer_Release_USA_x64.exe')]:
        destination = f'/tmp/rh-diagnostic-{label}.txt'
        run(f'{label}-capture.txt', [docker, 'exec', '-e', 'DISPLAY=:99', game, 'wine', '/opt/rh/console-input.exe', '--dump', process, 'Z:'+destination.replace('/', '\\')])
        run(f'{label}-console.txt', [docker, 'exec', game, 'cat', destination])
    client_dir = ROOT / 'gameclient'
    try:
        for line in (ROOT / 'runtime/client.conf').read_text().splitlines():
            if line.startswith('CLIENT_DIR=') and line[11:].strip():
                client_dir = Path(line[11:].strip())
    except OSError:
        pass
    for name, path in [('game-client.log', client_dir/'RUSTYHEARTS.LOG'), ('client.log', ROOT/'logs/steam-0.log'), ('server-debug.log', ROOT/'runtime/linux/server/login-debug-private.txt')]:
        if path.exists():
            with path.open('rb') as source:
                source.seek(max(0, path.stat().st_size - TAIL))
                save(name, source.read(TAIL).decode('utf-8', errors='replace'))
    save('README.txt', f'UTC capture: {stamp}\nEach file reports capture errors independently.\nNo environment dumps, request bodies, packet payloads or SQL rows are collected.\nExisting logs may contain account identifiers; keep this folder private.\nA listening port does not prove login works. HTTP probe requests are generated by this capture.\n')
    # Capture again after probes so their failures and request IDs are included.
    run('api-docker.log', [docker, 'logs', '--timestamps', '--tail', '500', 'rusty-hearts-offline-api-1'])
    latest = ROOT / 'logs/diagnostics/latest.txt'
    latest.write_text(str(folder) + '\n')
    print(f'Diagnostics saved: {folder}')


if __name__ == '__main__':
    main()
