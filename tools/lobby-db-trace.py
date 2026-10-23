#!/usr/bin/env python3
"""One-shot SQL trace of a single lobby login attempt.

Answers one question: after the client sends PT_GET_AUTH_INFO_PWE_TYPE to the
lobby, does the DBC run the lobby-stage game-database procedures
(up_read_uni_account_info, up_get_all_character, up_get_keymap, ...), and do
they fail or time out?

Usage:  lobby-db-trace.py start | read | stop | run
  run = start, wait for Enter (or --seconds N), read, diagnostics, stop.

Safety: read-only with respect to game data. It only creates/drops the
temporary Extended Events session 'rh_lobby_trace'. The SA password is never
read by this script: sqlcmd runs inside the SQL container using the
container's own MSSQL_SA_PASSWORD. Statement text and parameters are not
collected; batch text is reduced to procedure names before display.
"""
from datetime import datetime, timezone
from pathlib import Path
import os, re, shutil, subprocess, sys, select

ROOT = Path(__file__).resolve().parent.parent
SQL_CONTAINER = 'rusty-hearts-offline-sql-1'
SESSION = 'rh_lobby_trace'
SEP = '\x1f'  # unit separator; never appears in procedure names or messages we keep

LOBBY_PROCS = ['up_save_server_info', 'up_read_uni_account_info', 'up_get_all_character',
               'up_get_keymap', 'up_read_account_storage', 'up_read_cash_product_list',
               'up_read_pc_room_point', 'up_update_character_lobby', 'up_get_character']


def docker_bin():
    d = shutil.which('docker')
    if not d and Path('/run/host/usr/bin/docker').exists():
        d = '/run/host/usr/bin/docker'
    if not d:
        raise SystemExit('Docker was not found. Run this from a host terminal (or via host-spawn).')
    return d


def sqlcmd(query, timeout=60):
    """Run T-SQL inside the SQL container; password stays in that container."""
    script = ('SQLCMDPASSWORD="$MSSQL_SA_PASSWORD" /opt/mssql-tools18/bin/sqlcmd '
              '-S 127.0.0.1 -U sa -C -b -I -W -h -1 -s "$RH_SEP" -i /dev/stdin')
    r = subprocess.run([docker_bin(), 'exec', '-i', '-e', 'RH_SEP=' + SEP, SQL_CONTAINER, 'sh', '-c', script],
                       input=query.encode(), capture_output=True, timeout=timeout)
    out = r.stdout.decode('utf-8', 'replace')
    if r.returncode:
        raise SystemExit('sqlcmd failed (exit %d):\n%s%s' % (r.returncode, redact(out), redact(r.stderr.decode('utf-8', 'replace'))))
    return out


_secrets = None
def redact(text):
    global _secrets
    if _secrets is None:
        env = ROOT / 'docker/.env'
        _secrets = []
        if env.exists():
            _secrets = [l.split('=', 1)[1] for l in env.read_text().splitlines()
                        if l.startswith('RH_SQL_PASSWORD=')]
    for s in _secrets:
        if s:
            text = text.replace(s, '[REDACTED]')
    text = re.sub(r"(?i)((?:password|passwd|pwd)\s*[=:]\s*)[^\s,;'\"]+", r'\1[REDACTED]', text)
    text = re.sub(r'(?i)(\b(?:P|T|R)\[)\s*[^\]]*(\])', r'\1[REDACTED]\2', text)
    return text


