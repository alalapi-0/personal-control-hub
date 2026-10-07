from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from hub.host_observer import HostObserver
from hub.local_service import HubHTTPServer


class HostObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.proc = self.root / 'proc'; self.proc.mkdir()
        self.now = 0
        self.mount = '/dev/fixture'
        self.failure = False
        self.calls = []
        self.gpu = 'GPU-fixture, Test GPU, 1024, 0, 0, 31, 0, N/A\n'
        self.hwmon=self.root/'hwmon';sensor=self.hwmon/'hwmon0';sensor.mkdir(parents=True)
        (sensor/'name').write_text('k10temp');(sensor/'temp1_label').write_text('Tctl');(sensor/'temp1_input').write_text('45000')
        self.write_proc(100, 800)
        self.observer = HostObserver({'hub': self.root}, proc=self.proc,
            clock=lambda: self.now, wall=lambda: 1000 + self.now, run=self.run_command,
            statvfs=lambda _: SimpleNamespace(f_blocks=100, f_bavail=30, f_frsize=1024),hwmon=self.hwmon)

    def write_proc(self, user, idle):
        (self.proc / 'stat').write_text(f'cpu {user} 0 0 {idle} 0 0 0 0 999 0\ncpu0 0\ncpu1 0\n')
        (self.proc / 'meminfo').write_text('MemTotal: 1024 kB\nMemAvailable: 512 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n')
        self.write_diskstats(100,200)

    def write_diskstats(self,reads,writes):
        device=self.root.stat().st_dev
        (self.proc/'diskstats').write_text(f'{os.major(device)} {os.minor(device)} fixture 1 0 {reads} 0 1 0 {writes} 0 0 0 0 0 0 0 0\n')

    def run_command(self, args, **kwargs):
        self.calls.append(args)
        self.assertEqual(kwargs['timeout'], 1)
        if self.failure:
            raise subprocess.TimeoutExpired(args, 1)
        if args[0] == 'findmnt':
            output = json.dumps({'filesystems': [{'source': self.mount, 'target': '/', 'fstype': 'ext4'}]})
        elif args[0]=='nvidia-smi':output=self.gpu
        else:
            self.assertEqual(args[3], 'personal-control-hub.service')
            output = 'ActiveState=active\nSubState=running\nMainPID=123\n'
        return SimpleNamespace(returncode=0, stdout=output)

    def test_cpu_interval_bytes_zero_and_snapshot_no_collection(self):
        self.observer.sample()
        first = self.observer.snapshot()['metrics']
        self.assertEqual(first['cpu_usage']['reason'], 'FIRST_SAMPLE')
        self.now = 2; self.write_proc(100, 900); self.observer.sample()
        metrics = self.observer.snapshot()['metrics']
        self.assertEqual(metrics['cpu_usage']['value'], 0)
        self.assertEqual(metrics['cpu_usage']['state'], 'fresh')
        self.assertEqual(metrics['cpu_cores']['value'], 2)
        self.assertEqual(metrics['memory']['value']['available'], 512 * 1024)
        self.assertEqual(metrics['swap']['value']['total'], 0)
        self.assertEqual(metrics['disk_hub']['value']['available'], 30 * 1024)
        self.assertEqual(metrics['hub_service']['value']['pid'], 123)
        self.assertIsNone(self.observer.snapshot()['capacity_recommendation'])
        calls = len(self.calls)
        metrics['memory']['value']['available'] = -1
        self.assertEqual(self.observer.snapshot()['metrics']['memory']['value']['available'], 524288)
        self.assertEqual(len(self.calls), calls)

    def test_failure_expiry_retains_original_value_and_timestamp(self):
        self.observer.sample(); self.now = 2; self.write_proc(120, 880); self.observer.sample()
        original = self.observer.snapshot()['metrics']
        self.failure = True; (self.proc / 'meminfo').unlink(); (self.proc / 'stat').unlink()
        self.now = 4; self.observer.sample()
        failed = self.observer.snapshot()['metrics']
        for key in ('memory', 'swap', 'cpu_usage', 'cpu_cores', 'disk_hub', 'hub_service'):
            self.assertEqual(failed[key]['value'], original[key]['value'])
            self.assertEqual(failed[key]['observed_at'], original[key]['observed_at'])
            self.assertEqual(failed[key]['state'], 'stale')
        self.failure = False; self.write_proc(120, 880); self.observer.sample()
        self.now = 13
        self.assertEqual(self.observer.snapshot()['metrics']['memory']['reason'], 'SAMPLING_EXPIRED')

    def test_missing_and_changed_filesystem_never_falls_back(self):
        self.observer.sample(); self.mount = '/dev/replaced'; self.now = 2; self.observer.sample()
        disk = self.observer.snapshot()['metrics']['disk_hub']
        self.assertEqual(disk['reason'], 'FILESYSTEM_CHANGED')
        self.assertEqual(disk['value']['mount']['source'], '/dev/fixture')
        missing = HostObserver({'missing': self.root / 'absent'}, proc=self.root / 'absent', run=self.run_command,hwmon=self.root/'absent')
        missing.sample()
        metrics = missing.snapshot()['metrics']
        self.assertIsNone(metrics['disk_missing']['value'])
        self.assertEqual(metrics['disk_missing']['state'], 'unavailable')
        self.assertIsNone(metrics['cpu_usage']['value'])

    def test_thread_is_independent_and_stops_without_request(self):
        observer = HostObserver({}, proc=self.proc, interval=.1, ttl=1, run=self.run_command,hwmon=self.hwmon)
        observer.start()
        try:
            deadline = time.monotonic() + 1
            while not observer.snapshot()['available'] and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(observer.snapshot()['available'])
        finally:
            observer.stop()
        self.assertFalse(observer._thread.is_alive())

    def test_gpu_sensor_zero_unsupported_failure_and_device_change(self):
        self.observer.sample();m=self.observer.snapshot()['metrics']
        self.assertEqual(m['gpu_memory']['value'][0]['value'],{'total':1024**3,'used':0})
        self.assertEqual(m['gpu_usage']['value'][0]['value'],0)
        self.assertEqual(m['gpu_decode']['reason'],'UNSUPPORTED_SENSOR');self.assertIsNone(m['gpu_decode']['value'])
        self.assertEqual(m['cpu_temperature']['value'][0]['value'],45)
        self.gpu=self.gpu.replace('GPU-fixture','GPU-replaced');self.now=2;self.observer.sample()
        changed=self.observer.snapshot()['metrics']['gpu_memory']
        self.assertEqual(changed['reason'],'GPU_CHANGED');self.assertEqual(changed['value'],m['gpu_memory']['value'])
        last_temperature=self.observer.snapshot()['metrics']['cpu_temperature']
        self.failure=True;(self.hwmon/'hwmon0'/'temp1_input').unlink();self.now=4;self.observer.sample()
        failed=self.observer.snapshot()['metrics'];self.assertEqual(failed['gpu_usage']['state'],'stale')
        self.assertEqual(failed['cpu_temperature']['value'],m['cpu_temperature']['value'])
        self.assertEqual(failed['cpu_temperature']['observed_at'],last_temperature['observed_at'])
        self.now=13;self.assertEqual(self.observer.snapshot()['metrics']['gpu_usage']['state'],'stale')

    def test_disk_delta_units_reset_mount_change_and_independent_capacity(self):
        self.observer.sample();self.now=2;self.write_diskstats(104,208);self.observer.sample()
        m=self.observer.snapshot()['metrics'];self.assertEqual(m['io_hub']['value']['read'],1024)
        self.assertEqual(m['io_hub']['value']['write'],2048)
        self.now=4;self.write_diskstats(1,1);self.observer.sample();m=self.observer.snapshot()['metrics']
        self.assertEqual(m['io_hub']['reason'],'IO_COUNTER_CHANGED');self.assertEqual(m['disk_hub']['state'],'fresh')
        # Identical sample timestamp must not conceal a changed mount.
        self.mount='/dev/other';self.observer.sample();m=self.observer.snapshot()['metrics']
        self.assertEqual(m['disk_hub']['reason'],'FILESYSTEM_CHANGED');self.assertEqual(m['io_hub']['reason'],'FILESYSTEM_CHANGED')
        self.assertEqual(m['io_hub']['value']['mount']['source'],'/dev/fixture')
        self.mount='/dev/fixture';self.now=6;self.write_diskstats(-1,-1);self.observer.sample()
        self.assertEqual(self.observer.snapshot()['metrics']['io_hub']['reason'],'IO_UNAVAILABLE')

    def test_missing_cpu_sensor_and_malformed_gpu_never_publish_zero(self):
        (self.hwmon/'hwmon0'/'name').write_text('unrelated');self.gpu='GPU-fixture, Test GPU, 1, 2, nan, 31, 0, 0\n'
        self.observer.sample();m=self.observer.snapshot()['metrics']
        self.assertIsNone(m['cpu_temperature']['value']);self.assertIsNone(m['gpu_memory']['value'])
        self.assertEqual(m['gpu_memory']['state'],'unavailable');self.assertIsNone(self.observer.snapshot()['capacity_recommendation'])

    def test_sensor_identity_change_preserves_old_reading_as_stale(self):
        self.observer.sample();first=self.observer.snapshot()['metrics']['cpu_temperature']
        (self.hwmon/'hwmon0'/'name').write_text('coretemp');self.now=2;self.observer.sample()
        changed=self.observer.snapshot()['metrics']['cpu_temperature']
        self.assertEqual(changed['reason'],'SENSOR_CHANGED');self.assertEqual(changed['value'],first['value'])
        self.assertEqual(changed['observed_at'],first['observed_at']);self.assertEqual(changed['state'],'stale')

    def test_host_http_is_session_scoped_snapshot_only(self):
        observer = mock.Mock()
        observer.snapshot.return_value = {'available': True, 'metrics': {'fixture': {'value': 0}}}
        server = HubHTTPServer(mock.Mock(), mock.Mock(), host_observer=observer)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}); thread.start()
        def get(path, cookie=None):
            conn = http.client.HTTPConnection(*server.server_address, timeout=2)
            try:
                conn.request('GET', path, headers={'Cookie': cookie} if cookie else {})
                response = conn.getresponse()
                return response.status, response.getheader('Set-Cookie'), json.loads(response.read())
            finally:
                conn.close()
        try:
            self.assertEqual(get('/api/host')[0], 401)
            observer.snapshot.assert_not_called()
            _, cookie, _ = get('/api/session'); cookie = cookie.split(';')[0]
            self.assertEqual(get('/api/host?path=/etc', cookie)[0], 400)
            observer.snapshot.assert_not_called()
            self.assertEqual(get('/api/host', cookie)[0], 200)
            observer.snapshot.assert_called_once_with()
            observer.sample.assert_not_called(); observer.run.assert_not_called()
            server.projects.assert_not_called(); server.designs.assert_not_called()
        finally:
            server.shutdown(); server.server_close(); thread.join(2)
        self.assertFalse(thread.is_alive())
