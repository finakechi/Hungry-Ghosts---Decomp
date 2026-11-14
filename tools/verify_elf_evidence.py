#!/usr/bin/env python3
"""Supplement PS2DInfo's structural validator with game-specific ELF byte checks."""
import argparse
import hashlib
import json
from pathlib import Path
from hg_identify import elf32
from hg_evidence import EXPECTED, checked_function


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf',type=Path,required=True)
    p.add_argument('--record',type=Path,default=Path('analysis/disc_read/functions.json'))
    a=p.parse_args();data=a.elf.read_bytes();r=json.loads(a.record.read_text())
    if hashlib.sha256(data).hexdigest()!=EXPECTED or r['program']['sha256']!=EXPECTED:raise ValueError('wrong original ELF')
    elf=elf32(data)
    for f in r['functions']:checked_function(f,data,elf)
    print(f'PASS: {len(r["functions"])} function listings match original ELF bytes and JAL targets')


if __name__=='__main__':main()
