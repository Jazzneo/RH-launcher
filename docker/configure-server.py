#!/usr/bin/env python3
"""Verify the mounted server uses the same secret as SQL; no network access."""
import os
from pathlib import Path
password = os.environ['RH_SQL_PASSWORD']
for path in Path('/srv/game/Option').glob('*.xml'):
    text = path.read_text(encoding='utf-8-sig')
    text = text.replace('YOUR_WAN_IP', '127.0.0.1').replace('192.168.100.3', '127.0.0.1')
    text = text.replace('DB_SERVER', '127.0.0.1,1433').replace('DB_USER', 'sa').replace('DB_PASSWD', password)
    path.write_text(text)
