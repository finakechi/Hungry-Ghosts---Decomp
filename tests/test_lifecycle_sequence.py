"""M17 synthetic connected lifecycle sequence (packet image, no ELF).

Runs the C main init and main-loop frames through the reconstructed boot,
lifecycle and gameplay modes with the declared host models of
tools/run_lifecycle_sequence.py. This is an integration check of the C state
machines against each other, not a retail-equivalence claim."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import run_lifecycle_sequence as R  # noqa: E402
from lifecycle_differential import build, packet_image  # noqa: E402

FRAMES = 1500


def collapse(xs):
    out = []
    for x in xs:
        if not out or out[-1] != x:
            out.append(x)
    return out


class LifecycleSequence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = R.Runner(build(), packet_image()).run(FRAMES)
        cls.states = [(s['state']['handler'], s['state']['major'], s['state']['minor'], s['state']['sub'],
                       s['state']['area']) for s in cls.result['states']]

    def test_no_faults_and_counters(self):
        self.assertEqual(self.result['faults'], [])
        c = self.result['counters']
        self.assertEqual(c['main_loop'], FRAMES)
        self.assertEqual(c['vblank'], FRAMES)
        boot = next(s['frame'] for s in self.result['states'] if s['state']['handler'] == '00121be0')
        self.assertEqual(c['frame_tick'], FRAMES - boot - 1)   # boot mode does not tick gp+2f80

    def test_mode_handler_lifecycle(self):
        self.assertEqual(collapse(s[0] for s in self.states),
                         ['00121810', '00121be0', '0012d1c0', '00121be0', '0012d1c0'])

    def test_pause_quit_teardown(self):
        pause = [i for i, s in enumerate(self.states) if s[:4] == ('0012d1c0', 5, 1, 2)]
        teardown = [i for i, s in enumerate(self.states) if s[:3] == ('0012d1c0', 5, 4)]
        self.assertTrue(pause and teardown and pause[0] < teardown[0])
        self.assertEqual(self.states[teardown[0] + 1][0], '00121be0')

    def test_area_requests(self):
        area_loads = [r for r in self.result['requests'] if r['mode']['minor'] == 5 and r['mode']['major'] == 5]
        first = [(r['kind'], r['directory']) for r in area_loads if r['frame'] == area_loads[0]['frame']]
        # new game: AREA 0x51 for area 1 and PACK kind 2 / 0xc6, the request
        # observed at the M7 c6-entry runtime capture (triple (1,0,0)).
        self.assertEqual(first, [(1, '0x51'), (2, '0xc6')])
        self.assertEqual(self.states[-1][:4] + (self.states[-1][4],), ('0012d1c0', 5, 1, 1, 1))
        continue_loads = [r for r in area_loads if r['frame'] > area_loads[0]['frame']]
        self.assertEqual([(r['kind'], r['directory']) for r in continue_loads], [(1, '0x51'), (2, '0xc0')])

    def test_continue_requests_pause_data(self):
        self.assertIn((1, '0x69'), [(r['kind'], r['directory']) for r in self.result['requests']])


if __name__ == '__main__':
    unittest.main()
