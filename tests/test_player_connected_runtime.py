"""Synthetic checks of the retail-comparison harness rules (no retail inputs).

The external libpad DMA domain, its fail-closed access rule and the exact
callee-entry probe signatures are exercised on synthetic 32 MiB memories.
"""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import verify_player_connected_runtime as V

RAM=0x2000000


def memory():
    raw=bytearray(RAM)
    entry=V.PAD_TABLE+0x1c  # port 0, slot 1
    raw[entry:entry+4]=(0x95e340).to_bytes(4,'little')
    raw[entry+0x10:entry+0x14]=(1).to_bytes(4,'little')
    return V.CapturedMemory(bytes(raw))


class ExternalDmaDomainTests(unittest.TestCase):
    def test_open_buffers_are_derived_from_port_table(self):
        self.assertEqual(V.pad_dma_buffers(memory()),
                         [dict(port=0,slot=1,start=0x95e340,end=0x95e440)])

    def test_misaligned_buffer_fails_closed(self):
        m=memory();m.store(V.PAD_TABLE+0x1c,0x95e344)
        with self.assertRaises(ValueError):V.pad_dma_buffers(m)

    def test_untouched_dma_difference_is_separated(self):
        actual,expected=memory(),memory();expected.store(0x95e418,0xe741,2)
        result=V.compare_memory(actual,expected,V.pad_dma_buffers(actual))
        self.assertEqual(result['result'],'passed')
        self.assertEqual(result['external_dma']['different_bytes'],2)  # 0000 -> e741

    def test_modeled_write_or_read_of_dma_buffer_fails(self):
        for access in ('write','read'):
            actual,expected=memory(),memory();expected.store(0x95e418,0xe741,2)
            external=V.pad_dma_buffers(actual)
            actual.watch=[(e['start'],e['end']) for e in external]
            if access=='write':actual.store(0x95e400,0)
            else:actual.load(0x95e400)
            self.assertEqual(V.compare_memory(actual,expected,external)['result'],'failed',access)

    def test_difference_outside_domains_fails(self):
        actual,expected=memory(),memory();expected.store(0x3f9718,1,1)
        result=V.compare_memory(actual,expected,V.pad_dma_buffers(actual))
        self.assertEqual((result['result'],result['different_bytes']),('failed',1))

    def test_guest_stack_interval_is_excluded(self):
        actual,expected=memory(),memory();expected.store(0x1ff8000,7)
        self.assertEqual(V.compare_memory(actual,expected)['result'],'passed')


class ProbeSignatureTests(unittest.TestCase):
    def probe(self,events):
        driver=V.ProbeDriver.__new__(V.ProbeDriver)
        driver.mem=memory();driver.probes={'transition':V.TRANSITION_ENTRY}
        driver.recent=[];driver.snapshots={};driver.hits={'transition':0}
        for event in events:driver._event(event)
        return driver

    def test_exact_signature_snapshots_once(self):
        d=self.probe(V.TRANSITION_ENTRY+[('load',0x100,4)]+V.TRANSITION_ENTRY)
        self.assertEqual(d.hits['transition'],2)
        self.assertEqual(list(d.snapshots),['transition'])

    def test_interleaved_access_breaks_signature(self):
        events=V.TRANSITION_ENTRY[:2]+[('load',0x3eaf00,2)]+V.TRANSITION_ENTRY[2:]
        self.assertEqual(self.probe(events).hits['transition'],0)

    def test_store_value_is_part_of_signature(self):
        events=[('store',0x3f9f38,1,1)]+V.TRANSITION_ENTRY[1:]
        self.assertEqual(self.probe(events).hits['transition'],0)


if __name__=='__main__':unittest.main()
