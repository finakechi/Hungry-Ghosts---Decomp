#!/usr/bin/env python3
"""Merge the actual M2 discovery snapshot with existing reviewed statuses."""
import argparse
import json
from pathlib import Path


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--export',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();old=json.loads(Path('analysis/function-inventory.json').read_text())
    reviewed={f['entry']:f for f in old['functions']};rows=json.loads(a.export.read_text())
    m2=json.loads(Path('analysis/resource_loader/functions.json').read_text())
    names={f['entry']:f['name'] for f in m2['functions']}
    context={f['entry']:f['interpretation'] for f in json.loads(Path('analysis/resource_loader/context.json').read_text())['functions']}
    for f in rows:
        if f['entry'] in reviewed:
            previous=reviewed[f['entry']]
            if previous['body_ranges']!=f['body_ranges']:raise ValueError('Unreviewed boundary change '+f['entry'])
            for key,value in previous.items():
                if key not in ('name','calls'):f[key]=value
        f['byte_matching']='not-tested';f['gameplay_runtime_verification']='not-tested'
        if f['entry'] in names:
            f['analyst_name']=names[f['entry']];f['status']='reconstructed-model-differential-tested'
            f['milestone']='M2';f['evidence']='analysis/resource_loader/functions.json'
        elif f['entry'] in context:
            f['status']='analyzed-partial-byte-checked';f['analyst_description']=context[f['entry']]
    result={**old,'discovery':'M1 analysis plus two explicitly discovered M2 entries; automatic candidates are not verified function totals.',
            'count':len(rows),'status_counts':{s:sum(f['status']==s for f in rows) for s in sorted({f['status'] for f in rows})},'functions':rows}
    with a.output.open('x') as fp:json.dump(result,fp,indent=2);fp.write('\n')
    print(result['count'],result['status_counts'])


if __name__=='__main__':main()
