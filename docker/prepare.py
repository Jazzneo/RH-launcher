#!/usr/bin/env python3
"""Prepare localhost server settings; never starts Docker, Wine, or a VM."""
from pathlib import Path
import os
import re
import secrets
import shutil

ROOT = Path(__file__).resolve().parent.parent
region = os.environ.get('RH_REGION', 'usa')
if region not in {'usa', 'jpn'}:
    raise SystemExit('RH_REGION must be usa or jpn.')
env = ROOT / 'docker/.env'
if not env.exists():
    fd = os.open(env, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write('RH_SQL_PASSWORD=Rh9!' + secrets.token_urlsafe(24) + '\n')
password = next(line.split('=', 1)[1] for line in env.read_text().splitlines() if line.startswith('RH_SQL_PASSWORD='))
server = ROOT / 'runtime/linux/server'
if not server.exists():
    shutil.copytree(ROOT / 'server-package/RH Server (RustyHeartenstein) 04292025/Server', server)
templates = ROOT / 'sources/RustyHearts-Server-Manager-2.2.0/Resources/Option'
original_options = ROOT / 'server-package/RH Server (RustyHeartenstein) 04292025/Server/Option'
for original in original_options.glob('*.xml'):
    shutil.copy2(original, server / 'Option' / original.name)
for path in templates.glob('*.xml'):
    text = path.read_text(encoding='utf-8-sig')
    if not any(key in text for key in ('PRIVATE_IP', 'PUBLIC_IP', 'SQL_DB_')):
        continue
    for key, value in {'SQL_DB_PASSWORD': password, 'SQL_DB_USER': 'sa', 'SQL_DB_SERVER': '127.0.0.1,1433', 'PRIVATE_IP': '127.0.0.1', 'PUBLIC_IP': '127.0.0.1', 'DB_SERVER': '127.0.0.1,1433', 'DB_USER': 'sa', 'DB_PASSWD': password}.items():
        text = text.replace(key, value)
    (server / 'Option' / path.name).write_text(text)
service = server / 'Option/service_control.xml'
text = service.read_text(encoding='utf-8-sig')
text = re.sub(r'<active_area country="[^"]+"', f'<active_area country="{region}"', text)
service.write_text(text.replace(':8070/', ':18070/').replace(':8090/', ':18090/'))
print('Prepared Docker settings and localhost server configuration. No services started.')
