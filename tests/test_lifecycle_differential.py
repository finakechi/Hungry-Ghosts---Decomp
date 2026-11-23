"""M17 synthetic lifecycle differential: original words from the published
original-byte packet (analysis/game_lifecycle) versus src/game_lifecycle.c.
No retail data: the base image holds only the packet words and jump tables."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from lifecycle_differential import build, packet_image  # noqa: E402
import verify_lifecycle_differential as V  # noqa: E402


class LifecycleDifferential(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code = packet_image()
        ram = bytearray(0x2000000)
        ram[0x100000:0x100000 + len(cls.code)] = cls.code
        cls.bases = {'packet-image': (bytes(ram), bytes(0x4000))}
        cls.lib = build()

    def test_every_function_matches_original_with_full_reachable_coverage(self):
        report = V.run(self.code, self.lib, self.bases, 800, 17)
        self.assertEqual(len(report), 19)
        for entry, row in report.items():
            with self.subTest(entry=entry, function=row['function']):
                self.assertEqual(row['failures'], 0, row['first_failures'])
                self.assertEqual(row['executed_words'], row['words'], row['unexecuted'])

    def test_compiled_defects_are_rejected(self):
        import lifecycle_negative_controls as N
        source = N.SOURCE.read_text()
        out = Path(__file__).resolve().parents[1] / 'local/build/m17-test-controls'
        out.mkdir(parents=True, exist_ok=True)
        for name, old, new, target, defect in N.CONTROLS[:6]:
            with self.subTest(control=name):
                text = source if old is None else source.replace(old, new)
                path = out / f'{name}.c'
                path.write_text(text)
                lib = build(output=out / f'{name}.so', source=path)
                row = V.run(self.code, lib, self.bases, 300, 17, {target})[f'{target:08x}']
                self.assertEqual(row['failures'] > 0, defect, row['first_failures'][:1])


if __name__ == '__main__':
    unittest.main()
