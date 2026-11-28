"""M26 read-only reference timing safety and committed-evidence tests."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools' / 'hg_audio'))
import m26_reference_timing as ref  # noqa: E402

EVIDENCE = ROOT / 'evidence' / 'm26' / 'reference-timing.json'


class M26ReferenceTimingSafetyTests(unittest.TestCase):
    def test_rejects_unauthenticated_elf(self):
        with tempfile.NamedTemporaryFile() as fake:
            fake.write(b'\0' * 4096)
            fake.flush()
            with self.assertRaises(SystemExit):
                ref.scan(fake.name)

    def test_committed_evidence_is_sanitized_and_consistent(self):
        data = json.loads(EVIDENCE.read_text(encoding='utf-8'))
        self.assertEqual(data['schema'], 'hg-m26-reference-timing/v1')
        self.assertEqual(data['elf']['elf_sha256'], ref.ELF_SHA256)
        c = data['elf']['constants']
        self.assertEqual(c['scheduler_tick_hz_word'], 60)
        self.assertEqual(c['i_wakeup_thread_syscall'], -52)
        self.assertEqual(c['slot3_retire_min_age_wakes'], 3)
        self.assertEqual(c['slot3_retire_envx_lt'], 2)
        self.assertEqual(c['slot3_mask_enqueue_order'], [9, 10, 7, 8])
        sc = data['saved_checkpoints']
        self.assertEqual(sc['checkpoint_count'], 12)
        self.assertTrue(sc['all_diffs_equal_one'])
        self.assertTrue(sc['all_slots_match_wake'])
        attr = sc['cursor_attribution']
        self.assertEqual(attr['voice_8_owner_at_cursor_up'], 0)
        self.assertEqual(attr['voice_9_owner_at_cursor_up'], 1)
        self.assertEqual(attr['voice_10_owner_at_cursor_up'], 1)
        self.assertTrue(attr['voice_6_7_8_idle_at_confirm'])
        self.assertTrue(attr['voice_16_17_releasing_at_confirm'])
        # Ensure no private absolute home paths leaked into tracked evidence.
        raw = EVIDENCE.read_text(encoding='utf-8')
        self.assertNotIn('/home/', raw)
        self.assertNotIn('/Users/', raw)


if __name__ == '__main__':
    unittest.main()
