"""Synthetic regression of the SG2 transport, upload and physical-voice path."""
from pathlib import Path
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from hg_audio.connected import ConnectedAudio, RPC_ID, UPLOAD_START
from hg_audio.audio_guest import ID_TABLE
from test_hg_audio_engine import make_bank

CURSOR = 0xa80180


def make_resource(payload):
    bank = make_bank()
    struct.pack_into('<I', bank, 4, len(payload))
    source = 0x30 + len(bank)
    return (struct.pack('<12I', source + len(payload), 0x30, 0, 1, source,
                        len(payload), source, 0, len(payload), 0x30, 2, 127)
            + bank + payload)


def packet_bytes(records):
    return b''.join(struct.pack('<4I', *record) for record in records)


def voice_setup(voice, address, left, right, scale=0x116b):
    return [(3, voice, address, 0), (1, voice, left, right),
            (2, voice, 0x8080, 0x808a), (4, voice, 0x3c3c0040, 0x01000000 | scale)]


class ConnectedAudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('HG_M22_LIBRARY'):
            cls.library = Path(os.environ['HG_M22_LIBRARY'])
            return
        cls.scratch = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.scratch.cleanup)
        cls.library = Path(cls.scratch.name) / 'm22.so'
        ConnectedAudio.build(cls.library)

    def service(self):
        return ConnectedAudio([0x5000, 0x54000, 0x89000, 0x1e0000, 0x71000], self.library)

    def register_bank(self, h, samples, address=0x1000000):
        h.mem.load(address, make_resource(samples))
        staged = False
        for _ in range(12):
            endpoint = h.register_step(address)
            if endpoint:
                self.assertTrue(staged)
                self.assertEqual(h.model.frees, 1)
                self.assertEqual(h.model.sif_heap, bytearray())
                return h.mem.r32(ID_TABLE + 2 * 0x78)
            reply = h.flush()
            self.assertIsNotNone(reply)
            if reply.upload_busy:
                staged = True
                self.assertEqual(h.register_step(address), 0)
                self.assertEqual(h.spu(0x89000, 16), bytes(16))
                self.assertTrue(h.step_dma())
                self.assertEqual(h.register_step(address), 0)
                self.assertEqual(h.flush().upload_busy, 0)
        self.fail('EE registration did not finish')

    def test_registered_bank_to_voice_completion_and_replacement(self):
        # Predictor 0, shift 12, values +1 and -2. Flag 4 starts a loop
        # point and flag 1 ends the sample without repeating.
        payload = bytes([12, 4] + [0xe1] * 14 + [12, 1] + [0xe1] * 14)
        with self.service() as h:
            slot = self.register_bank(h, payload)
            sample = h.sample_address(slot, 0)
            self.assertEqual(sample, 0x89000)
            self.assertEqual(h.spu(sample, len(payload)), payload)
            self.assertIsNone(h.sample_address(slot, len(payload)))
            self.assertIsNone(h.sample_address(slot, 1))
            self.assertTrue(h.stage_voice(1, slot, 0, 60, 60, 0x116b, 0x2000, 0,
                                          0x8080, 0x808a))
            self.assertTrue(h.stage_key(1, True))
            self.assertEqual(h.render(4), [(0, 0)] * 4)
            self.assertEqual(h.flush().envelope[1], 0x7fff)
            self.assertEqual(h.render(8), [(0, 0), (-1, 0)] * 4)
            h.render(90)
            self.assertEqual(h.flush().envelope[1], 0)
            self.assertEqual(h.render(8), [(0, 0)] * 8)
            h._bind('hg_audio_slot_clear', ['slot'])(h._bus_p, slot)
            self.assertIsNone(h.sample_address(slot, 0))
            replacement = bytes([12, 1] + [0x55] * 14)
            source = h.model._sif_alloc(16)
            h.model._dma_to_iop(source, replacement)
            self.assertEqual(h.rpc(packet_bytes([(0x13, source, sample, 16)])).upload_busy, 1)
            self.assertTrue(h.step_dma())
            self.assertEqual(h.flush().upload_busy, 0)
            self.assertEqual(h.spu(sample, 16), replacement)
            self.assertEqual(h.rpc(packet_bytes([(7, 0, 2, 0)])).envelope[1], 0x7fff)
            self.assertEqual(h.render(3), [(2, 0)] * 3)

    def test_two_cores_loop_and_key_off_are_independent(self):
        loop = bytes([12, 7] + [0x66] * 14)
        with self.service() as h:
            sample = h.sample_address(self.register_bank(h, loop), 0)
            batch = (voice_setup(0, sample, 0x2000, 0) +
                     voice_setup(25, sample, 0, 0x2000) +
                     [(9, 0, 1, 2), (7, 0, 1, 2)])
            self.assertIsNotNone(h.rpc(packet_bytes(batch)))
            self.assertEqual(h.render(100), [(3, 3)] * 100)
            self.assertIsNotNone(h.rpc(packet_bytes([(8, 0, 1, 0)])))
            self.assertEqual(h.render(100), [(0, 3)] * 100)
            self.assertEqual(h.flush().envelope[0], 0)
            self.assertEqual(h.flush().envelope[25], 0x7fff)
            self.assertIsNotNone(h.rpc(packet_bytes([(8, 0, 0, 2)])))
            self.assertEqual(h.render(4), [(0, 0)] * 4)

    def test_bad_record_rejects_entire_rpc(self):
        with self.service() as h:
            for bad in [(0xff, 0, 0, 0), (3, 48, 0, 0), (7, 0, 1 << 24, 0),
                        (3, 0, 1, 0), (0x13, 0, 0x1ffff0, 32), (4, 0, 0x00000000, 0xff001000)]:
                with self.subTest(record=bad):
                    self.assertIsNone(h.rpc(packet_bytes([(7, 0, 1, 0), bad])))
                    self.assertEqual(h.flush().envelope[0], 0)
                    self.assertEqual(h.events, [])
            self.assertIsNone(h.rpc(b'\0' * 15))
            self.assertIsNone(h.rpc(server=RPC_ID - 1))
            self.assertIsNone(h.rpc(function=1))

    def test_queue_capacity_and_wrap(self):
        with self.service() as h:
            self.assertIsNone(h.rpc(bytes(512 * 16)))
            for _ in range(511):
                self.assertTrue(h.post((0, 0, 0, 0)))
            self.assertFalse(h.post((7, 0, 1, 0)))
            self.assertEqual(h.mem.r32(CURSOR), 511)
            self.assertIsNotNone(h.flush())
            self.assertEqual(h.mem.r32(CURSOR), 0)
            self.assertTrue(h.post((7, 0, 1, 0)))
            self.assertTrue(h.post((8, 0, 1, 0)))
            self.assertEqual(h.flush().envelope[0], 0)
            self.assertEqual(h.events[0], ('write', 0xbf9001a0, 1))
            self.assertEqual(h.events[4], ('write', 0xbf9001a4, 1))

    def test_full_ee_queue_defers_pending_upload_without_losing_it(self):
        with self.service() as h:
            for _ in range(511):
                self.assertTrue(h.post((0, 0, 0, 0)))
            source = h.model._sif_alloc(16)
            self.assertEqual(h.model.call(UPLOAD_START, source, 0x100, 16, 0, 0), 0)
            self.assertEqual(h.flush().upload_busy, 0)
            self.assertFalse(h.step_dma())
            self.assertEqual(h.flush().upload_busy, 1)
            self.assertTrue(h.step_dma())
            self.assertEqual(h.flush().upload_busy, 0)

    def test_busy_upload_needs_readable_source_and_subsequent_rpc(self):
        with self.service() as h:
            wire = packet_bytes([(0x13, 0x40000000, 0x100, 16)])
            self.assertEqual(h.rpc(wire).upload_busy, 1)
            self.assertFalse(h.step_dma())
            self.assertIsNone(h.rpc(wire))
            self.assertEqual(h.rpc().upload_busy, 1)
            h.model._sif_alloc(16)
            self.assertTrue(h.step_dma())
            self.assertIsNone(h.rpc(wire))
            self.assertEqual(h.rpc().upload_busy, 0)
            self.assertEqual(h.rpc(wire).upload_busy, 1)


if __name__ == '__main__':
    unittest.main()
