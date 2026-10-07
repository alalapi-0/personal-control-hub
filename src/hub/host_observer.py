"""Bounded Linux observations, independent of browser/request lifetimes.

Only trusted startup code supplies named filesystem paths. No client can select
paths, commands or services. Failed samples retain the last value as stale.
"""
from __future__ import annotations

import copy
import csv
import io
import json
import math
import os
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


class HostObserver:
    def __init__(self, filesystems, *, proc=Path('/proc'), interval=2, ttl=8,
                 clock=time.monotonic, wall=time.time, run=subprocess.run,
                 statvfs=os.statvfs, hwmon=Path('/sys/class/hwmon')):
        if not 0.1 <= interval <= 60 or not interval < ttl <= 300:
            raise ValueError('invalid sampling interval')
        self.filesystems = {name: Path(path) for name, path in filesystems.items()}
        if any(not path.is_absolute() for path in self.filesystems.values()):
            raise ValueError('absolute named filesystems required')
        self.proc, self.interval, self.ttl = Path(proc), interval, ttl
        self.clock, self.wall, self.run, self.statvfs = clock, wall, run, statvfs
        self._lock, self._stop = threading.Lock(), threading.Event()
        self._thread = None
        self._values, self._times, self._mounts = {}, {}, {}
        self._cpu = None
        self.hwmon = Path(hwmon)
        self._temperatures, self._gpu_ids, self._io = None, None, {}

    def start(self):
        if self._thread is not None:
            raise RuntimeError('observer already started')
        self._thread = threading.Thread(target=self._loop, name='hub-host-observer', daemon=False)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(5)
            if self._thread.is_alive():
                raise RuntimeError('host observer did not stop')

    def _loop(self):
        while not self._stop.is_set():
            self.sample()
            self._stop.wait(self.interval)

    def _command(self, args):
        result = self.run(args, capture_output=True, text=True, timeout=1, check=False)
        if result.returncode or len(result.stdout) > 16384:
            raise ValueError('COMMAND_UNAVAILABLE')
        return result.stdout

    def _put(self, key, value, unit, source, now, stamp):
        self._values[key] = {'value': value, 'unit': unit, 'source': source,
                             'observed_at': stamp, 'state': 'fresh', 'supported': True,
                             'reason': None, 'max_age_seconds': self.ttl}
        self._times[key] = now

    def _fail(self, key, unit, source, reason):
        if key in self._values and self._values[key]['value'] is not None:
            self._values[key].update(state='stale', reason=reason)
        else:
            self._values[key] = {'value': None, 'unit': unit, 'source': source,
                                 'observed_at': None, 'state': 'unavailable',
                                 'supported': False, 'reason': reason,
                                 'max_age_seconds': self.ttl}

    def _cpu_sample(self, now, stamp):
        lines = (self.proc / 'stat').read_text().splitlines()
        counts = [int(n) for n in lines[0].split()[1:9]]
        if not lines[0].startswith('cpu ') or len(counts) != 8 or min(counts) < 0:
            raise ValueError('CPU_UNAVAILABLE')
        total, idle = sum(counts), counts[3] + counts[4]
        cores = sum(line.split()[0][3:].isdigit() for line in lines if line.startswith('cpu'))
        self._put('cpu_cores', cores, 'cores', '/proc/stat', now, stamp)
        previous = self._cpu
        self._cpu = (total, idle, now)
        if previous is None:
            self._fail('cpu_usage', 'percent', '/proc/stat delta', 'FIRST_SAMPLE')
            return
        delta, idle_delta = total - previous[0], idle - previous[1]
        if now <= previous[2] or delta <= 0 or not 0 <= idle_delta <= delta:
            raise ValueError('CPU_COUNTER_CHANGED')
        self._put('cpu_usage', round(100 * (delta - idle_delta) / delta, 2),
                  'percent', '/proc/stat delta', now, stamp)

    def _memory_sample(self, now, stamp):
        fields = {}
        for line in (self.proc / 'meminfo').read_text().splitlines():
            parts = line.split()
            if parts[0].rstrip(':') in ('MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree'):
                if len(parts) != 3 or parts[2] != 'kB' or int(parts[1]) < 0:
                    raise ValueError('MEMORY_UNAVAILABLE')
                fields[parts[0].rstrip(':')] = int(parts[1]) * 1024
        if set(fields) != {'MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree'}:
            raise ValueError('MEMORY_UNAVAILABLE')
        if fields['MemAvailable'] > fields['MemTotal'] or fields['SwapFree'] > fields['SwapTotal']:
            raise ValueError('MEMORY_UNAVAILABLE')
        self._put('memory', {'total': fields['MemTotal'], 'available': fields['MemAvailable']},
                  'bytes', '/proc/meminfo MemAvailable', now, stamp)
        self._put('swap', {'total': fields['SwapTotal'], 'free': fields['SwapFree']},
                  'bytes', '/proc/meminfo', now, stamp)

    def _disk_sample(self, name, path, now, stamp):
        if not path.is_dir() or path.is_symlink():
            raise ValueError('FILESYSTEM_UNAVAILABLE')
        listing = json.loads(self._command(['findmnt', '--json', '--target', str(path),
                                          '--output', 'SOURCE,TARGET,FSTYPE']))
        rows = listing['filesystems']
        if len(rows) != 1 or any(not isinstance(rows[0].get(k), str) for k in ('source', 'target', 'fstype')):
            raise ValueError('FILESYSTEM_UNAVAILABLE')
        mount = {key: rows[0][key] for key in ('source', 'target', 'fstype')}
        mount['device'] = path.stat().st_dev
        if name in self._mounts and self._mounts[name] != mount:
            raise ValueError('FILESYSTEM_CHANGED')
        stats = self.statvfs(path)
        self._mounts[name] = mount
        self._put('disk_' + name, {'total': stats.f_blocks * stats.f_frsize,
                                  'available': stats.f_bavail * stats.f_frsize,
                                  'mount': mount}, 'bytes', 'findmnt + statvfs:' + name, now, stamp)

    def _service_sample(self, now, stamp):
        output = self._command(['systemctl', '--user', 'show', 'personal-control-hub.service',
                                '--property=ActiveState,SubState,MainPID', '--no-pager'])
        values = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
        active, sub = values['ActiveState'], values['SubState']
        if active not in {'active', 'inactive', 'failed', 'activating', 'deactivating', 'reloading'}:
            raise ValueError('SERVICE_UNAVAILABLE')
        if not sub.isascii() or not sub.replace('-', '').isalnum() or len(sub) > 40:
            raise ValueError('SERVICE_UNAVAILABLE')
        pid = int(values['MainPID'])
        if pid < 0:
            raise ValueError('SERVICE_UNAVAILABLE')
        self._put('hub_service', {'active': active, 'sub': sub, 'pid': pid},
                  'state', 'systemctl --user show personal-control-hub.service', now, stamp)

    def _temperature_sample(self, now, stamp):
        sensors = []
        for device in sorted(self.hwmon.glob('hwmon*'))[:16]:
            name = (device / 'name').read_text().strip()
            if name not in {'k10temp', 'coretemp'}:
                continue
            for path in sorted(device.glob('temp*_input'))[:16]:
                value = int(path.read_text().strip()) / 1000
                if not -40 <= value <= 150:
                    raise ValueError('SENSOR_UNAVAILABLE')
                label_path = path.with_name(path.name.replace('_input', '_label'))
                label = label_path.read_text().strip() if label_path.exists() else path.stem
                if not label or len(label) > 80:
                    raise ValueError('SENSOR_UNAVAILABLE')
                sensors.append({'name': name, 'label': label, 'value': value, 'path': str(path)})
        if not sensors:
            raise ValueError('SENSOR_UNAVAILABLE')
        identity = [(s['name'], s['label'], str(Path(s['path']).resolve())) for s in sensors]
        if self._temperatures is not None and identity != self._temperatures:
            raise ValueError('SENSOR_CHANGED')
        self._temperatures = identity
        self._put('cpu_temperature', sensors, 'celsius', 'CPU hwmon k10temp/coretemp', now, stamp)

    def _gpu_sample(self, now, stamp):
        fields = 'uuid,name,memory.total,memory.used,utilization.gpu,temperature.gpu,utilization.encoder,utilization.decoder'
        output = self._command(['nvidia-smi', '--query-gpu=' + fields, '--format=csv,noheader,nounits'])
        rows = list(csv.reader(io.StringIO(output), skipinitialspace=True))
        if not 1 <= len(rows) <= 8:
            raise ValueError('GPU_UNAVAILABLE')
        metrics = {key: [] for key in ('gpu_memory', 'gpu_usage', 'gpu_temperature', 'gpu_encode', 'gpu_decode')}
        def numeric(raw, limit):
            if raw.strip() in {'N/A', '[N/A]', 'Not Supported', '[Not Supported]'}:
                return None
            n = float(raw)
            if not math.isfinite(n) or not 0 <= n <= limit:
                raise ValueError('GPU_UNAVAILABLE')
            return n
        for row in rows:
            if len(row) != 8:
                raise ValueError('GPU_UNAVAILABLE')
            uid, name = row[:2]
            if not uid.startswith('GPU-') or len(uid) > 80 or not name or len(name) > 120:
                raise ValueError('GPU_UNAVAILABLE')
            total, used = numeric(row[2], 1048576), numeric(row[3], 1048576)
            if total is None or used is None or not 0 <= used <= total or total <= 0:
                raise ValueError('GPU_UNAVAILABLE')
            values = [ {'total': int(total * 1024**2), 'used': int(used * 1024**2)},
                       numeric(row[4],100),numeric(row[5],150),numeric(row[6],100),numeric(row[7],100)]
            for key, value in zip(metrics, values):
                metrics[key].append({'id': uid, 'name': name, 'value': value})
        identity = sorted(item['id'] for item in metrics['gpu_memory'])
        if len(set(identity)) != len(identity) or (self._gpu_ids is not None and self._gpu_ids != identity):
            raise ValueError('GPU_CHANGED')
        self._gpu_ids = identity
        for key, values in metrics.items():
            unit = 'bytes' if key == 'gpu_memory' else 'celsius' if key == 'gpu_temperature' else 'percent'
            if all(item['value'] is None for item in values):
                self._fail(key, unit, 'nvidia-smi --query-gpu:' + fields, 'UNSUPPORTED_SENSOR')
            else:
                self._put(key, values, unit, 'nvidia-smi --query-gpu:' + fields, now, stamp)

    def _io_sample(self, name, now, stamp):
        mount = self._mounts[name]
        raw = (self.proc / 'diskstats').read_text()
        if len(raw) > 131072:
            raise ValueError('IO_UNAVAILABLE')
        device = mount['device']
        rows = [line.split() for line in raw.splitlines()]
        rows = [r for r in rows if len(r) >= 14 and (int(r[0]), int(r[1])) == (os.major(device), os.minor(device))]
        if len(rows) != 1:
            raise ValueError('IO_UNAVAILABLE')
        row = rows[0]; reads, writes = int(row[5]), int(row[9])
        if min(reads,writes) < 0:
            raise ValueError('IO_UNAVAILABLE')
        previous = self._io.get(name); self._io[name] = (device, reads, writes, now)
        if previous is None:
            self._fail('io_' + name, 'bytes_per_second', '/proc/diskstats named device delta', 'FIRST_SAMPLE')
            return
        elapsed = now - previous[3]
        if previous[0] != device or elapsed <= 0 or reads < previous[1] or writes < previous[2]:
            raise ValueError('IO_COUNTER_CHANGED')
        # Kernel diskstats sectors are 512-byte accounting units, independently
        # of the physical sector size. This is device/partition I/O, not per-path.
        self._put('io_' + name, {'read': (reads - previous[1]) * 512 / elapsed,
                                'write': (writes - previous[2]) * 512 / elapsed,
                                'device_name': row[2], 'mount': mount, 'interval_seconds': elapsed},
                  'bytes_per_second', '/proc/diskstats major/minor bound to:' + name, now, stamp)

    def sample(self):
        now = self.clock()
        stamp = datetime.fromtimestamp(self.wall(), timezone.utc).isoformat()
        # Single collector owns mutable sampling state. Snapshot reads never run
        # subprocesses or touch host paths and return a detached projection.
        with self._lock:
            try:
                self._cpu_sample(now, stamp)
            except (OSError, ValueError, IndexError):
                self._cpu = None
                for key, unit in (('cpu_usage', 'percent'), ('cpu_cores', 'cores')):
                    self._fail(key, unit, '/proc/stat', 'CPU_UNAVAILABLE')
            try:
                self._memory_sample(now, stamp)
            except (OSError, ValueError, IndexError):
                for key in ('memory', 'swap'):
                    self._fail(key, 'bytes', '/proc/meminfo', 'MEMORY_UNAVAILABLE')
            for name, path in self.filesystems.items():
                try:
                    self._disk_sample(name, path, now, stamp)
                except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
                    reason = 'FILESYSTEM_CHANGED' if str(error) == 'FILESYSTEM_CHANGED' else 'FILESYSTEM_UNAVAILABLE'
                    self._fail('disk_' + name, 'bytes', 'findmnt + statvfs:' + name, reason)
                    self._fail('io_' + name, 'bytes_per_second', '/proc/diskstats named device delta', reason)
                    continue
                try:
                    self._io_sample(name, now, stamp)
                except (OSError, ValueError, KeyError, TypeError) as error:
                    # Unsupported I/O does not invalidate independently read capacity.
                    self._fail('io_' + name, 'bytes_per_second', '/proc/diskstats named device delta', str(error) if str(error) in {'FILESYSTEM_CHANGED','IO_COUNTER_CHANGED'} else 'IO_UNAVAILABLE')
            try:
                self._temperature_sample(now, stamp)
            except (OSError, ValueError) as error:
                self._fail('cpu_temperature', 'celsius', 'CPU hwmon k10temp/coretemp',
                           'SENSOR_CHANGED' if str(error)=='SENSOR_CHANGED' else 'SENSOR_UNAVAILABLE')
            try:
                self._gpu_sample(now, stamp)
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                for key in ('gpu_memory','gpu_usage','gpu_temperature','gpu_encode','gpu_decode'):
                    unit = 'bytes' if key == 'gpu_memory' else 'celsius' if key == 'gpu_temperature' else 'percent'
                    self._fail(key,unit,'nvidia-smi query','GPU_CHANGED' if str(error)=='GPU_CHANGED' else 'GPU_UNAVAILABLE')
            try:
                self._service_sample(now, stamp)
            except (OSError, ValueError, KeyError, subprocess.SubprocessError):
                self._fail('hub_service', 'state', 'systemctl --user show personal-control-hub.service', 'SERVICE_UNAVAILABLE')
            self._put('sample_duration', round(max(0,self.clock()-now)*1000,2),
                      'milliseconds', 'collector monotonic elapsed wall time', now, stamp)

    def snapshot(self):
        with self._lock:
            values = copy.deepcopy(self._values)
            now = self.clock()
            for key, value in values.items():
                age = max(0, now - self._times[key]) if key in self._times else None
                value['age_seconds'] = round(age, 2) if age is not None else None
                if value['state'] == 'fresh' and age is not None and age > self.ttl:
                    value.update(state='stale', reason='SAMPLING_EXPIRED')
            return {'available': bool(values), 'interval_seconds': self.interval,
                    'metrics': values, 'capacity_recommendation': None,
                    'capacity_reason': 'NO_MEASURED_TASK_PROFILES', 'initial_sample_target_ms': 250}
