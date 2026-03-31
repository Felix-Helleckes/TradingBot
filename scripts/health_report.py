#!/usr/bin/env python3
import json
import os
from pathlib import Path
from datetime import datetime

REPORT_DIR = Path('/home/felix/TradingBot/reports/sim')
OUT = Path('/home/felix/TradingBot/reports/health_summary.txt')


def _read_pi_temperature():
    """Read Raspberry Pi CPU temperature from sysfs. Returns float °C or None."""
    temp_path = Path('/sys/class/thermal/thermal_zone0/temp')
    try:
        if temp_path.exists():
            return int(temp_path.read_text().strip()) / 1000.0
    except Exception:
        pass
    return None


def _read_pi_throttle_flags():
    """Read Pi throttle flags via vcgencmd. Returns dict or None."""
    try:
        import subprocess
        result = subprocess.run(
            ['vcgencmd', 'get_throttled'],
            capture_output=True, text=True, timeout=3
        )
        raw = result.stdout.strip()  # e.g. "throttled=0x0"
        if '=' in raw:
            val = int(raw.split('=')[1], 16)
            return {
                'currently_throttled': bool(val & 0x4),
                'under_voltage': bool(val & 0x1),
                'arm_freq_capped': bool(val & 0x2),
                'soft_temp_limit': bool(val & 0x8),
            }
    except Exception:
        pass
    return None


def _read_memory_info():
    """Return dict with total/available/free memory in MB from /proc/meminfo (Linux only)."""
    meminfo = Path('/proc/meminfo')
    if not meminfo.exists():
        return {}
    info = {}
    try:
        for line in meminfo.read_text().splitlines():
            parts = line.split()
            if parts[0] in ('MemTotal:', 'MemAvailable:', 'MemFree:', 'SwapTotal:', 'SwapFree:'):
                info[parts[0].rstrip(':')] = int(parts[1]) // 1024  # kB -> MB
    except Exception:
        pass
    return info


now = datetime.utcnow().isoformat()
summary = {'generated': now, 'runs': []}

for f in sorted(REPORT_DIR.glob('*.json')):
    try:
        j = json.load(open(f))
    except Exception:
        continue
    summary['runs'].append({
        'file': str(f.name),
        'period_days': j.get('period_days'),
        'initial': j.get('initial_eur'),
        'final': j.get('final_eur'),
        'return_pct': j.get('return_pct'),
        'max_drawdown_pct': j.get('max_drawdown_pct'),
        'sharpe': j.get('metrics', {}).get('sharpe'),
        'calmar': j.get('metrics', {}).get('calmar'),
    })

OUT.parent.mkdir(parents=True, exist_ok=True)
with open(OUT, 'w') as fo:
    fo.write('Health Summary\n')
    fo.write('Generated: %s\n\n' % now)

    # --- Raspberry Pi system health ---
    fo.write('=== Raspberry Pi System Health ===\n')
    cpu_temp = _read_pi_temperature()
    if cpu_temp is not None:
        warn = ' *** THROTTLING RISK ***' if cpu_temp >= 65 else (' (warm)' if cpu_temp >= 55 else '')
        fo.write(f'  CPU Temperature : {cpu_temp:.1f} °C{warn}\n')
    else:
        fo.write('  CPU Temperature : n/a (not a Pi or sysfs unavailable)\n')

    throttle = _read_pi_throttle_flags()
    if throttle:
        fo.write(f'  Throttle Flags  : {throttle}\n')

    mem = _read_memory_info()
    if mem:
        fo.write(
            f'  Memory          : {mem.get("MemAvailable", "?")} MB free / '
            f'{mem.get("MemTotal", "?")} MB total | '
            f'Swap: {mem.get("SwapFree", "?")} / {mem.get("SwapTotal", "?")} MB\n'
        )
    fo.write('\n')

    # --- Backtest results ---
    fo.write('=== Backtest Results ===\n')
    for r in summary['runs']:
        fo.write(f"File: {r['file']}\n")
        fo.write(f"  Period days: {r['period_days']}\n")
        fo.write(f"  Initial: {r['initial']} Final: {r['final']} Return%: {r['return_pct']} MDD%: {r['max_drawdown_pct']}\n")
        fo.write(f"  Sharpe: {r['sharpe']} Calmar: {r['calmar']}\n\n")

print('Wrote', OUT)
