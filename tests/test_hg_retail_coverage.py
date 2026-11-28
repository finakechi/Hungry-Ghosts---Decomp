"""Controls for the build-scoped coverage inventory without retail media."""

from hashlib import sha256
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

from hg_coverage_inventory import c_body
from hg_retail_coverage import Image, mapped_sources


class RetailCoverageTests(unittest.TestCase):
    def test_candidate_bytes_and_instruction_checks_are_deduplicated(self):
        data = bytes(range(16))
        image = Image('EE', sha256(data).hexdigest(), lambda low, size: data[low:low + size])
        ranges = [{'start': '00000000', 'end_exclusive': '0000000c'}]
        row = dict(entry='00000000', body_ranges=ranges, instructions=[{'address': '00000004', 'bytes': '04050607'}])
        packet = dict(schema='ps2-evidence/v1', functions=[row])
        image.packet(packet, 'original')
        image.packet(packet, 'duplicate')
        image.candidate(4, [{'start': '00000004', 'end_exclusive': '00000010'}])
        report = image.report({0: {'example'}}, [])
        self.assertEqual(report['denominator'], {'candidate_entries': 2, 'candidate_body_bytes': 16})
        self.assertEqual(report['numerator']['original_bytes_checked_in_candidates'], 4)
        self.assertIsNone(report['numerator']['recovered_original_instruction_bytes'])
        self.assertEqual(report['evidence_flags']['partial-recovery'], 1)
        self.assertEqual(report['evidence_flags']['reviewed'], 1)
        self.assertEqual(report['classifications']['unknown'], 1)
        with self.assertRaisesRegex(ValueError, 'original word mismatch'):
            image.check(4, b'\0' * 4, 'mutated')

    def test_overlay_identity_separates_the_same_guest_address(self):
        a = Image('AREA0', 'a', lambda low, size: b'a' * size)
        b = Image('AREA1', 'b', lambda low, size: b'b' * size)
        a.check(0xA87800, b'aaaa', 'AREA0')
        b.check(0xA87800, b'bbbb', 'AREA1')
        with self.assertRaisesRegex(ValueError, 'original word mismatch'):
            b.check(0xA87800, b'aaaa', 'AREA1')

    def test_export_and_address_annotation_cannot_promote_an_independent_model(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / 'src' / 'audio'
            audio.mkdir(parents=True)
            entry = '/* 0x002ab508 hg_audio_example */\nvoid hg_audio_example(void) {}\n'
            (audio / 'audio_engine.c').write_text(entry)
            (audio / 'audio_sequencer.c').write_text(entry.replace('example', 'synthetic'))
            scene = root / 'src' / 'scene'
            scene.mkdir()
            (scene / 'title_scene.c').write_text('/* 00141470: scene. */\nstatic void title_frame(void) {}\n')
            mapped = mapped_sources(root, {}, c_body)
            self.assertEqual(mapped, {
                0x2AB508: {'hg_audio_example': 'src/audio/audio_engine.c'},
                0x141470: {'title_frame': 'src/scene/title_scene.c'},
            })


if __name__ == '__main__':
    unittest.main()
