#!/usr/bin/env python3
"""Reanalyse paused original-game AREA loading, actor publication and reuse.

PC/register observations are manual screenshot transcriptions, never PINE PCs.
The legacy M5 comparison retains mocks. Optional M6 evidence checks real fixups.
"""
import argparse
import ctypes as C
import json
import struct
from pathlib import Path

from hg_evidence_safety import digest, receipt
from hg_identify import ISO, read_at
from verify_fixup_runtime import require, samples
from verify_resource_loader import EXPECTED, ROOT, shared_module
from verify_area_lifecycle import AREA_BASE, AREA_PATH, AREA_SHA256, verify_area1_bytes
from test_area_lifecycle import ActorDriver, configure, TASK
from resource_harness import Memory

IMAGE = '8ca1513c8417c0a40b838ff5edf22a2360f43bce1b0458693a95a6efdafd994e'
ACTOR = 0x4d8150
EVENTS = {
    'request': 0x130150, 'schedule': 0x143360, 'worker': 0x130220,
    'alternative': 0x131420, 'selection': 0x131810, 'read_context': 0x131810,
    'finalize': 0x131dd0, 'cache': 0x1041e0, 'alternative_return': 0x1317fc,
    'worker_exit': 0x103da0, 'actor': 0xa8a070, 'publication': 0x18c280,
    'later_request': 0x130150, 'later_worker': 0x130220, 'later_general': 0x130900,
    'later_complete': 0x1434f0, 'later_exit': 0x103da0,
    'reused_actor': 0xa8a070, 'republication': 0x18c280,
}


def word(buf, offset=0, width=4):
    return int.from_bytes(buf[offset:offset+width], 'little')


def check_profile(path,slot=28050):
    import configparser
    cfg = configparser.ConfigParser()
    cfg.read(path)
    for key in ('EnablePatches', 'EnableCheats', 'EnableWideScreenPatches', 'EnableNoInterlacingPatches'):
        require(not cfg.getboolean('EmuCore', key), 'Optional patch enabled')
    for key in ('Slot1_Enable', 'Slot2_Enable'):
        require(not cfg.getboolean('MemoryCards', key), 'Memory card enabled')
    require(cfg.getboolean('EmuCore', 'EnablePINE') and cfg.getint('EmuCore', 'PINESlot') == slot,
            'Unexpected isolated PINE configuration')


def load_observation(runtime, emulator, profile):
    path = runtime/'observation-resumed.json'
    obs = json.loads(path.read_text())
    require(obs['elf_sha256'] == EXPECTED and obs['image_sha256'] == IMAGE, 'Observation build mismatch')
    require(obs['emulator_sha256'] == digest(emulator), 'Emulator changed')
    require(obs['profile_sha256'] == digest(profile), 'Profile changed')
    check_profile(profile)
    require([(e['event'], int(e['pc'], 16)) for e in obs['observations']] == list(EVENTS.items()),
            'Missing or reordered debugger observations')
    captures, inputs = {}, {'observation': path}
    for event in obs['observations']:
        for kind in ('screenshot', 'capture'):
            p = runtime/event[kind]
            require(p.parent == runtime and digest(p) == event[kind+'_sha256'], 'Debugger artifact changed')
            inputs[p.name] = p
        captures[event['event']] = samples(runtime/event['capture'])
    for extra in obs['supplemental']:
        p = runtime/extra['capture']
        require(p.parent == runtime and digest(p) == extra['capture_sha256'], 'Supplemental capture changed')
        inputs[p.name] = p
        captures[p.stem] = samples(p)
    return obs, captures, inputs


class RetailAreaDriver(ActorDriver):
    def __init__(self, mem, fp):
        super().__init__()
        self.mem = mem
        self.fp = fp

    def read(self, lba, count, dst, mode):
        self.calls.append(('read', lba, count, dst, mode))
        require(0 < count <= 1024 and mode == b'\0\1\0', 'Unexpected retail transfer')
        self.mem.put(dst, read_at(self.fp, lba*2048, count*2048))
        return 1


def put_samples(mem, blocks):
    for address, data in blocks.items():
        mem.put(address, data)


