"""Synthetic safety regressions; no game data or Ghidra required."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from hg_evidence_safety import digest, select_export, receipt, check_receipt, reconcile

class Safety(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        subprocess.run(['git','init','-q',str(self.root)],check=True)
        subprocess.run(['git','-C',str(self.root),'-c','user.name=Test','-c','user.email=test@example.invalid','commit','--allow-empty','-qm','fixture'],check=True)
        (self.root/'src').mkdir();self.source=self.root/'src/a.c';self.source.write_text('original')
        self.input=self.root/'input';self.input.write_text('synthetic')
    def export(self,name):
        d=self.root/name;d.mkdir();(d/'00100000.json').write_text('{}');return d
    def test_ambiguous(self):
        self.export('a');self.export('b')
        with self.assertRaisesRegex(ValueError,'found 2'):select_export(self.root,0x100000)
    def test_explicit(self):
        a=self.export('a');self.export('b');self.assertEqual(select_export(a,0x100000),a/'00100000.json')
    def test_legacy(self):
        a=self.export('a');self.assertEqual(select_export(self.root,0x100000),a/'00100000.json')
    def test_partial(self):
        a=self.export('a');(a/'.incomplete').touch()
        with self.assertRaisesRegex(ValueError,'Incomplete'):select_export(a,0x100000)
    def test_bad_hash(self):
        a=self.export('a');(a/'COMPLETE.json').write_text(json.dumps({'files':{'00100000.json':'bad'}}))
        with self.assertRaisesRegex(ValueError,'digest'):select_export(a,0x100000)
    def bound(self):return receipt(self.root,{'input':self.input},'differential',['fixture'],['synthetic only'])
    def test_freshness(self):self.assertEqual(check_receipt(self.bound(),self.root,{'input':self.input}),'differential')
    def test_source_change(self):
        r=self.bound();self.source.write_text('changed')
        with self.assertRaisesRegex(ValueError,'source changed'):check_receipt(r,self.root,{'input':self.input})
    def test_input_change(self):
        r=self.bound();self.input.write_text('changed')
        with self.assertRaisesRegex(ValueError,'digest'):check_receipt(r,self.root,{'input':self.input})
    def test_missing_runtime(self):
        with self.assertRaisesRegex(ValueError,'Runtime proof'):receipt(self.root,{},'saved-runtime',[],[],None)
    def test_mismatched_runtime(self):
        with self.assertRaisesRegex(ValueError,'identity mismatch'):receipt(self.root,{k:self.input for k in ['raw','post','profile','emulator','image']},'saved-runtime',[],[],{'before':{'serial':'a'},'after':{'serial':'b'}})
    def test_cannot_claim_fresh(self):
        with self.assertRaises(ValueError):receipt(self.root,{},'fresh-runtime',[],[])
    def test_review_continuity(self):
        previous={'entry':'1','status':'reviewed','name':'known','body_ranges':[],'calls':[]}
        with self.assertRaisesRegex(ValueError,'reconciliation'):reconcile([previous],[],{})
        self.assertEqual(reconcile([previous],[previous.copy()],{}),[])
        self.assertEqual(reconcile([previous],[],{'1':{'reason':'merged','replacements':[]}})[0]['previous'],previous)
    def test_java_export_transaction(self):
        source=Path(__file__).resolve().parents[1]/'ghidra_scripts/HGExportSet.java'
        harness=self.root/'Probe.java'
        harness.write_text('''import java.nio.file.*;
public class Probe {public static void main(String[] a)throws Exception {
Path p=Path.of(a[0]); HGExportSet x=new HGExportSet(p);
Files.writeString(x.staging().resolve("data.json"),"synthetic");
if(a[1].equals("complete"))x.finish();
}}''')
        subprocess.run(['javac','-d',str(self.root),str(source),str(harness)],check=True)
        def run(path,mode):return subprocess.run(['java','-cp',str(self.root),'Probe',str(path),mode],capture_output=True)
        p=self.root/'complete';self.assertEqual(run(p,'complete').returncode,0)
        before={x.name:digest(x) for x in p.iterdir()};self.assertNotEqual(run(p,'complete').returncode,0)
        self.assertEqual(before,{x.name:digest(x) for x in p.iterdir()})
        partial=self.root/'partial';self.assertEqual(run(partial,'interrupt').returncode,0)
        self.assertFalse((partial/'COMPLETE.json').exists());self.assertTrue((partial/'.incomplete').exists())
        self.assertNotEqual(run(partial,'complete').returncode,0)
