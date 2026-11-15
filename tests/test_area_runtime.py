"""Synthetic integrity gates for saved AREA debugger evidence, no retail data."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from hg_evidence_safety import digest
from verify_area_runtime import EVENTS, EXPECTED, IMAGE, load_observation


class AreaRuntimeIntegrity(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.exe=self.root/'emulator';self.exe.write_bytes(b'synthetic emulator identity')
        self.profile=self.root/'profile.ini'
        self.profile.write_text('[EmuCore]\nEnablePatches=false\nEnableCheats=false\n'
            'EnableWideScreenPatches=false\nEnableNoInterlacingPatches=false\nEnablePINE=true\nPINESlot=28050\n'
            '[MemoryCards]\nSlot1_Enable=false\nSlot2_Enable=false\n')
        self.shot=self.root/'shot.png';self.shot.write_bytes(b'synthetic screenshot binding only')
        self.capture=self.root/'capture.json'
        meta=dict(serial='SCPS-15046',uuid='91aa4b28',status=1)
        self.capture.write_text(json.dumps(dict(metadata_before=meta,metadata_after=meta,
            samples=[dict(address='0x00500000',size_bytes=4,bytes='01020304',
                sha256=hashlib.sha256(bytes([1,2,3,4])).hexdigest())])))
        self.obs=dict(elf_sha256=EXPECTED,image_sha256=IMAGE,emulator_sha256=digest(self.exe),
            profile_sha256=digest(self.profile),supplemental=[],observations=[
                dict(event=n,pc=hex(pc),screenshot=self.shot.name,screenshot_sha256=digest(self.shot),
                    capture=self.capture.name,capture_sha256=digest(self.capture),registers={})
                for n,pc in EVENTS.items()])

    def load(self):
        (self.root/'observation-resumed.json').write_text(json.dumps(self.obs))
        return load_observation(self.root,self.exe,self.profile)

    def test_valid_bound_synthetic_capture_and_missing_reuse_observation(self):
        self.assertEqual(len(self.load()[1]),len(EVENTS))
        self.obs['observations'].pop()
        with self.assertRaisesRegex(ValueError,'Missing or reordered'):self.load()

    def test_artifact_tampering_is_rejected(self):
        self.shot.write_bytes(b'changed screenshot')
        with self.assertRaisesRegex(ValueError,'artifact changed'):self.load()

    def test_running_capture_cannot_be_promoted_by_rebinding_hash(self):
        d=json.loads(self.capture.read_text());d['metadata_before']['status']=0
        d['metadata_after']['status']=0;self.capture.write_text(json.dumps(d))
        for e in self.obs['observations']:e['capture_sha256']=digest(self.capture)
        with self.assertRaisesRegex(ValueError,'must be paused'):self.load()

    def test_rebound_profile_with_patches_is_rejected(self):
        self.profile.write_text(self.profile.read_text().replace('EnablePatches=false','EnablePatches=true'))
        self.obs['profile_sha256']=digest(self.profile)
        with self.assertRaisesRegex(ValueError,'Optional patch enabled'):self.load()


if __name__=='__main__':unittest.main()