START_SQL = f"""
SET NOCOUNT ON;
IF EXISTS (SELECT 1 FROM sys.server_event_sessions WHERE name = N'{SESSION}')
    DROP EVENT SESSION [{SESSION}] ON SERVER;
CREATE EVENT SESSION [{SESSION}] ON SERVER
ADD EVENT sqlserver.rpc_completed (
    SET collect_statement = 0, collect_data_stream = 0, collect_output_parameters = 0
    ACTION (sqlserver.database_name, sqlserver.client_app_name, sqlserver.session_id)
    WHERE ([object_name] <> N'sp_reset_connection' AND sqlserver.client_app_name <> N'SQLCMD')),
ADD EVENT sqlserver.sql_batch_completed (
    ACTION (sqlserver.database_name, sqlserver.client_app_name, sqlserver.session_id)
    WHERE (sqlserver.client_app_name <> N'SQLCMD')),
ADD EVENT sqlserver.error_reported (
    ACTION (sqlserver.database_name, sqlserver.client_app_name, sqlserver.session_id)
    WHERE ([severity] > 10 AND sqlserver.client_app_name <> N'SQLCMD')),
ADD EVENT sqlserver.rpc_starting (
    SET collect_statement = 0, collect_data_stream = 0
    ACTION (sqlserver.database_name, sqlserver.client_app_name, sqlserver.session_id)
    WHERE (sqlserver.database_name = N'RustyHearts')),
ADD EVENT sqlserver.sql_batch_starting (
    ACTION (sqlserver.database_name, sqlserver.client_app_name, sqlserver.session_id)
    WHERE (sqlserver.database_name = N'RustyHearts')),
ADD EVENT sqlserver.login (SET collect_options_text = 0
    ACTION (sqlserver.database_name, sqlserver.client_app_name, sqlserver.session_id, sqlserver.client_pid)),
ADD EVENT sqlserver.attention (
    ACTION (sqlserver.database_name, sqlserver.client_app_name, sqlserver.session_id))
ADD TARGET package0.ring_buffer (SET max_memory = 16384, max_events_limit = 20000)
WITH (EVENT_RETENTION_MODE = ALLOW_SINGLE_EVENT_LOSS, MAX_DISPATCH_LATENCY = 1 SECONDS, STARTUP_STATE = OFF);
ALTER EVENT SESSION [{SESSION}] ON SERVER STATE = START;
SELECT 'armed', CONVERT(varchar(30), SYSUTCDATETIME(), 126);
"""

READ_SQL = f"""
SET NOCOUNT ON;
DECLARE @d xml = (SELECT CAST(t.target_data AS xml) FROM sys.dm_xe_session_targets t
    JOIN sys.dm_xe_sessions s ON s.address = t.event_session_address
    WHERE s.name = N'{SESSION}' AND t.target_name = N'ring_buffer');
IF @d IS NULL BEGIN SELECT 'NO_SESSION'; RETURN; END
SELECT 'CONN', ISNULL(DB_NAME(s.database_id),'?'), ISNULL(s.program_name,''), ISNULL(CONVERT(varchar(10), s.host_process_id),''), COUNT(*)
FROM sys.dm_exec_sessions s WHERE s.is_user_process = 1 AND s.session_id <> @@SPID
GROUP BY s.database_id, s.program_name, s.host_process_id ORDER BY 2, 3;
SELECT 'META', @d.value('(/RingBufferTarget/@eventCount)[1]','int'), @d.value('(/RingBufferTarget/@droppedCount)[1]','int');
SELECT 'EV',
  CONVERT(varchar(30), e.value('@timestamp','datetime2(3)'), 126),
  e.value('@name','nvarchar(60)'),
  ISNULL(e.value('(action[@name="database_name"]/value)[1]','nvarchar(128)'),''),
  ISNULL(e.value('(action[@name="client_app_name"]/value)[1]','nvarchar(128)'),''),
  ISNULL(CONVERT(varchar(10), e.value('(action[@name="session_id"]/value)[1]','int')),''),
  ISNULL(e.value('(data[@name="object_name"]/value)[1]','nvarchar(256)'),''),
  ISNULL(REPLACE(REPLACE(LEFT(e.value('(data[@name="batch_text"]/value)[1]','nvarchar(max)'),400),CHAR(10),' '),CHAR(13),' '),''),
  ISNULL(e.value('(data[@name="result"]/text)[1]','nvarchar(40)'),''),
  ISNULL(CONVERT(varchar(20), e.value('(data[@name="row_count"]/value)[1]','bigint')),''),
  ISNULL(CONVERT(varchar(20), e.value('(data[@name="duration"]/value)[1]','bigint')),''),
  ISNULL(CONVERT(varchar(10), e.value('(data[@name="error_number"]/value)[1]','int')),''),
  ISNULL(CONVERT(varchar(10), e.value('(data[@name="severity"]/value)[1]','int')),''),
  ISNULL(REPLACE(REPLACE(LEFT(e.value('(data[@name="message"]/value)[1]','nvarchar(max)'),400),CHAR(10),' '),CHAR(13),' '),'')
FROM @d.nodes('/RingBufferTarget/event') n(e)
ORDER BY e.value('@timestamp','datetime2(3)');
"""

