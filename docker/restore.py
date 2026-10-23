#!/usr/bin/env python3
"""Restore supplied backups once; never overwrite existing character databases."""
from pathlib import Path
import os
import subprocess
import time

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ['docker', 'compose', '-f', str(ROOT / 'docker/compose.yaml')]
password = next(x.split('=', 1)[1] for x in (ROOT / 'docker/.env').read_text().splitlines() if x.startswith('RH_SQL_PASSWORD='))

def sql(query):
    env = dict(os.environ, SQLCMDPASSWORD=password)
    return subprocess.check_output(COMPOSE + ['exec', '-T', '-e', 'SQLCMDPASSWORD', 'sql', '/opt/mssql-tools18/bin/sqlcmd', '-S', '127.0.0.1', '-U', 'sa', '-C', '-b', '-W', '-h', '-1', '-s', '|', '-Q', query], env=env, text=True)

for i in range(60):
    try:
        sql('SET NOCOUNT ON; SELECT 1;')
        break
    except subprocess.CalledProcessError:
        time.sleep(5)
else:
    raise SystemExit('SQL Server did not become ready.')

container = subprocess.check_output(COMPOSE + ['ps', '-q', 'sql'], text=True).strip()
subprocess.run(['docker', 'exec', '-u', '0', container, 'mkdir', '-p', '/var/opt/mssql/backup'], check=True)
backups = ROOT / 'server-package/RH Server (RustyHeartenstein) 04292025/DB Backup/RustyHearts_Backup_20250429175454'
for path in sorted(backups.glob('*.bak')):
    db = path.stem
    if db not in {'RustyHearts', 'RustyHearts_Account', 'RustyHearts_Auth', 'RustyHearts_Log', 'GMRustyHearts'}:
        raise SystemExit('Unexpected database backup: ' + db)
    existing = sql(f"SET NOCOUNT ON; SELECT COUNT(*) FROM sys.databases WHERE name=N'{db}';").strip()
    if existing == '1':
        print(db + ': already exists; preserving it.')
        continue
    target = '/var/opt/mssql/backup/' + path.name
    subprocess.run(['docker', 'cp', str(path), container + ':' + target], check=True)
    subprocess.run(['docker', 'exec', '-u', '0', container, 'chmod', '644', target], check=True)
    rows = sql("RESTORE FILELISTONLY FROM DISK=N'" + target + "';")
    moves = []
    for row in rows.splitlines():
        cols = row.split('|')
        if len(cols) < 3 or cols[2].strip() not in {'D', 'L'}:
            continue
        logical = cols[0].strip().replace("'", "''")
        ext = '.ldf' if cols[2].strip() == 'L' else '.mdf'
        dest = '/var/opt/mssql/data/' + db + '_' + str(len(moves)) + ext
        moves.append("MOVE N'" + logical + "' TO N'" + dest + "'")
    if not moves:
        raise SystemExit('No restorable files found for ' + db)
    print('Restoring ' + db + '...')
    sql(f"RESTORE DATABASE [{db}] FROM DISK=N'{target}' WITH " + ', '.join(moves) + ', RECOVERY;')
sql("UPDATE RustyHearts_Auth.dbo.ServerOption SET PublicAddress='127.0.0.1' WHERE PublicAddress<>''; UPDATE RustyHearts_Auth.dbo.ServerOption SET PrivateAddress='127.0.0.1' WHERE PrivateAddress<>'';")
print('Database restore/configuration finished. Existing databases were preserved.')
