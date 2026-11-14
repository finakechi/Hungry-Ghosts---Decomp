import io
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from hg_identify import ISO, elf32, va_to_offset, identify
from hg_pack_scan import scan
from hg_evidence import checked_function


def synthetic_elf():
    data=bytearray(512)
    data[:7]=b'\x7fELF\x01\x01\x01'
    struct.pack_into('<HHIIIIIHHHHHH',data,16,2,8,1,0x100000,52,0,0,52,32,1,40,0,0)
    struct.pack_into('<8I',data,52,1,256,0x100000,0x100000,16,64,7,256)
    return data


def directory_record(name,lba,size,is_dir=False):
    n=33+len(name)+(1 if len(name)%2==0 else 0)
    b=bytearray(n);b[0]=n
    struct.pack_into('<I',b,2,lba);struct.pack_into('>I',b,6,lba)
    struct.pack_into('<I',b,10,size);struct.pack_into('>I',b,14,size)
    b[25]=2 if is_dir else 0
    struct.pack_into('<H',b,28,1);struct.pack_into('>H',b,30,1)
    b[32]=len(name);b[33:33+len(name)]=name
    return b


def synthetic_iso():
    b=bytearray(40*2048);pvd=bytearray(2048)
    pvd[:7]=b'\x01CD001\x01'
    struct.pack_into('<I',pvd,80,40);struct.pack_into('>I',pvd,84,40)
    struct.pack_into('<H',pvd,128,2048);struct.pack_into('>H',pvd,130,2048)
    root=directory_record(b'\0',20,2048,True);pvd[156:190]=root
    b[16*2048:17*2048]=pvd
    cnf=b'BOOT2 = cdrom0:\\SCPS_150.46;1\r\nVER = 1.02\r\nVMODE = NTSC\r\n'
    boot=synthetic_elf()
    d=root+directory_record(b'\1',20,2048,True)+directory_record(b'SYSTEM.CNF;1',21,len(cnf))+directory_record(b'SCPS_150.46;1',22,len(boot))
    b[20*2048:20*2048+len(d)]=d;b[21*2048:21*2048+len(cnf)]=cnf;b[22*2048:22*2048+len(boot)]=boot
    return b


class InputTests(unittest.TestCase):
    def test_elf_mapping_excludes_bss(self):
        e=elf32(synthetic_elf())
        self.assertEqual(va_to_offset(e,0x100000),256)
        with self.assertRaises(ValueError):va_to_offset(e,0x100010)

    def test_elf_rejects_wrong_machine_and_truncation(self):
        data=synthetic_elf();struct.pack_into('<H',data,18,62)
        with self.assertRaises(ValueError):elf32(data)
        with self.assertRaises(ValueError):elf32(synthetic_elf()[:70])

    def test_elf_rejects_bad_segment(self):
        data=synthetic_elf();struct.pack_into('<I',data,52+16,1000)
        with self.assertRaises(ValueError):elf32(data)

    def test_elf_ambiguous_file_mapping_fails(self):
        e=elf32(synthetic_elf());e['pt_load'].append(e['pt_load'][0].copy())
        with self.assertRaises(ValueError):va_to_offset(e,0x100000)

    def test_iso_and_identification_roundtrip_without_retail(self):
        data=synthetic_iso();iso=ISO(io.BytesIO(data))
        self.assertEqual(set(iso.files),{'/SCPS_150.46','/SYSTEM.CNF'})
        (ROOT/'local').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT/'local') as temp:
            path=Path(temp)/'fixture.iso';path.write_bytes(data)
            before=path.read_bytes();r=identify(path,'synthetic')
            self.assertEqual(r['serial'],'SCPS-15046');self.assertEqual(r['elf']['entry'],'0x00100000')
            self.assertEqual(path.read_bytes(),before)

    def test_iso_both_endian_mismatch_fails(self):
        data=synthetic_iso();data[16*2048+87]^=1
        with self.assertRaises(ValueError):ISO(io.BytesIO(data))

    def test_iso_directory_cycle_fails(self):
        data=synthetic_iso();rec=directory_record(b'LOOP',20,2048,True)
        data[20*2048+68:20*2048+68+len(rec)]=rec
        with self.assertRaises(ValueError):ISO(io.BytesIO(data))

    def test_pack_boundary_is_measured(self):
        data=bytearray(2048+16)
        struct.pack_into('<II',data,0,0,1);struct.pack_into('<4I',data,64,2048,16,0,0)
        data[2048:2052]=b'FLK5';r=scan(io.BytesIO(data),0,len(data))
        self.assertEqual((r['directory_count'],r['member_count'],r['flk5_count']),(1,1,1))

    def test_pack_bad_member_and_count_fail(self):
        data=bytearray(2048+16);struct.pack_into('<II',data,0,0,1)
        struct.pack_into('<4I',data,64,2048,17,0,0)
        with self.assertRaises(ValueError):scan(io.BytesIO(data),0,len(data))
        struct.pack_into('<I',data,4,125)
        with self.assertRaises(ValueError):scan(io.BytesIO(data),0,len(data))

    def test_evidence_checks_bytes_and_decoded_jal_target(self):
        data=synthetic_elf();data[256:260]=bytes.fromhex('0000040c') # synthetic JAL 00100000
        row={'instructions':[{'address':'0x00100000','bytes':'0000040c','text':'jal 0x00100000'}]}
        checked_function(row,data,elf32(data))
        row['instructions'][0]['text']='jal 0x00200000'
        with self.assertRaises(ValueError):checked_function(row,data,elf32(data))
        row['instructions'][0]['bytes']='00000000'
        with self.assertRaises(ValueError):checked_function(row,data,elf32(data))


if __name__=='__main__':unittest.main()
