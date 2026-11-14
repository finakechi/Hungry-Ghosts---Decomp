import ctypes as C
import unittest
from disc_harness import State, Driver, library


class DiscReadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = library()

    def setup_read(self,length=600000,offset=0):
        s=State()
        self.lib.hg_read_setup(C.byref(s),0x500000,0x800000,offset,length)
        return s

    def test_setup_preserves_derived_fields(self):
        s=State(*range(11))
        self.lib.hg_read_setup(C.byref(s),10,20,30,40)
        self.assertEqual([s.state_word,s.error,s.file_record,s.destination,s.byte_offset,s.byte_length],[0,0,10,20,30,40])
        self.assertEqual([s.next_lba,s.sector_count,s.sectors_issued,s.next_destination,s.between_chunks],[6,7,8,9,10])

    def test_zero_length_poll_has_no_effects(self):
        s=self.setup_read(0);driver=Driver();env=driver.environment()
        before=bytes(s)
        self.assertEqual(self.lib.hg_read_poll(C.byref(s),C.byref(env)),1)
        self.assertEqual(bytes(s),before);self.assertEqual(driver.calls,[])

    def test_chunked_full_lifecycle(self):
        s=self.setup_read();driver=Driver();env=driver.environment(flag_a=1)
        returns=[]
        for _ in range(6):returns.append(self.lib.hg_read_poll(C.byref(s),C.byref(env)))
        self.assertEqual(returns,[0,0,0,0,0,1])
        reads=[c for c in driver.calls if c[0]=='read']
        self.assertEqual(reads,[('read',1000,128,0x800000,b'\0\1\0'),
                                ('read',1128,128,0x840000,b'\0\1\0'),
                                ('read',1256,37,0x880000,b'\0\1\0')])
        self.assertEqual((s.sector_count,s.sectors_issued,env.active),(293,293,0))

    def test_error_restarts_whole_request(self):
        s=self.setup_read(2048);d=Driver(error=0x31);env=d.environment()
        self.assertEqual(self.lib.hg_read_poll(C.byref(s),C.byref(env)),0)
        self.assertEqual(s.state_word,1)
        self.assertEqual(self.lib.hg_read_poll(C.byref(s),C.byref(env)),0)
        self.assertEqual((s.state_word,s.error,env.active),(0,0x31,1))
        d.error_result=0
        self.lib.hg_read_poll(C.byref(s),C.byref(env))
        self.assertEqual(self.lib.hg_read_poll(C.byref(s),C.byref(env)),1)
        self.assertEqual([x[1:4] for x in d.calls if x[0]=='read'],[(1000,1,0x800000)]*2)

    def test_busy_short_circuits_ready_and_error(self):
        s=self.setup_read();d=Driver(sync=1);env=d.environment()
        self.assertEqual(self.lib.hg_read_poll(C.byref(s),C.byref(env)),0)
        self.assertEqual([x for x in d.calls if x[0]!='read32'],[('sync',1)])
        self.assertEqual(s.state_word,0)

    def test_inhibit_blocks_only_value_one(self):
        for inhibit,issued in [(0,True),(1,False),(2,True),(255,True)]:
            with self.subTest(inhibit=inhibit):
                s=self.setup_read(2048);d=Driver();env=d.environment(inhibit=inhibit)
                result=self.lib.hg_read_start(C.byref(s),C.byref(env))
                self.assertEqual(result,2048 if issued else -1)
                self.assertEqual(any(x[0]=='read' for x in d.calls),issued)

    def test_signed_offset_truncates_toward_zero(self):
        for offset,lba in [(0,1000),(2047,1000),(2048,1001),(-1,1000),(-2048,999),(-2049,999)]:
            with self.subTest(offset=offset):
                s=self.setup_read(1,offset);d=Driver();env=d.environment()
                self.lib.hg_read_start(C.byref(s),C.byref(env))
                self.assertEqual(next(c[1] for c in d.calls if c[0]=='read'),lba)

    def test_negative_length_uses_whole_file_size(self):
        s=self.setup_read(0xffffffff,2048);d=Driver(file_size=4097);env=d.environment()
        self.assertEqual(self.lib.hg_read_start(C.byref(s),C.byref(env)),6144)
        self.assertIn(('read',1001,3,0x800000,b'\0\1\0'),d.calls)

    def test_read_rejection_waits_and_retries(self):
        s=self.setup_read(2048);d=Driver(failures=2);env=d.environment()
        self.assertEqual(self.lib.hg_read_start(C.byref(s),C.byref(env)),2048)
        self.assertEqual([c[0] for c in d.calls if c[0]!='read32'],['sync','ready','read','ready','read','ready','read'])
        self.assertEqual([c for c in d.calls if c==('ready',0)],[('ready',0)]*2)

    def test_unknown_poll_state_preserves_memory(self):
        s=self.setup_read();s.state_word=0x123456ff;d=Driver();env=d.environment();before=bytes(s)
        self.assertEqual(self.lib.hg_read_poll(C.byref(s),C.byref(env)),0)
        self.assertEqual(bytes(s),before);self.assertEqual(d.calls,[])


if __name__=='__main__':unittest.main()