STOP_SQL = f"""
SET NOCOUNT ON;
IF EXISTS (SELECT 1 FROM sys.server_event_sessions WHERE name = N'{SESSION}')
    DROP EVENT SESSION [{SESSION}] ON SERVER;
SELECT 'stopped';
"""


def proc_names(batch):
    """Reduce batch text to identifiers only - never literals/parameters."""
    names = re.findall(r'\b(?:up|sp|xp|usp)_[A-Za-z0-9_]+', batch)
    if names:
        return 'batch:' + ','.join(dict.fromkeys(names))
    m = re.match(r'\s*([A-Za-z]+)', batch)
    return 'batch:' + (m.group(1).upper() if m else '?')


def clean_msg(msg):
    msg = re.sub(r"'[^']*'", "'…'", msg)  # strip literals from error text
    return redact(msg)


conns = []
def parse(out):
    meta, events = None, []
    for line in out.splitlines():
        f = [x.strip() for x in line.split(SEP)]
        if f[0] == 'NO_SESSION':
            raise SystemExit('Trace session is not running. Run "start" first.')
        if f[0] == 'CONN' and len(f) >= 5:
            conns.append(f[1:5])
            continue
        if f[0] == 'META' and len(f) >= 3:
            meta = (f[1], f[2])
        elif f[0] == 'EV' and len(f) >= 14:
            ts, ev, db, app, spid, obj, batch, res, rows, dur, errno, sev, msg = f[1:14]
            what = obj if obj else (proc_names(batch) if batch else '')
            events.append(dict(ts=ts, ev=ev, db=db, app=app, spid=spid, what=what, res=res,
                               rows=rows, dur=dur, errno=errno, sev=sev, msg=clean_msg(msg)))
    return meta, events


