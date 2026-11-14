import struct
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from hg_pine_read import Pine


class FakeSocket:
    def __init__(self,response):self.response=response;self.sent=b''
    def sendall(self,data):self.sent+=data
    def recv(self,n):
        part=self.response[:min(n,2)];self.response=self.response[len(part):];return part


class PineTests(unittest.TestCase):
    def client(self,payload):
        p=Pine.__new__(Pine);p.sock=FakeSocket(struct.pack('<I',4+len(payload))+payload);return p

    def test_fragmented_reply_and_only_read_opcodes(self):
        p=self.client(b'\0abcdefgh')
        self.assertEqual(p.read(0x100000,8),b'abcdefgh')
        self.assertEqual(p.sock.sent,struct.pack('<I',9)+b'\x03'+struct.pack('<I',0x100000))

    def test_tail_does_not_read_outside_range(self):
        p=self.client(b'\0abc')
        self.assertEqual(p.read(0x100000,3),b'abc')
        expected=b''.join(b'\0'+struct.pack('<I',0x100000+i) for i in range(3))
        self.assertEqual(p.sock.sent,struct.pack('<I',len(expected)+4)+expected)

    def test_error_and_invalid_lengths_fail(self):
        p=self.client(b'\xff')
        with self.assertRaises(ValueError):p.request(b'\x0f')
        p=Pine.__new__(Pine);p.sock=FakeSocket(struct.pack('<I',999999))
        with self.assertRaises(ValueError):p.request(b'\x0f')

    def test_short_closed_reply_fails(self):
        p=Pine.__new__(Pine);p.sock=FakeSocket(b'\x05')
        with self.assertRaises(ValueError):p.request(b'\x0f')

    def test_only_ee_ram_ranges_allowed(self):
        p=self.client(b'\0')
        for at,size in [(-1,1),(0,-1),(0x2000000,1),(0x1ffffff,2)]:
            with self.assertRaises(ValueError):p.read(at,size)


if __name__=='__main__':unittest.main()
