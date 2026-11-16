"""Selected setup comparison with independently captured original execution."""
import sys,json,ctypes as C,hashlib,argparse
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'tests')]
from resource_harness import Driver,Memory,machine,library,ResourceEnv
from verify_resource_loader import shared_module,EXPECTED
from test_actor_setup import configure
from hg_evidence_safety import digest
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--elf',type=Path,required=True);p.add_argument('--ps2dinfo',type=Path,required=True)
p.add_argument('--entry',type=Path,required=True);p.add_argument('--returned',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
opts=p.parse_args()
elf=opts.elf
if digest(elf)!=EXPECTED:raise ValueError('Wrong original ELF')
shared=shared_module(opts.ps2dinfo);data=elf.read_bytes();segments,_=shared.segments_from_elf(data)
packet=json.loads((ROOT/'analysis/actor_setup/functions.json').read_text());shared.verify(data,packet,require_all_bytes=True)
shared.verify(data,json.loads((ROOT/'analysis/actor_setup/math-dependencies.json').read_text()),require_all_bytes=True)
def capture(name):
 p=name;d=json.load(p.open());m=Memory(data,segments)
 if d['metadata_before']!=d['metadata_after'] or d['metadata_before']['status']!=1:
  raise ValueError('Capture is not a stable paused checkpoint')
 if d['metadata_before']['serial']!='SCPS-15046' or d['metadata_before']['uuid'].lower()!='91aa4b28':
  raise ValueError('Wrong runtime build')
 for s in d['samples']:
  raw=bytes.fromhex(s['bytes']);assert len(raw)==s['size_bytes'] and hashlib.sha256(raw).hexdigest()==s['sha256']
  m.put(int(s['address'],16),raw)
 return m,digest(p)
initial,beforehash=capture(opts.entry);expected,afterhash=capture(opts.returned)
class RuntimeDriver(Driver):
 def external(self,pc,a=0,b=0,c=0,d=0):
  if pc not in (0x1330f0,0x143e60):raise ValueError(f'Unresolved dependency {pc:#x}')
  self.calls.append((hex(pc),hex(a),hex(b)))
  m=machine(self)
  for i,v in enumerate((a,b,c,d),4):m.reg[i]=v
  return m.run(pc,limit=10000)&0xffffffff
lib=configure(library(output=opts.output.with_suffix('.so')))
lib.hg_actor_setup_fixed.argtypes=[C.POINTER(ResourceEnv)]+[C.c_uint32]*6
lib.hg_actor_setup_fixed.restype=None
d=RuntimeDriver(initial)
lib.hg_actor_setup_fixed(C.byref(d.env),0x4d8150,0x1000000,0x9000001,0,0,0)
assert not d.errors,d.errors
ranges=[(0x4d8150,0x3d0),(0x4d8140,8)]
top=0x4d8080
for i in range(2):ranges.append((initial.load(top-4*(i+1)),0x100))
for a,n in ranges:
 actual,want=d.mem.get(a,n),expected.get(a,n)
 if actual!=want:
  at=next(i for i in range(n) if actual[i]!=want[i])+a
  raise AssertionError((hex(at),d.mem.get(at,32).hex(),expected.get(at,32).hex()))
result=dict(result='passed',elf_sha256=EXPECTED,captures={'setup_entry_sha256':beforehash,'setup_return_sha256':afterhash},
 compared_ranges=[dict(address=hex(a),size_bytes=n,sha256=hashlib.sha256(d.mem.get(a,n)).hexdigest()) for a,n in ranges],
 external_calls=d.calls,limitations=['Recovered independently observed original-game captures, not fresh resumed execution.',
 'Setup reconstructed C with original-instruction-modeled 1330f0 and 143e60 math leaves; not all-C equivalence.',
 'Only selected setup call and compared ranges; input/active-update runtime equivalence not established.'])
result['verification_sources']={name:digest(ROOT/name) for name in (
 'src/resource_loader.c','src/resource_loader.h','tools/ee_subset.py',
 'tools/verify_actor_setup_runtime.py','analysis/actor_setup/functions.json',
 'analysis/actor_setup/math-dependencies.json')}
with opts.output.open('x') as out:json.dump(result,out,indent=2);out.write('\n')
print(json.dumps(result,indent=2))