def check_actor(lib, mem, before, after):
    """Stop before resident 18c280: compare the callback's defined publication."""
    d = ActorDriver()
    d.mem = mem
    put_samples(d.mem, before)
    require(before[ACTOR][0x18] == 1 and word(before[ACTOR], 0x1c) == 0xa8a070,
            'Wrong actor state or original callback')
    lib.hg_area1_actor(C.byref(d.env), ACTOR, d.setup)
    require(not d.errors, 'Actor bus errors: '+str(d.errors))
    require(d.mem.get(ACTOR, len(after[ACTOR])) == after[ACTOR], 'Actor memory differs')
    require(d.mem.get(0x3eb058, 12) == after[0x3eb054][4:16], 'Actor publication differs')
    require(d.calls == [('call', 0x119ac8, 0x3eb058, 0, 12), ('call', 0x18c280, 1)],
            'Wrong resident callback boundary')
    return [hex(d.mem.load(0x3eb058+i*4)) for i in range(3)]


def check_runtime(lib, data, segments, fp, captured, obs, ps2dinfo):
    c = {name: pair[1] for name, pair in captured.items()}
    regs = {e['event']: {k: int(v, 16) for k, v in e['registers'].items()} for e in obs['observations']}
    require(regs['request'] == {'a0': 2, 'a1': 0xc6}, 'Wrong initial request')
    require(regs['later_request'] == {'a0': 1, 'a1': 0x69}, 'Wrong retention request')
    # The recovered screenshot remains at the AREA helper. These arguments
    # come from the M1 saved request context, not from a setup-entry PC hit.
    require(regs['read_context'] == {'a0': TASK}, 'Wrong AREA polling argument')
    context = c['read_context'][0x3eace0]
    require(struct.unpack_from('<4I', context, 0x18) == (0x3f3a80, AREA_BASE, 0, 0xffffffff),
            'Wrong original AREA saved read context')
    require(regs['finalize'] == {'a0': AREA_BASE, 'a1': 30720} and regs['cache'] == {'a0': 2},
            'Wrong preparation arguments')
    require(all(regs[n] == {'a0': ACTOR} for n in ('actor', 'reused_actor')) and
            all(regs[n] == {'a0': 1} for n in ('publication', 'republication')), 'Wrong actor arguments')
    iso = ISO(fp)
    raw = iso.read(AREA_PATH)
    require(len(raw) == 30720 and __import__('hashlib').sha256(raw).hexdigest() == AREA_SHA256,
            'Wrong original AREA file')
    require((iso.files[AREA_PATH]['lba'], iso.files[AREA_PATH]['size_bytes']) == (1402368, 30720),
            'Wrong original AREA extent')
    previous = iso.read('/AREA0/A0_B09.BIN')
    require(len(previous) == 16384 and c['worker'][AREA_BASE] == previous,
            'Previous AREA bytes do not match the original A0_B09 file')
    raw_check = verify_area1_bytes(raw, json.loads((ROOT/'analysis/area_lifecycle/area1-functions.json').read_text()), ps2dinfo)
    # Every selected observation after loading retains the entire original file.
    retained = [n for n in EVENTS if n in c and AREA_BASE in c[n] and
                list(EVENTS).index(n) >= list(EVENTS).index('finalize')]
    for name in retained:
        require(c[name][AREA_BASE][:len(raw)] == raw, 'AREA changed at '+name)
    selection = c['selection']
    index = word(selection[0x3e01b0], selection[0x3fa1b0][0]*4)+selection[0x3fa1b0][1]
    require(index == 10 and struct.unpack_from('<2I', selection[0x3f3a30], index*8) == (1402368, 30720),
            'AREA selection differs')
    # The original finalizer reads +14 before FlushCache. This member needs no
    # zero tail; synthetic fixtures separately exercise nonzero lengths.
    require(word(raw, 0x14) == 0, 'Unexpected zero-tail length')
    code = c['later_general']
    resident_words = 0
    for fn in json.loads((ROOT/'analysis/area_lifecycle/functions.json').read_text())['functions']:
        for insn in fn['instructions']:
            address = int(insn['address'], 16)
            found = next((b[address-base:address-base+4] for base, b in code.items()
                          if base <= address and address+4 <= base+len(b)), None)
            require(found == bytes.fromhex(insn['bytes']), 'Runtime instruction differs at '+hex(address))
            resident_words += 1
    pack = iso.files['/DATA/PACK.DAT']
    # Reconstruct the actual AREA read and first PACK control operation. Marked
    # PACK fixups remain mocks here; their bytes are deliberately not compared.
    d = RetailAreaDriver(Memory(data, segments), fp)
    put_samples(d.mem, c['worker'])
    put_samples(d.mem, {a: selection[a] for a in (0x3f3a30, 0x3e01b0, 0x3fa1b0)})
    d.mem.store(0x3dff04, AREA_BASE)
    d.mem.store(0x3ead20, pack['lba']); d.mem.store(0x3ead24, pack['size_bytes'])
    lib.hg_pack_alternative(C.byref(d.env), TASK)
    require(not d.errors, 'AREA/PACK bus errors: '+str(d.errors))
    require(d.mem.get(AREA_BASE, len(raw)) == raw, 'Reconstructed AREA read differs')
    for address, size in ((0x3f4d3c, 36), (0x3f4300, 2600), (0x3f3b00, 2048)):
        require(d.mem.get(address, size) == c['alternative_return'][address][:size],
                'Initial PACK control state differs at '+hex(address))
    initial_reads = [list(x[1:4]) for x in d.calls if x[0] == 'read']
    fixups = [list(x[1:]) for x in d.calls if x[:2] == ('call', 0x137ed0)]
    require(len(fixups) == 13, 'Unexpected original PACK fixup prefix')
    require([x for x in d.calls if x[:2] == ('call', 0x1041e0)] == [('call', 0x1041e0, 2)],
            'Missing cache preparation')
    require(word(c['worker_exit'][TASK], 0x30, 2) == 0 and c['worker_exit'][0x3eace0][12] == 0,
            'Initial worker did not complete')
    publication = check_actor(lib, Memory(data, segments), c['actor'], c['publication'])
    # Compare the later loader's handles, task phase, arenas and retained AREA.
    # Its payload was sampled after menu use, not at the loader return; report
    # downstream changes without assigning them to an unobserved function.
    later = RetailAreaDriver(Memory(data, segments), fp)
    put_samples(later.mem, c['later_general'])
    later.mem.store(0x3ead20, pack['lba']); later.mem.store(0x3ead24, pack['size_bytes'])
    lib.hg_pack_general(C.byref(later.env), TASK)
    require(not later.errors, 'Later PACK bus errors: '+str(later.errors))
    for address, size in ((AREA_BASE, 31000), (0x3f4300, 2600), (0x3f4d3c, 36), (0x3f3b00, 2048)):
        require(later.mem.get(address, size) == c['later_complete'][address][:size],
                'Retention control state differs at '+hex(address))
    require(later.mem.get(TASK+0x32, 0x1e) == c['later_complete'][TASK][0x32:0x50], 'Later task differs')
    require(not any(x[:2] == ('call', 0x137ed0) for x in later.calls), 'Unexpected later fixup')
    bulk_base = later.mem.load(TASK+0x48)
    bulk_size = later.mem.load(0x3f3b14)
    require((bulk_base, bulk_size) == (0x17411c0, 295040), 'Unexpected later bulk extent')
    model_bulk = later.mem.get(bulk_base, bulk_size)
    runtime_bulk = c['resume-retained-bulk'][bulk_base]
    require(len(runtime_bulk) == bulk_size, 'Truncated later bulk capture')
    downstream_changes = [dict(offset=hex(i), loaded=hex(word(model_bulk, i)), observed=hex(word(runtime_bulk, i)))
                          for i in range(0, bulk_size, 4) if model_bulk[i:i+4] != runtime_bulk[i:i+4]]
    require(word(c['later_exit'][TASK], 0x30, 2) == 0 and c['later_exit'][0x3eace0][12] == 0,
            'Later worker did not complete')
    reused = check_actor(lib, Memory(data, segments), c['reused_actor'], c['republication'])
    return dict(area_index=index, area_sha256=AREA_SHA256, area_bytes_compared=len(raw),
        previous_area=dict(iso_path='/AREA0/A0_B09.BIN', bytes_compared=len(previous),
            sha256=__import__('hashlib').sha256(previous).hexdigest()),
        zero_tail_size=0, cache_argument=2, resident_runtime_words=resident_words,
        raw_area_byte_check=raw_check, retained_observations=retained,
        initial_read_ranges=initial_reads, initial_pack_fixup_boundaries=fixups,
        first_pack_control_comparison='passed; 13 fixup bodies mocked, PACK payload not compared',
        actor_publication=publication, reused_actor_publication=reused,
        later_read_ranges=[list(x[1:4]) for x in later.calls if x[0] == 'read'],
        later_bulk_bytes_inspected=bulk_size, later_bulk_sha256=__import__('hashlib').sha256(runtime_bulk).hexdigest(),
        later_bulk_comparison='Observed after menu consumption; downstream mutations unassigned; no payload equivalence claim',
        later_bulk_changed_words=downstream_changes,
        lifecycle='AREA1 loaded, cache prepared, callback executed/published; retained and reused across later PACK69 request',
        release_observed=False)


