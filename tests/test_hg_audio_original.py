"""Original-data verification: authenticated audio members against M7 evidence.

Reads the two M7-verified members from the authenticated Japan ISO (SHA-256
8ca1513c...) and drives the reconstructed registration to completion,
comparing the resulting endpoint and bank-pointer effects with the
independently recorded M7 runtime evidence (docs/M7.md):
  6b/51 -> 01009000 -> endpoint 0100a740
  85/94 -> 00f78000 -> endpoint 00f78880
plus the six rebased pointer words at bank+0x30..+0x44.

Nothing is written to the ISO; raw member bytes stay private.
"""
import hashlib
import os
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools'))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools' / 'hg_audio'))
from hg_identify import ISO, read_at  # noqa: E402
from audio_guest import AudioHarness, SLOT_TABLE, ID_TABLE  # noqa: E402

ISO_PATH = Path(os.environ.get('HG_JAPAN_ISO',
                        str(Path(__file__).resolve().parent.parent.parent
                            / 'Hungry Ghosts (Japan).iso')))
ISO_SHA256 = '8ca1513c8417c0a40b838ff5edf22a2360f43bce1b0458693a95a6efdafd994e'
MEMBER_SHA = {
    (0x6b, 0x51): '3776b4159cacfe36e86648c459b99542e9a5ce29fc64dc8aa5111751ffc8284f',
    (0x85, 0x94): '142042dbd0ffe40be0d7f55272434759f247fdb5e099b4c67e8ccd6b447a789f',
}
M7_ENDPOINTS = {(0x6b, 0x51): 0x0100a740, (0x85, 0x94): 0x00f78880}
M7_LOADS = {(0x6b, 0x51): 0x01009000, (0x85, 0x94): 0x00f78000}


def _members():
    with open(ISO_PATH, 'rb') as fp:
        image = hashlib.sha256(fp.read(64 * 2048)).digest()  # cheap pre-read
    with open(ISO_PATH, 'rb') as fp:
        iso = ISO(fp)
        rec = iso.files['/DATA/PACK.DAT']
        base = rec['lba'] * 2048
        for (dir_id, flags), expect_sha in MEMBER_SHA.items():
            block = read_at(fp, base + dir_id * 2048, 2048)
            ident, count = struct.unpack_from('<2I', block)
            blob = None
            for item in range(count):
                offset, length, fl, reserved = struct.unpack_from(
                    '<4I', block, 64 + item * 16)
                if fl == flags:
                    blob = read_at(fp, base + offset, length)
                    break
            assert blob is not None
            yield dir_id, flags, blob, expect_sha


class OriginalDataTests(unittest.TestCase):
    def _auth(self):
        with open(ISO_PATH, 'rb') as fp:
            h = hashlib.sha256()
            for chunk in iter(lambda: fp.read(4 * 1024 * 1024), b''):
                h.update(chunk)
            self.assertEqual(h.hexdigest(), ISO_SHA256,
                             'ISO identity changed - reauthenticate')

    def test_registration_matches_m7_evidence(self):
        self._auth()
        for dir_id, flags, blob, expect_sha in _members():
            with self.subTest(member=f'{dir_id:#x}/{flags:#x}'):
                self.assertEqual(hashlib.sha256(blob).hexdigest(), expect_sha,
                                 'member hash changed')
                load = M7_LOADS[(dir_id, flags)]
                harness = AudioHarness()
                endpoint = harness.register_resource(blob, load)
                self.assertEqual(endpoint, M7_ENDPOINTS[(dir_id, flags)],
                                 'endpoint differs from M7 runtime evidence')
                # six pointer words; bank file-relative offset from the
                # descriptor at file+0x20 (word(+0x08)=0 for both members)
                bank_file = struct.unpack_from('<I', blob, 0x20 + 4)[0]
                bank = load + bank_file
                self.assertEqual(struct.unpack_from('<I', blob, bank_file + 0x0c)[0],
                                 0x64685353, 'SShd magic missing')
                for i in range(6):
                    offset = struct.unpack_from('<I', blob, bank_file + 0x10 + i * 4)[0]
                    expected = 0 if offset == 0xffffffff else (bank + offset) & 0xffffffff
                    self.assertEqual(harness.mem.r32(bank + 0x30 + i * 4),
                                     expected, f'pointer {i} differs')
                # slot registered and bound in the id table
                entry = harness.mem.r32(ID_TABLE + struct.unpack_from(
                    '<I', blob, 0x28)[0] * 0x78)
                self.assertLess(entry, 8)
                self.assertEqual(harness.mem.r32(SLOT_TABLE + entry * 0x2c + 0x14),
                                 bank, 'slot bank pointer differs')

    def test_payload_transferred_to_iop(self):
        self._auth()
        for dir_id, flags, blob, expect_sha in _members():
            with self.subTest(member=f'{dir_id:#x}/{flags:#x}'):
                load = M7_LOADS[(dir_id, flags)]
                harness = AudioHarness()
                harness.register_resource(blob, load)
                payload = harness.model.iop_audio.payloads
                self.assertEqual(len(payload), 1)
                size = struct.unpack_from('<I', blob, 0x20)[0]
                src = load + struct.unpack_from('<I', blob, 0x18)[0]
                self.assertEqual(payload[0]['size'], size)
                self.assertEqual(payload[0]['blob'], blob[src - load:src - load + size],
                                 'transferred payload differs from original bytes')

    def test_control_wrong_bank_offset_detected(self):
        self._auth()
        for dir_id, flags, blob, expect_sha in _members():
            with self.subTest(member=f'{dir_id:#x}/{flags:#x}'):
                load = M7_LOADS[(dir_id, flags)]
                harness = AudioHarness()
                endpoint = harness.register_resource(blob, load)
                # corrupt bank offset +0x10 by one word in a copy
                corrupt = bytearray(blob)
                bank_file = struct.unpack_from('<I', blob, 0x20 + 4)[0]
                off = struct.unpack_from('<I', corrupt, bank_file + 0x10)[0]
                struct.pack_into('<I', corrupt, bank_file + 0x10, (off + 4) & 0xffffffff)
                bank = load + bank_file
                harness2 = AudioHarness()
                harness2.register_resource(bytes(corrupt), load)
                self.assertNotEqual(
                    harness2.mem.r32(bank + 0x30),
                    harness.mem.r32(bank + 0x30),
                    'corrupted offset not reflected in calculated pointer')


if __name__ == '__main__':
    unittest.main()
