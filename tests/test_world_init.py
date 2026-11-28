"""M23 synthetic world-initialization, provenance harness, and negative-control tests.

Pure synthetic CI suite: requires only Python 3.10+ and a C11 compiler (zero
retail media, ELF, captures, or emulator needed). Keeps synthetic expectations
strictly distinct from retail equivalence.
"""
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / 'tools') not in sys.path:
    sys.path.insert(0, str(ROOT / 'tools'))

import verify_world_init as W


class TestWorldInitSynthetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = os.environ.get('CC', 'cc')
        cls.report = W.verify_synthetic(compiler=cls.compiler, sanitize=False)

    def test_synthetic_identity_and_non_retail_marker(self):
        self.assertEqual(self.report['schema'], 'hg-m23-synthetic-lifecycle/v1')
        self.assertTrue(self.report['synthetic_only'])
        self.assertFalse(self.report['retail_equivalence_claimed'])
        # Confirm independently authored synthetic spawn (not retail AREA1 48.5/38/265).
        self.assertEqual(self.report['scene']['spawn'], [12.0, 10.0, 20.0])

    def test_ordered_initialization_call_chain(self):
        expected = [f'{pc:08x}' for pc in W.EXPECTED_INIT_ORDER]
        self.assertEqual(self.report['ordered_init_chain'], expected)
        pcs = [row['pc'] for row in self.report['cold_entry_calls']]
        cursor = 0
        for pc in expected:
            idx = pcs.index(pc, cursor)
            cursor = idx + 1

    def test_provenance_classification_separates_fixture_c_and_model(self):
        ledger = self.report['provenance_ledger']
        self.assertGreater(ledger['fixture'], 0)
        self.assertGreater(ledger['c_produced'], 0)
        self.assertGreater(ledger['synthetic_model'], 0)
        self.assertEqual(ledger['unknown'], 0)
        by_label = {row['label']: row for row in self.report['cold_entry_provenance']}
        self.assertEqual(by_label['fixture_spawn_table']['provenance'], 'fixture')
        self.assertEqual(by_label['published_handle_0x3f']['provenance'], 'c-produced')
        self.assertEqual(by_label['published_handle_0x96']['provenance'], 'c-produced')
        self.assertEqual(by_label['cleared_title_handle_0x22f']['value'], '00000000')
        self.assertEqual(by_label['player_node_count_003f93b4']['provenance'], 'c-produced')
        self.assertEqual(by_label['world_root_actor']['provenance'], 'c-produced')

    def test_connected_synthetic_progression_and_clean_reinit(self):
        c1 = self.report['cycle1']
        c2 = self.report['cycle2_parity']
        # Fade completed over a realistic multi-frame ramp, then neutral held spawn.
        self.assertGreaterEqual(c1['fade_frames'], 60)
        self.assertEqual(c1['neutral_pos'], [12.0, 10.0, 20.0])
        # Consecutive forward walk advanced Z while keeping grounded Y = 10.0.
        self.assertGreater(c1['walk_pos'][2], 23.0)
        self.assertAlmostEqual(c1['walk_pos'][1], 10.0, places=4)
        # Consecutive turn + walk rotated yaw and advanced X & Z.
        self.assertGreater(c1['turn_yaw'], 0.35)
        self.assertGreater(c1['turn_pos'][0], 12.5)
        # Sustained walk triggered real hg_player_static_constraint wall contacts.
        self.assertGreater(c1['wall_contacts'], 0)
        self.assertGreater(c1['floor_queries'], 50)
        # Camera carried eye = player_pos + (0, 16.5, 0) cleanly through release.
        self.assertAlmostEqual(c1['release_camera_eye'][0], c1['release_pos'][0], places=3)
        self.assertAlmostEqual(c1['release_camera_eye'][1], c1['release_pos'][1] + 16.5, places=3)
        self.assertAlmostEqual(c1['release_camera_eye'][2], c1['release_pos'][2], places=3)
        # Teardown restored node pool cleanly (2028 -> 2048) and Cycle 2 matched bit-exactly.
        self.assertEqual(c1['free_nodes_active'], 2028)
        self.assertEqual(c1['free_nodes_after_teardown'], 2048)
        self.assertTrue(c2['bit_exact_trajectory_match'])
        self.assertEqual(c2['free_nodes_active'], 2028)
        self.assertEqual(c2['free_nodes_after_teardown'], 2048)
        self.assertEqual(c2['main_actors_active'], 1)
        self.assertEqual(c2['type2_actors_active'], 3)
        self.assertGreater(c2['counters_release']['3fa190'], c1['counters_release']['3fa190'])

    def test_all_ten_negative_controls_detected(self):
        controls = self.report['negative_controls']
        self.assertEqual(len(controls), 10)
        by_name = {c['name']: c for c in controls}
        for name, row in by_name.items():
            self.assertTrue(row['passed'], f'Control {name} failed: {row}')
            self.assertEqual(row['actual'], row['expected'], f'Control {name} mismatch')
        # Omitting state-0 init must reproduce the exact 00158560 state-1 guard fault (0x30).
        self.assertEqual(by_name['omit-state0-player-init']['domain_fault'], 0x30)


if __name__ == '__main__':
    unittest.main()
