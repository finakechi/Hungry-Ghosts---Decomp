"""Check a bound result against current source and explicitly supplied inputs."""
import argparse
import json
from pathlib import Path
from hg_evidence_safety import check_receipt
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--record',type=Path,required=True)
p.add_argument('--input',nargs=2,action='append',required=True,metavar=('LABEL','PATH'))
a=p.parse_args();r=json.loads(a.record.read_text())
print('PASS fresh binding:',check_receipt(r.get('binding',r),Path(__file__).resolve().parents[1],{k:Path(v) for k,v in a.input}))
