"""Synthetic fail-closed checks for the private M4 capture reader."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from verify_fixup_runtime import samples

class FixupCapture(unittest.TestCase):
    def packet(self):
        meta={'serial':'SCPS-15046','uuid':'91aa4b28','status':1}
        return {'metadata_before':meta.copy(),'metadata_after':meta.copy(),
                'samples':[{'address':'0xb00000','size_bytes':4,'bytes':'54455354',
                            'sha256':hashlib.sha256(b'TEST').hexdigest()}]}
    def read(self,row):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'capture.json';p.write_text(json.dumps(row));return samples(p)
    def test_paused_sample_accepts_consistent_identity_and_digest(self):
        self.assertEqual(self.read(self.packet())[1],{0xb00000:b'TEST'})
    def test_running_or_changed_identity_fails(self):
        for key,value in (('status',0),('serial','OTHER'),('uuid','00000000')):
            row=self.packet();row['metadata_before'][key]=value;row['metadata_after'][key]=value
            with self.assertRaises(ValueError):self.read(row)
        row=self.packet();row['metadata_after']['status']=0
        with self.assertRaises(ValueError):self.read(row)
    def test_changed_bytes_or_length_fails(self):
        for key,value in (('bytes','54455355'),('size_bytes',3)):
            row=self.packet();row['samples'][0][key]=value
            with self.assertRaises(ValueError):self.read(row)

if __name__=='__main__':unittest.main()
