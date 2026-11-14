#!/usr/bin/env python3
"""Observe original resource loading through read-only PINE sampling."""
import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from hg_pine_read import Pine


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--socket',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--seconds',type=float,default=18)
    p.add_argument('--hz',type=int,default=100)
    a=p.parse_args();deadline=time.monotonic()+30;client=None
    while time.monotonic()<deadline:
        try:
            client=Pine(a.socket);meta=client.metadata()
            if meta['serial']=='SCPS-15046' and meta['uuid'].lower()=='91aa4b28':break
            client.close();client=None
        except (OSError,ValueError):
            if client:client.close();client=None
        time.sleep(.05)
    if client is None:raise SystemExit('Original game PINE identity not available')
    start=time.monotonic();rows=[]
    try:
        while time.monotonic()-start<a.seconds:
            before=time.monotonic()
            row={'elapsed':before-start}
            for key,address,size in [('read_and_globals',0x3eace0,128),('task',0x3f8e00,80),
                                     ('pack_header',0x3f3b00,64),('arena_header',0xa87800,32)]:
                row[key]=client.read(address,size).hex()
            row['read_duration']=time.monotonic()-before;rows.append(row)
            time.sleep(max(0,1/a.hz-(time.monotonic()-before)))
        result=dict(schema='hg-loading-capture/v1',timestamp_utc=datetime.now(timezone.utc).isoformat(),
                    metadata_before=meta,metadata_after=client.metadata(),requested_hz=a.hz,samples=rows,
                    limitation='Running sequential samples; not atomic, no program counter/call trace.')
        with a.output.open('x') as fp:json.dump(result,fp,indent=2);fp.write('\n')
        print(f'Captured {len(rows)} read-only loading samples')
    finally:client.close()


if __name__=='__main__':main()
