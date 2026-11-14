import struct
import unittest
from resource_harness import Driver,library,setup_pack,synthetic_pack


class ResourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.lib=library()
    def test_request_through_worker_to_pack_completion(self):
        d=Driver();setup_pack(d)
        d.invoke(self.lib,'request_raw',0,0)
        self.assertEqual((d.mem.load(0x3eacec,1),d.mem.load(0x3f8e30,2)),(1,1))
        self.assertEqual(d.mem.load(0x3f8e04),0x130220)
        d.invoke(self.lib,'worker')
        self.assertEqual((d.mem.load(0x3eacec,1),d.mem.load(0x3f8e30,2)),(0,0))
        self.assertEqual(d.mem.load(0x3f4300+4*4),0xb00000)
        self.assertEqual(d.mem.load(0x3f4300+7*4),0xb00800)
        self.assertEqual(d.mem.load(0x3f4d3c),0xb01000)
        self.assertEqual(d.mem.get(0xb00000,4),b'TEST')
        self.assertEqual([x[1:4] for x in d.calls if x[0]=='read'],[(1000,1,0x3f3b00),(1001,2,0xb00000)])
        self.assertIn(('call',0x103da0),d.calls)
    def test_busy_rejection_retains_original_request_flag_effect(self):
        d=Driver();d.mem.store(0x3f8e30,1,2);d.mem.store(0x3f8e44,123)
        d.invoke(self.lib,'request_raw',1,88)
        self.assertEqual(d.mem.load(0x3f8e44),123)
        self.assertEqual(d.mem.load(0x3eacec,1),1)
        self.assertFalse(d.calls)
    def test_nonzero_directory_uses_sector_index_and_existing_arena(self):
        d=Driver();setup_pack(d,id=1);d.invoke(self.lib,'request_raw',0,1);d.invoke(self.lib,'worker')
        self.assertEqual([x[1:4] for x in d.calls if x[0]=='read'],[(1001,1,0x3f3b00),(1002,2,0xc00000)])
        self.assertEqual(d.mem.load(0x3f4310),0xc00000)
    def test_priority_kind_100_bypasses_normal_busy_gate(self):
        d=Driver();d.mem.store(0x3f8e30,1,2);d.mem.store(0x3f6db0,7,2)
        d.invoke(self.lib,'schedule',0x10064,77,0x1568f0)
        self.assertEqual(d.mem.load(0x3f6db0,2),1)
        self.assertEqual(d.mem.load(0x3f6dc4),77)
        self.assertEqual(d.mem.load(0x3f6d94),3)
        self.assertIn(('call',0x103d80,17,0),d.calls)
    def test_create_failure_is_passed_to_start(self):
        d=Driver();d.thread_id=-1;d.invoke(self.lib,'schedule',1,2,0x130220)
        self.assertIn(('call',0x103d80,0xffffffff,0),d.calls)
    def test_remapped_identifiers(self):
        for variant in (0,1,0xffff):
            for identifier in (9,0x4e,0x67,42):
                d=Driver();d.mem.store(0x3fa1c4,variant,2)
                d.invoke(self.lib,'request',1,identifier)
                expected=identifier+(variant!=0 and identifier in (9,0x4e,0x67))
                self.assertEqual(d.mem.load(0x3f8e44),expected)
    def test_empty_directory_completes_before_bulk_read(self):
        d=Driver();setup_pack(d,pack=bytes(2048));d.invoke(self.lib,'request_raw',0,0);d.invoke(self.lib,'worker')
        self.assertEqual(len([x for x in d.calls if x[0]=='read']),1)
        self.assertEqual(d.mem.load(0x3f8e39,1),2)
    def test_busy_and_rejected_reads_progress(self):
        d=Driver();setup_pack(d);d.sync_busy=3;d.read_failures=2
        d.invoke(self.lib,'request_raw',0,0);d.invoke(self.lib,'worker')
        self.assertEqual(d.mem.load(0x3f8e30,2),0)
        self.assertEqual(len([x for x in d.calls if x[0]=='read']),4)
        self.assertEqual(len([x for x in d.calls if x==('ready',0)]),2)
    def test_unknown_worker_kind_only_completes(self):
        d=Driver();d.invoke(self.lib,'request_raw',0xffff,4);d.calls=[];d.invoke(self.lib,'worker')
        self.assertEqual(d.calls,[('call',0x103da0)])
    def test_invalid_bootstrap_and_special_phases_do_not_progress(self):
        d=Driver();d.mem.store(0x3f8e39,255,1);d.mem.store(0x3f8e3b,255,1)
        self.assertEqual(d.invoke(self.lib,'bootstrap_step',0x3f8e00),0)
        self.assertEqual(d.invoke(self.lib,'special',0x3f8e00,0x3f3b00,0x3f3b00),0)
        self.assertFalse(d.calls)
    def test_bulk_arena_rounding_boundaries_and_word_wrap(self):
        for dst,size in [(0xb00000,n) for n in (0,1,2047,2048,2049)]+[(0xfffffff0,32)]:
            with self.subTest(dst=dst,size=size):
                d=Driver();d.mem.store(0x3f8e39,5,1);d.mem.store(0x3f8e48,dst);d.mem.store(0x3f3b14,size)
                self.assertEqual(d.invoke(self.lib,'bootstrap_step',0x3f8e00),1)
                self.assertEqual(d.mem.load(0x3f4d3c),((dst+size+2047)&0xffffffff)&0xfffff800)
    def test_special_fixup_advances_cursor_and_wraps_counter(self):
        d=Driver();t=0x3f8e00;d.mem.store(t+0x3b,6,1);d.mem.store(t+0x3c,1,2)
        d.mem.store(t+0x48,0xb00000);d.mem.store(t+0x4c,0x3f3b40);d.mem.store(0x3f3b48,9)
        self.assertEqual(d.invoke(self.lib,'special',t,0x3f3b00,0x3f3b00),0)
        self.assertEqual((d.mem.load(t+0x3b,1),d.mem.load(t+0x3c,2)),(7,0))
        self.assertEqual((d.mem.load(t+0x48),d.mem.load(t+0x4c)),(0xb00020,0x3f3b50))
        self.assertEqual(d.mem.load(0x3f4324),0xb00000)
    def test_special_fixup_retains_id_and_reloads_address_across_alias(self):
        for id,cursor,expected in ((9,0x3f4300+9*4-8,0xb00000),
                                   (0x12d2,0x3f3b40,0xb00000)):
            d=Driver();t=0x3f8e00
            d.mem.store(t+0x3b,6,1);d.mem.store(t+0x3c,1,2)
            d.mem.store(t+0x48,0xb00000);d.mem.store(t+0x4c,cursor)
            d.mem.store(cursor+8,id)
            d.invoke(self.lib,'special',t,0x3f3b00,0x3f3b00)
            self.assertIn(('call',0x137ed0,expected,0,id),d.calls)
    def test_area_read_full_operation_and_zero_tail(self):
        d=Driver();t=0x3f8e00;d.mem.store(0x3dff04,0xa87800)
        d.mem.store(0x3e01b4,10);d.mem.store(0x3fa1b0,1,1);d.mem.store(0x3fa1b1,2,1)
        d.mem.store(0x3f3a30+12*8,2000);d.mem.store(0x3f3a34+12*8,2048)
        data=bytearray(2048);data[:4]=b'MWo3';struct.pack_into('<I',data,0x14,32)
        d.files[2000]=bytes(data);d.mem.put(0xa88000,b'X'*32)
        answers=[d.invoke(self.lib,'area_step',t) for _ in range(4)]
        self.assertEqual(answers,[0,0,0,1])
        self.assertEqual(d.mem.get(0xa87800,4),b'MWo3')
        self.assertEqual(d.mem.get(0xa88000,32),bytes(32))
        self.assertIn(('call',0x1041e0,2),d.calls)
    def test_area_finalize_retains_header_value_before_cache_callback(self):
        d=Driver();d.mem.store(0xa87814,32);original=d.external
        def callback(pc,*args):
            result=original(pc,*args)
            if pc==0x1041e0:d.mem.store(0xa87814,64)
            return result
        d.external=callback;d.invoke(self.lib,'area_finalize',0xa87800,2048)
        self.assertEqual(d.calls,[('call',0x1041e0,2),('call',0x119ac8,0xa88000,0,32)])


if __name__=='__main__':unittest.main()
