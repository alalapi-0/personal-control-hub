import tempfile
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hub.systemd_refresh import SERVICE, TIMER, main, render_units


class DailyUnitsTests(unittest.TestCase):
    def test_default_calendar_and_direct_local_exec(self):
        units = render_units(Path('/workspace/hub'), Path('/runtime/version'), Path('/runtime/env/bin/python'))
        self.assertIn('OnCalendar=*-*-* 09:00:00 Asia/Tokyo', units[TIMER])
        self.assertIn('Persistent=true', units[TIMER])
        self.assertIn('Type=oneshot', units[SERVICE])
        self.assertIn('WorkingDirectory=/workspace/hub\n', units[SERVICE])
        self.assertIn('"/runtime/env/bin/python" "/runtime/version/scripts/hub_sync.py" --root "/workspace/hub" sync --daily', units[SERVICE])
        self.assertNotIn('personal-control-hub.service', ''.join(units.values()))
        self.assertNotIn('Restart=', units[SERVICE])

    def test_arguments_do_not_become_systemd_expansion_or_shell(self):
        text = render_units(Path('/space $x/%i/hub'), Path('/code path'), Path('/bin/python'))[SERVICE]
        self.assertIn('"/space $$x/%%i/hub"', text)
        self.assertIn('WorkingDirectory=/space $x/%%i/hub\n', text)
        for time, zone in [('25:00', 'Asia/Tokyo'), ('09:00\nExecStart=bad', 'Asia/Tokyo'), ('09:00', '../UTC')]:
            with self.subTest(time=time, zone=zone), self.assertRaises((ValueError, KeyError)):
                render_units(Path('/hub'), Path('/code'), Path('/bin/python'), time=time, timezone=zone)
        with self.assertRaises(ValueError):
            render_units(Path('/hub\nother'), Path('/code'), Path('/bin/python'))

    def test_install_refuses_existing_unit_and_does_not_control_manager(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            args = ['--root', '/hub', '--code-root', '/code', '--python', '/bin/python', '--unit-dir', directory]
            self.assertEqual(main(args), 0)
            previous = {name: (target / name).read_bytes() for name in (SERVICE, TIMER)}
            with self.assertRaises(SystemExit):
                main(args)
            self.assertEqual(previous, {name: (target / name).read_bytes() for name in previous})


if __name__ == '__main__':
    unittest.main()
