"""Network play (RH Launcher > Settings > Network play).

runtime/rhlauncher/network.conf:
  MODE=local   only this PC plays (default; everything on 127.0.0.1)
  MODE=host    this PC runs the server for other devices on the home network
  MODE=join    this device only runs the game and plays on another PC's server (JOIN_IP)
  HOST_IP=     optional fixed address to announce in host mode (empty = detect it at every start)
"""
import socket
from pathlib import Path

MODES = ('local', 'host', 'join')
# What other devices must reach on the host PC (TCP, plus the PvP UDP port).
CLIENT_TCP_PORTS = (3000, 50001, 50003, 50004, 50006, 50016)
CLIENT_UDP_PORTS = (52000,)


def lan_ip():
    """This PC's address on the home network (no traffic is sent)."""
    for target in ('192.168.0.1', '10.255.255.255', '172.16.0.1'):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((target, 9))
                ip = s.getsockname()[0]
                if ip and not ip.startswith('127.'):
                    return ip
        except OSError:
            pass
    return ''


def _path(root):
    return Path(root) / 'runtime/rhlauncher/network.conf'


def load(root):
    d = {'mode': 'local', 'host_ip_fixed': '', 'join_ip': ''}
    try:
        for line in _path(root).read_text().splitlines():
            k, _, v = line.partition('=')
            v = v.strip()
            if k == 'MODE' and v in MODES:
                d['mode'] = v
            elif k == 'HOST_IP':
                d['host_ip_fixed'] = v
            elif k == 'JOIN_IP':
                d['join_ip'] = v
    except OSError:
        pass
    d['public_ip'] = (d['host_ip_fixed'] or lan_ip() or '127.0.0.1') if d['mode'] == 'host' else '127.0.0.1'
    return d


def save(root, mode, host_ip_fixed='', join_ip=''):
    if mode not in MODES:
        raise ValueError(mode)
    p = _path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f'MODE={mode}\nHOST_IP={host_ip_fixed}\nJOIN_IP={join_ip}\n')