def report(meta, events):
    lines = []
    lines.append(f'Events captured: {meta[0] if meta else "?"}  dropped: {meta[1] if meta else "?"}')
    by_db = {}
    for e in events:
        by_db[e['db'] or '(none)'] = by_db.get(e['db'] or '(none)', 0) + 1
    lines.append('Events by database: ' + ', '.join(f'{k}={v}' for k, v in sorted(by_db.items())))
    lines.append('')
    lines.append('UTC time                 event                db                    spid  procedure/batch                         result     rows   ms     error')
    for e in events:
        ms = str(int(e['dur']) // 1000) if e['dur'].isdigit() else ''
        err = f"{e['errno']}/sev{e['sev']} {e['msg']}" if e['errno'] else ''
        lines.append(f"{e['ts']:<24} {e['ev'][:20]:<20} {e['db'][:21]:<21} {e['spid']:>5}  {e['what'][:39]:<39} {e['res'][:10]:<10} {e['rows']:>5} {ms:>5}  {err}")
    lines.append('')
    def names(e):
        return e['what'].removeprefix('batch:').split(',')
    seen = {p: [e for e in events if p in names(e)] for p in LOBBY_PROCS}
    game_events = [e for e in events if e['db'] == 'RustyHearts']
    errors = [e for e in events if e['ev'] == 'error_reported']
    attentions = [e for e in events if e['ev'] == 'attention']
    lines.append('=== Open SQL connections at read time (database | program | client pid | count) ===')
    for c in conns:
        lines.append('  ' + ' | '.join(c))
    game_conns = [c for c in conns if c[0] == 'RustyHearts']
    lines.append(f'  connections to game database RustyHearts: {sum(int(c[3]) for c in game_conns if c[3].isdigit())}')
    logins = [e for e in events if e['ev'] == 'login']
    by = {}
    for e in logins:
        by[e['db'] or '?'] = by.get(e['db'] or '?', 0) + 1
    lines.append('  logins during trace by database: ' + (', '.join(f'{k}={v}' for k, v in sorted(by.items())) or 'none'))
    lines.append('')
    lines.append('=== Verdict inputs ===')
    for p, hits in seen.items():
        status = 'NOT CALLED' if not hits else ', '.join(f"{h['ts'][11:]} {h['res'] or h['ev']} rows={h['rows']}" for h in hits[:4])
        lines.append(f'  {p:<28} {status}')
    lines.append(f'  game-database (RustyHearts) events: {len(game_events)}')
    lines.append(f'  SQL errors (severity>10): {len(errors)}   client attentions/timeouts: {len(attentions)}')
    lines.append('')
    if not seen['up_save_server_info']:
        lines.append('VERDICT: the lobby-stage auth write (up_save_server_info) did not run in this window - '
                     'either the attempt was outside the window or the lobby never reached the DB step.')
    elif not game_events:
        lines.append('VERDICT: lobby auth reached SQL, but NO game-database query followed. The lobby/DBC never '
                     'requested the character list. Next step: debugger on the lobby DB-result handler, not SQL.')
    elif errors or attentions or any(h['res'] not in ('OK', '') for hs in seen.values() for h in hs):
        lines.append('VERDICT: game-database queries ran but errored or timed out - see the rows above. '
                     'This is a DB/ADO problem, not a lobby protocol problem.')
    elif seen['up_get_all_character']:
        lines.append('VERDICT: character list query ran and succeeded in SQL. The stall is after the DB '
                     '(DBC->lobby reply or lobby->client send). Next step: debugger on DBC/lobby reply path.')
    else:
        lines.append('VERDICT: some game-database queries ran, but not the character list. Compare the last '
                     'game-DB call above with the expected login sequence.')
    return '\n'.join(lines) + '\n'


def cmd_start():
    out = sqlcmd(START_SQL)
    print(redact(out).strip())


def cmd_read():
    meta, events = parse(sqlcmd(READ_SQL, timeout=120))
    text = report(meta, events)
    os.umask(0o077)
    folder = ROOT / 'logs/diagnostics'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ('lobby-db-trace-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.txt')
    path.write_text(text)
    print(text)
    print(f'Saved: {path}')


def cmd_stop():
    print(redact(sqlcmd(STOP_SQL)).strip())


def wait_for_enter(seconds):
    if sys.stdin.isatty():
        print(f'Press Enter after the attempt has failed (auto-continues after {seconds}s)...', flush=True)
        select.select([sys.stdin], [], [], seconds)
        if select.select([sys.stdin], [], [], 0)[0]:
            sys.stdin.readline()
    else:
        print(f'No terminal input; capturing for {seconds}s.', flush=True)
        select.select([], [], [], seconds)


def cmd_run(seconds):
    cmd_start()
    try:
        print('\nTrace armed. In the game: click the "Ragezone" world now and wait until the\n'
              'waiting dialog ends or the game returns to server select (about 15 seconds).\n', flush=True)
        wait_for_enter(seconds)
        cmd_read()
        diag = ROOT / 'docker/diagnostics.py'
        if diag.exists():
            subprocess.run([sys.executable, str(diag)])
    finally:
        cmd_stop()


if __name__ == '__main__':
    args = sys.argv[1:]
    seconds = 300
    if '--seconds' in args:
        i = args.index('--seconds'); seconds = int(args[i + 1]); del args[i:i + 2]
    action = args[0] if args else 'run'
    {'start': cmd_start, 'read': cmd_read, 'stop': cmd_stop}.get(action, lambda: cmd_run(seconds))()
