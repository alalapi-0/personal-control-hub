"""Pure unit rendering: no installation or user-manager effects."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from hub.systemd_refresh import render_units
from hub.refresh_schedule import _elapsed_seconds

class IncrementalScheduleTests(unittest.TestCase):
    def test_one_named_service_minute_and_daily_calendars(self):
        units=render_units(Path('/synthetic/hub'),Path('/code'),Path('/usr/bin/python3'),
            incremental=True,unit_prefix='pch-opt-synthetic-test')
        self.assertEqual(set(units),{'pch-opt-synthetic-test.service','pch-opt-synthetic-test.timer'})
        service=units['pch-opt-synthetic-test.service'];timer=units['pch-opt-synthetic-test.timer']
        self.assertIn('sync --scheduled',service)
        self.assertIn('OnCalendar=*-*-* 09:00:00 Asia/Tokyo',timer)
        self.assertIn('OnCalendar=*-*-* 21:00:00 Asia/Tokyo',timer)
        self.assertNotIn('*:*:00',timer)
        self.assertIn('AccuracySec=1min',timer)
        self.assertIn('Unit=pch-opt-synthetic-test.service',timer)
        self.assertNotIn('Restart=',service)
    def test_24_hour_calendar_has_only_anchor_and_no_background_runtime(self):
        units=render_units(Path('/hub'),Path('/code'),Path('/bin/python'),interval_hours=24)
        combined=''.join(units.values())
        self.assertEqual(combined.count('OnCalendar='),1)
        self.assertNotIn('21:00',combined)
        self.assertIn('Persistent=true',combined)
        for forbidden in ('--incremental','*:*:00','Restart=','browser','agent','mcp'):
            self.assertNotIn(forbidden,combined)
        for invalid in (True,6,'12'):
            with self.assertRaises(ValueError):render_units(Path('/hub'),Path('/code'),Path('/bin/python'),interval_hours=invalid)

    def test_explicit_other_daily_time_cannot_silently_use_nine(self):
        with self.assertRaises(ValueError):
            render_units(Path('/hub'),Path('/code'),Path('/bin/python'),incremental=True,time='10:00')
        for prefix in ('../other','bad\nunit','other.service'):
            with self.assertRaises(ValueError):
                render_units(Path('/hub'),Path('/code'),Path('/bin/python'),unit_prefix=prefix)
    def test_elapsed_and_unknown_timestamps(self):
        start='Fri 2026-10-16 09:00:00 JST'
        self.assertEqual(_elapsed_seconds(start,'Fri 2026-10-16 09:01:01 JST'),61)
        self.assertIsNone(_elapsed_seconds(start,'Fri 2026-10-16 09:01:01 UTC'))
        self.assertIsNone(_elapsed_seconds('n/a',start))

if __name__=='__main__':unittest.main()