def pack_event_order(members):
    require(len(members)==13 and sum(len(r['geometry']) for r in members)==16,
            'M6 requires 13 fixups and 16 observed geometry calls')
    sequence=[('loader',0x131420)]
    for i,row in enumerate(members):
        sequence.append((f'before_{i}',0x137ed0))
        sequence.extend((f'geometry_{i}_{j}',0x1386e0) for j in range(len(row['geometry'])))
        sequence.append((f'after_{i}',0x1316ec))
    return sequence+[(n,pc) for n,pc in (
        ('loader_return',0x1317fc),('finish',0x1434f0),('exit',0x103da0),
        ('actor',0xa8a070),('published',0x18c280),('reuse_request',0x130150),
        ('reuse_general',0x130900),('reuse_finish',0x1434f0),('reuse_exit',0x103da0),
        ('reused_actor',0xa8a070),('republished',0x18c280))]


def check_pack_runtime(lib,data,segments,fp,runtime,emulator,profile):
    """M6: original-disc inputs -> real C fixups -> independent debugger output.

    Reuses the M5 driver, bus, capture validator and actor comparison. The later
    engine loaders remain explicit boundaries; no captured output feeds a fixup.
    """
    from test_resource_geometry import GeometryDriver, configure as geometry_configure
    from test_resource_fixup import FRAME
    import hashlib
    geometry_configure(lib)
    path=runtime/'observation.json';obs=json.loads(path.read_text())
    require(obs['schema']=='hg-m6-debugger-observation/v1' and obs['elf_sha256']==EXPECTED
            and obs['image_sha256']==IMAGE,'Wrong M6 observation build')
    require(obs['emulator_sha256']==digest(emulator) and obs['profile_sha256']==digest(profile),
            'M6 emulator/profile changed')
    check_profile(profile,28051)
    inputs={'observation':path,'emulator':emulator,'profile':profile}
    def artifact(event,kind):
        name=event[kind];p=runtime/name
        require(p.parent==runtime and p.name==name and digest(p)==event[kind+'_sha256'],
                'M6 debugger artifact changed')
        inputs[name]=p
        return p
    captured={e['event']:samples(artifact(e,'capture'))[1] for e in obs['observations']}
    for e in obs['observations']:artifact(e,'screenshot')
    regs={e['event']:{k:int(v,16) for k,v in e['registers'].items()} for e in obs['observations']}
    c=captured;base=0x1270000
    iso=ISO(fp);pack=iso.files['/DATA/PACK.DAT'];disc_base=pack['lba']*2048
    directory=read_at(fp,disc_base+0xc6*2048,2048)
    require(directory==c['before_0'][0x3f3b00] and word(directory)==0xc6
            and word(directory,4)==16 and word(directory,32)==13,'Wrong selected PACK directory')
    start,logical=word(directory,16),word(directory,20)
    length=(logical+2047)&~2047
    require((logical,length)==(1227648,1228800),'Selected PACK extent changed')
    raw=read_at(fp,disc_base+start,length)
    require(c['before_0'][base]==raw,'Complete paused input differs from original disc')
    # The first primary GPR panel was stale. Its arguments were separately
    # observed in the earlier attempt, with an identical full paused input.
    alt=obs['first_arguments']
    earlier=samples(artifact(alt,'capture'))[1];artifact(alt,'screenshot')
    require(earlier[base]==raw and alt['registers']=={'a0':'0x1270000','a1':'0x0','a2':'0x9d'}
            and int(alt['pc'],16)==0x137ed0,'Invalid first-argument corroboration')
    members=[]
    def sha(b):return hashlib.sha256(b).hexdigest()
    for i in range(13):
        offset,size,ident,_=struct.unpack_from('<4I',directory,64+i*16)
        at=offset-start;address=base+at
        source=read_at(fp,disc_base+offset,size)
        before=c[f'before_{i}'][base];after=c[f'after_{i}'][base]
        require(before[at:at+size]==source,'Member input is already modified')
        require(regs[f'before_{i}']==dict(a0=address,a1=0,a2=ident),'Wrong observed fixup arguments')
        d=GeometryDriver(Memory(data,segments),lib);d.mem.put(address,source)
        answer=lib.hg_resource_fixup(C.byref(d.env),address,0,ident,FRAME)
        require(not d.errors,'M6 direct bus errors: '+str(d.errors))
        output=d.mem.get(address,size)
        require(output==after[at:at+size],'Transformed member differs: '+hex(ident))
        require(regs[f'after_{i}']==dict(v0=answer),'M6 fixup return differs')
        require(before[:at]==after[:at] and before[at+size:]==after[at+size:],
                'Selected fixup changed another bulk range')
        observed=[];j=0
        while f'geometry_{i}_{j}' in regs:
            observed.append((regs[f'geometry_{i}_{j}']['a0'],regs[f'geometry_{i}_{j}']['a1']));j+=1
        require(d.geometry_calls==observed,'Geometry dependency order/arguments differ')
        for a in (TASK,0x3f4300,0x3f4d3c):
            require(c[f'before_{i}'][a]==c[f'after_{i}'][a], 'Fixup changed captured loader state')
        changed=[q for q in range(0,size,4) if source[q:q+4]!=output[q:q+4]]
        ranges=[]
        for q in changed:
            if ranges and ranges[-1][0]+ranges[-1][1]==q:ranges[-1][1]+=4
            else:ranges.append([q,4])
        members.append(dict(index=i,id=hex(ident),pack_offset=offset,address=hex(address),
            size_bytes=size,input_sha256=sha(source),output_sha256=sha(output),return_value=hex(answer),
            geometry=[dict(address=hex(a),delta=hex(b),kind=source[a-address+0x10]) for a,b in observed],
            model_entries=sum(word(source,16+k*4)>>24==1 for k in range(word(source,4))),
            entry_types=sorted(set(word(source,16+k*4)>>24 for k in range(word(source,4)))),
            texture_entries=sum(word(source,16+k*4)>>24==9 for k in range(word(source,4))),
            changed_words=len(changed),changed_bytes=sum(a!=b for a,b in zip(source,output)),
            modified_word_ranges=ranges))
        require(c['loader_return'][base][at:at+size]==output,'Later selected call changed an earlier member')
    require([(e['event'],int(e['pc'],16)) for e in obs['observations']]==pack_event_order(members),
            'Missing, reordered or unexpected M6 processing observation')

    class ProcessingRetail(GeometryDriver):
        def __init__(self,mem):
            super().__init__(mem,lib);self.fixups=[]
        def read(self,lba,count,dst,mode):
            require(0<count<=4096 and mode==b'\0\1\0','Unexpected M6 disc transfer')
            self.calls.append(('read',lba,count,dst,mode))
            self.mem.put(dst,read_at(fp,lba*2048,count*2048));return 1
        def external(self,pc,a=0,b=0,ident=0,d=0):
            if pc==0x137ed0:
                self.fixups.append((a,b,ident))
                return lib.hg_resource_fixup(C.byref(self.env),a,b,ident,FRAME)
            return super().external(pc,a,b,ident,d)
    def seeded(event,exclude_bulk=True):
        d=ProcessingRetail(Memory(data,segments))
        # Bulk results are NOT supplied to either reconstructed load.
        put_samples(d.mem,{a:b for a,b in c[event].items() if not exclude_bulk or a!=base})
        d.mem.store(0x3dff04,AREA_BASE)
        return d
    d=seeded('loader');lib.hg_pack_alternative(C.byref(d.env),TASK)
    require(not d.errors,'M6 integrated bus errors: '+str(d.errors))
    require(d.fixups==[(int(r['address'],16),0,int(r['id'],16)) for r in members],
            'Integrated loader did not execute all 13 real fixups')
    require(d.mem.get(base,length)==c['loader_return'][base],'Complete postprocessing buffer differs')
    for address,size in ((TASK,80),(0x3f3b00,2048),(0x3f4300,2600),(0x3f4d3c,36)):
        require(d.mem.get(address,size)==c['loader_return'][address][:size],
                'M6 loader publication differs at '+hex(address))
    require([x for x in d.calls if x[:2]==('call',0x1041e0)]==[('call',0x1041e0,2)],
            'M6 cache preparation differs')
    worker=seeded('loader');lib.hg_resource_worker_fixed(C.byref(worker.env),FRAME)
    require(not worker.errors and not worker.fixups and not worker.geometry_calls,
            'Connected worker used an external processing substitute')
    require(worker.mem.get(base,logical)==c['loader_return'][base][:logical],
            'Worker did not retain calculated selected contents')
    require(c['exit'][base][:logical]==c['loader_return'][base][:logical],
            'Retail worker changed logical selected buffer')
    for row in members:
        handle=0x3f4300+int(row['id'],16)*4
        require(worker.mem.load(handle)==word(c['exit'][0x3f4300],handle-0x3f4300)==int(row['address'],16),
                'Selected handle not retained at worker completion')
    for event in ('exit','reuse_exit'):
        require(word(c[event][TASK],0x30,2)==0 and c[event][0x3eace0][12]==0,
                'Original worker did not complete')
    require(worker.mem.load(TASK+0x30,2)==0 and worker.mem.load(0x3eacec,1)==0,
            'Reconstructed worker did not complete')
    require(regs['reuse_request']==dict(a0=1,a1=0x69),'Wrong reuse request')
    later=seeded('reuse_general',False);lib.hg_resource_worker_fixed(C.byref(later.env),FRAME)
    require(not later.errors,'Reuse worker errors: '+str(later.errors))
    for address,size in ((0x3f4300,2600),(0x3f4d3c,36)):
        require(later.mem.get(address,size)==c['reuse_exit'][address][:size],'Reuse publication differs')
    require(later.mem.get(base,logical)==c['reuse_exit'][base][:logical], 'Reuse changed retained payload')
    area=iso.read(AREA_PATH)
    retained=[n for n in c if n!='loader']
    require(all(c[n][AREA_BASE][:len(area)]==area for n in retained),'AREA1 bytes changed')
    def actor_pair(before,after):
        a,b=dict(c[before]),dict(c[after])
        for s in (a,b):s[ACTOR]=s[0x4d0000][ACTOR-0x4d0000:ACTOR-0x4d0000+0x300]
        return check_actor(lib,Memory(data,segments),a,b)
    actor=actor_pair('actor','published');reused=actor_pair('reused_actor','republished')
    code=samples(artifact(obs['runtime_code'],'capture'))[1];verified=0
    packet_path=ROOT/'analysis/pack_finalization/functions.json'
    inputs['instruction_packet']=packet_path
    packet=json.loads(packet_path.read_text())
    for fn in packet['functions']:
        for ins in fn['instructions']:
            address=int(ins['address'],16)
            found=next((b[address-a:address-a+4] for a,b in code.items() if a<=address and address+4<=a+len(b)),None)
            require(found==bytes.fromhex(ins['bytes']),'M6 resident runtime instruction changed')
            verified+=1
    report=dict(result='passed',members=members,marked_members=13,geometry_calls=16,upload_calls=0,
        member_bytes_compared=sum(r['size_bytes'] for r in members),
        bulk_read_bytes_compared=length,logical_bulk_bytes_retained=logical,
        bulk_input_sha256=sha(raw),bulk_output_sha256=sha(c['loader_return'][base]),
        logical_output_sha256=sha(c['loader_return'][base][:logical]),
        runtime_instruction_words=verified,
        handle_table_bytes_compared=2600,arena_bytes_compared=36,
        arena_at_selected_return=[hex(word(c['loader_return'][0x3f4d3c],i)) for i in range(0,36,4)],
        worker_completion='selected payload, 13 handles, busy=0, active=0 match; later engine arena/table effects outside selected PACK comparison',
        integrated_reads=[list(x[1:4]) for x in d.calls if x[0]=='read'],
        retained_area_bytes=len(area),area_sha256=sha(area),actor_publication=actor,reused_publication=reused,
        observation_count=len(c),provenance_limit=obs['provenance_limit'])
    return report,inputs


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for n in ('elf', 'image', 'runtime', 'ps2dinfo', 'emulator', 'profile', 'output'):
        p.add_argument('--'+n, type=Path, required=True)
    p.add_argument('--pack-runtime',type=Path,help='M6 independently observed processing checkpoints')
    p.add_argument('--pack-profile',type=Path,help='Frozen M6 isolated slot28051 profile')
    p.add_argument('--source',type=Path,help='Disposable C source for compiled negative controls')
    a = p.parse_args()
    require(bool(a.pack_runtime)==bool(a.pack_profile),'M6 requires both --pack-runtime and --pack-profile')
    require(digest(a.elf) == EXPECTED and digest(a.image) == IMAGE, 'Wrong original inputs')
    obs, captured, inputs = load_observation(a.runtime, a.emulator, a.profile)
    data = a.elf.read_bytes()
    shared = shared_module(a.ps2dinfo)
    segments, _ = shared.segments_from_elf(data)
    from resource_harness import library
    lib = configure(library(source=a.source,output=ROOT/'local/m5/retail-area.so'))
    with a.image.open('rb') as fp:
        result = check_runtime(lib, data, segments, fp, captured, obs, a.ps2dinfo)
        if a.pack_runtime:
            pack_result,pack_inputs=check_pack_runtime(lib,data,segments,fp,a.pack_runtime,a.emulator,a.pack_profile)
            result['m6_processing']=pack_result
            inputs.update({'m6_'+k:v for k,v in pack_inputs.items()})
    if a.source:inputs['tested_c_source']=a.source
    first = captured['finalize'][0]
    post = captured['republication'][0]
    result.update(schema='hg-m5-runtime-comparison/v1', result='passed',
        evidence_level='saved-debugger-observation-reanalysis',
        debugger_sequence=[{k: e[k] for k in ('event', 'pc', 'registers')} for e in obs['observations']],
        binding=receipt(ROOT, dict(elf=a.elf, image=a.image, emulator=a.emulator, profile=a.profile,
            raw=a.runtime/'area1-finalizer.json', post=a.runtime/'resume-republished.json', **inputs),
            'saved-runtime', ['verify_area_runtime.py'], [
                obs['provenance_limit'],
                'Breakpoint snapshots with manually transcribed PCs/registers; not exhaustive call/instruction tracing.',
                'AREA1 state1 publication/reuse observed; state0 initialization has instruction-model evidence only.',
                'Initial PACKc6 has 13 mocked fixups: no geometry/upload or full PACK payload equivalence claim.',
                'Actor construction/dispatch and resident18c280 remain explicit engine boundaries.',
                'Kernel/CDVD timing modeled; no concurrent-memory equivalence, memory free or other AREA variant claim.'],
            runtime=dict(before=first['metadata_before'], after=first['metadata_after'],
                post_before=post['metadata_before'], post_after=post['metadata_after'])))
    with a.output.open('x') as f:
        json.dump(result, f, indent=2); f.write('\n')
    print('PASS: original AREA1 read/preparation, callback publication, retention/reuse; PACK control state')
    if a.pack_runtime:print('PASS: 13 real M6 fixups, complete transformed bulk, handles/arena, worker completion and fresh AREA1 reuse')


if __name__ == '__main__':
    main()
