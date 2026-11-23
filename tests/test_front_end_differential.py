"""M18 synthetic front-end differential: original words from the published
original-byte packet (analysis/front_end: ELF rows, title-overlay rows and
jump/data tables) versus src/front_end.c. No retail data: the base image
holds only the packet words."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import front_end_differential as F  # noqa: E402
import verify_front_end_differential as V  # noqa: E402

# (name, anchor, replacement, entry): compiled defects the differential must reject.
CONTROLS = (
    ('fade-out-mode-1-kind-5', 'if(mode==1) {w32(e,0x700000d4,1);w8(e,FADE+3,4);}',
     'if(mode==1) {w32(e,0x700000d4,1);w8(e,FADE+3,5);}', 0x139040),
    ('fade-in-kind-3', 'w8(e,FADE+3,2);w8(e,FADE+2,blend);', 'w8(e,FADE+3,3);w8(e,FADE+2,blend);', 0x1390b0),
    ('pause-resume-next-state', 'pause_fade(e,0x11);\n                sound(e,0x52);',
     'pause_fade(e,0x12);\n                sound(e,0x52);', 0x162750),
    ('menu-cursor-upper-bound', '>=(int32_t)(limit&0xffffu))return;', '>(int32_t)(limit&0xffffu))return;',
     0x161cf0),
)


def bases(code):
    image = bytearray(0x2000000)
    image[0x100000:0x100000 + len(code)] = code
    return [('packet-image', bytes(image), V.scratch_with_mode(V.SCRATCH_BASE))]


class FrontEndDifferential(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code = F.packet_image()
        cls.bases = bases(cls.code)
        cls.lib = F.build(ROOT / 'local/build/m18-test-front-end.so')

    def test_every_function_matches_original(self):
        report = V.run(self.code, self.lib, self.bases, 400, 18)
        self.assertEqual(len(report), len(F.FUNCTIONS))
        for entry, row in report.items():
            with self.subTest(entry=entry):
                self.assertEqual(row['mismatches'], 0, row['failures'][:1])
                self.assertGreater(row['executed_words'], 0)

    def test_compiled_defects_are_rejected(self):
        source = (ROOT / 'src/front_end.c').read_text()
        out = ROOT / 'local/build/m18-test-controls'
        out.mkdir(parents=True, exist_ok=True)
        for name, old, new, entry in CONTROLS:
            with self.subTest(control=name):
                self.assertEqual(source.count(old), 1, 'anchor not unique')
                path = out / f'{name}.c'
                path.write_text(source.replace(old, new))
                lib = F.build(out / f'{name}.so', front_end=path)
                row = V.run(self.code, lib, self.bases, 400, 18, [entry])[f'{entry:08x}']
                self.assertGreater(row['mismatches'], 0)

    def test_native_runner_control_anchors_are_unique(self):
        import run_front_end_native as R
        for name, src, old, _new, _start in R.CONTROLS:
            if old is None:
                continue
            with self.subTest(control=name):
                self.assertEqual((ROOT / 'src' / src).read_text().count(old), 1)


if __name__ == '__main__':
    unittest.main()
