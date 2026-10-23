"""Server memory levels (RH Launcher > Settings > Server memory).

The choice is stored in runtime/rhlauncher/memory.conf (PROFILE=auto|normal|low|minimum).
prepare.py writes the container limits into docker/.env (compose.yaml reads them), restore.py sets
SQL Server's own memory cap, and preflight.py uses the free-memory rule.
"""
from pathlib import Path

PROFILES = {
    # SQL Server memory cap (MB); container memory caps (*_mem) and memory+swap caps (*_swap); free memory a
    # start needs (GiB); guard_gib: the launcher stops everything when free memory drops below it. On Low/Minimum the containers may use swap instead of being killed when they hit
    # their memory cap (the ~14 Windows server programs under Wine need several GB while loading).
    'normal':  {'label': 'Normal (PC with 32 GB+)', 'sql_limit_mb': 2048,
                'sql_mem': '3g', 'sql_swap': '3g', 'api_mem': '512m', 'api_swap': '512m',
                'game_mem': '8g', 'game_swap': '8g', 'start_gib': 16, 'ready_gib': 10, 'guard_gib': 2},
    'low':     {'label': 'Low (Steam Deck, 16 GB PC)', 'sql_limit_mb': 1024,
                'sql_mem': '1536m', 'sql_swap': '3g', 'api_mem': '384m', 'api_swap': '768m',
                'game_mem': '6g', 'game_swap': '12g', 'start_gib': 5, 'ready_gib': 2, 'guard_gib': 1},
    'minimum': {'label': 'Minimum (tightest; slower, uses swap)', 'sql_limit_mb': 768,
                'sql_mem': '1g', 'sql_swap': '2g', 'api_mem': '384m', 'api_swap': '768m',
                'game_mem': '4g', 'game_swap': '12g', 'start_gib': 4, 'ready_gib': 2, 'guard_gib': 0.75},
}
CHOICES = ['auto', 'normal', 'low', 'minimum']


def total_gib():
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemTotal:'):
                return int(line.split()[1]) / 1024 / 1024
    except OSError:
        pass
    return 32


def choice(root):
    try:
        for line in (Path(root) / 'runtime/rhlauncher/memory.conf').read_text().splitlines():
            if line.startswith('PROFILE='):
                v = line[8:].strip()
                if v in CHOICES:
                    return v
    except OSError:
        pass
    return 'auto'


def resolve(name):
    """'auto' becomes 'low' on machines with less than 20 GiB, else 'normal'."""
    if name == 'auto':
        return 'low' if total_gib() < 20 else 'normal'
    return name


def load(root):
    name = resolve(choice(root))
    return dict(PROFILES[name], name=name)


def save(root, name):
    if name not in CHOICES:
        raise ValueError(name)
    p = Path(root) / 'runtime/rhlauncher/memory.conf'
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f'PROFILE={name}\n')
