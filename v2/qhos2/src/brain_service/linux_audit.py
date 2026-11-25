"""Local, read-only host evidence. No user text becomes a command or path."""
import os
from pathlib import Path
import platform
import shutil
import subprocess
import time

UNITS = ('qhos2-brain.service', 'qhos2-coletor.service', 'qhos2-brain-voice.service')


def collect_linux_audit():
    result = {'id': 'L1', 'observed_at': time.time(), 'scope': 'Linux que hospeda o Brain',
              'read_only': True, 'available': platform.system() == 'Linux', 'errors': [],
              'sources': ['/proc/loadavg', '/proc/meminfo', '/proc/uptime', 'statvfs(/)', 'systemctl show: unidades QHOS']}
    if not result['available']:
        result['errors'].append('host_not_linux')
        return result
    try:
        result['load_average'] = list(os.getloadavg())
        result['logical_cpus'] = os.cpu_count()
        result['uptime_seconds'] = float(Path('/proc/uptime').read_text()[:100].split()[0])
        memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text()[:8192].splitlines() if ':' in line)
        result['memory_kib'] = {key: int(memory[key].split()[0]) for key in ('MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree')}
        disk = shutil.disk_usage('/')
        result['disk_root_bytes'] = {'total': disk.total, 'used': disk.used, 'free': disk.free}
    except (OSError, ValueError, KeyError) as exc:
        result['errors'].append(type(exc).__name__)
    result['services'] = {}
    for unit in UNITS:
        try:
            proc = subprocess.run(['/usr/bin/systemctl', '--user', 'show', unit, '--no-pager',
                                   '--property=LoadState,ActiveState,SubState,MainPID,Result'],
                                  capture_output=True, text=True, timeout=2, check=False,
                                  env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'XDG_RUNTIME_DIR': '/run/user/'+str(os.getuid())})
            if proc.returncode:
                result['services'][unit] = {'available': False, 'reason': 'systemctl_unavailable'}
                result['errors'].append(unit + ': systemctl_unavailable')
            else:
                result['services'][unit] = dict(line.split('=', 1) for line in proc.stdout[:4096].splitlines() if '=' in line)
        except (OSError, subprocess.TimeoutExpired) as exc:
            result['services'][unit] = {'available': False, 'reason': type(exc).__name__}
            result['errors'].append(unit + ': ' + type(exc).__name__)
    result['complete'] = not result['errors']
    result['limits'] = ('Leitura pontual de recursos e unidades QHOS; não é auditoria de todo o SO nem prova de ausência de bugs. '
                        'LoadState=not-found significa unidade não instalada, não servidor parado: o Trader pode rodar como processo independente; conferir sua telemetria.')
    return result
