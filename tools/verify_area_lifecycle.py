#!/usr/bin/env python3
"""M5 general PACK/AREA original-byte and bounded instruction comparisons.

Extends the M1-M4 bus/interpreter and keeps CDVD/kernel/fixup boundaries explicit.
A successful result is model evidence, not a retail activation observation.
"""
import argparse
import ctypes as C
import hashlib
import importlib.util
import json
import random
import sys
from pathlib import Path
from hg_evidence_safety import digest, select_export, receipt
from verify_resource_loader import ROOT, EXPECTED, shared_module, compare, clone
sys.path.insert(0, str(ROOT/'tests'))
from resource_harness import Driver, Memory, ResourceEnv, library, machine, synthetic_pack

FUNCTIONS = {
    'general': (0x130900, 'hg_pack_general', 1),
    'alternative': (0x131420, 'hg_pack_alternative', 1),
    'clear_dynamic': (0x12fb30, 'hg_resource_clear_dynamic_handles', 0),
    'clear_selected': (0x12fb90, 'hg_resource_clear_selected_handles', 0),
}
TASK = 0x3f8e00


AREA_BASE = 0xa87800
AREA_PATH = '/AREA1/A1_B00.BIN'
AREA_SHA256 = '8ec765c77cb432b8dbe29c3ef8daa99d23ff0dcfdca939636ffc7b8b78cfa788'
AREA_FUNCTIONS = {
    'area1_actor': (0xa8a070, 'hg_area1_actor', 1),
}
CALL6 = C.CFUNCTYPE(C.c_uint32, C.c_void_p, *([C.c_uint32]*7))
RESIDENT_AREA_FUNCTIONS = {'actor_data_init':(0x18c220,'hg_actor_data_init',2)}
AREA_EXTERNAL_ARITIES = {0x18c280:1, 0x142e90:1, 0x144340:3,
    0x1235c0:2, 0x123660:2, 0x139c90:2}


def raw_area_evidence(exports):
    packet = dict(schema='ps2-evidence/v1', program=dict(game='Hungry Ghosts',
        build_id='japan-area1-a1-b00', executable=None, sha256=AREA_SHA256,
        processor='EE/R5900', reported_language='r5900:LE:32:default',
        endianness='little', instruction_encoding='ee-mips32', address_space='ram',
        load_address=f'0x{AREA_BASE:08x}',
        address_context='Raw AREA1/A1_B00.BIN mapped at independently measured retail destination; not an ELF PT_LOAD.',
        raw_resource=dict(iso_path=AREA_PATH, lba=1402368, size_bytes=30720,
            source_elf_sha256=EXPECTED)),
        provenance=dict(classification='observed', observation_date='2026-11-14',
            source='Read-only original-image file and isolated raw Ghidra import; exact selected resource bytes checked independently of ELF.',
            tool='Ghidra HGExport.java', tool_version='12.1.4',
            retrieval_method='Fresh raw import at measured destination, read-only/noanalysis function export',
            truncated=False, limitations=[
                'Raw file mapping is checked directly; ELF-only verifier does not apply to these functions.',
                'Engine rendering/registration operations remain external; no floating-point or complete actor-system equivalence.',
                'An instruction match alone does not prove retail control reached the mapped entry.']), functions=[])
    paths = {}
    for name, (entry, symbol, _) in AREA_FUNCTIONS.items():
        path = select_export(exports, entry)
        paths[f'area_export_{entry:08x}'] = path
        row = json.loads(path.read_text())
        packet['functions'].append(dict(entry=row['entry'], name=symbol, complete=True,
            body_ranges=row['body_ranges'], instructions=row['instructions'],
            references=[{**r, 'ghidra_type':r['type'], 'type':'call' if 'CALL' in r['type'] else 'unknown'} for r in row['references_to']],
            interpretation='Observed AREA1 actor dispatch, state0 initialization and state1 publication; external engine operations explicit.',
            verification_status='original-byte-verified', source='src/resource_loader.c',
            original_listing_sha256=row['listing_sha256'], snapshot_sha256=digest(path)))
    return packet, paths


def verify_area1_bytes(raw, packet, ps2dinfo):
    """Selected game-owned raw mapping; deliberately never fabricate an ELF."""
    pins = json.loads((ROOT/'evidence/m2/shared-tools.json').read_text())
    validator = ps2dinfo/'tools/validate_evidence.py'
    if digest(validator) != pins['tools']['validate_evidence.py']:
        raise ValueError('Shared structural validator hash mismatch')
    spec = importlib.util.spec_from_file_location('ps2_area_structure', validator)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    errors = module.validate(packet)
    if errors:
        raise ValueError('Invalid raw AREA evidence structure: '+str(errors))
    if hashlib.sha256(raw).hexdigest() != AREA_SHA256 or packet['program']['sha256'] != AREA_SHA256:
        raise ValueError('Wrong selected AREA resource')
    if len(raw) != 30720 or raw[:4] != b'MWo3' or int.from_bytes(raw[8:12],'little') != AREA_BASE:
        raise ValueError('Unexpected AREA source size/header destination')
    program = packet['program']
    expected_resource = dict(iso_path=AREA_PATH,lba=1402368,size_bytes=30720,source_elf_sha256=EXPECTED)
    if program.get('executable') is not None or program.get('load_address') != f'0x{AREA_BASE:08x}' or program.get('raw_resource') != expected_resource:
        raise ValueError('Unexpected raw AREA mapping metadata')
    if {int(f['entry'],16) for f in packet['functions']} != {v[0] for v in AREA_FUNCTIONS.values()}:
        raise ValueError('Unexpected selected raw AREA function entries')

    count = 0
    for function in packet['functions']:
        for instruction in function['instructions']:
            address = int(instruction['address'],16)
            encoded = bytes.fromhex(instruction['bytes'])
            offset = address-AREA_BASE
            if len(encoded)!=4 or offset<0 or offset+4>len(raw) or raw[offset:offset+4]!=encoded:
                raise ValueError(f'Raw AREA instruction byte mismatch at {address:08x}')
            count += 1
    return dict(schema='hg-area-raw-byte-check/v1', result='passed',
        raw_sha256=AREA_SHA256, instructions_verified=count, instructions_unknown=0,
        mapping=dict(iso_path=AREA_PATH, lba=1402368, size_bytes=len(raw),
            destination=f'0x{AREA_BASE:08x}', file_offset_at_destination=0),
        scope='Selected raw file offsets only; no ELF claim, semantic equivalence or retail execution promotion.')


def load_area1(image):
    from hg_identify import ISO
    expected_image = json.loads((ROOT/'evidence/builds/japan.json').read_text())['image']['sha256']
    if digest(image) != expected_image:
        raise ValueError('Wrong original image')
    with image.open('rb') as stream:
        iso = ISO(stream)
        rec = iso.files[AREA_PATH]
        if rec['lba'] != 1402368 or rec['size_bytes'] != 30720:
            raise ValueError('Unexpected selected AREA file identity')
        raw = iso.read(AREA_PATH)
    if hashlib.sha256(raw).hexdigest() != AREA_SHA256:
        raise ValueError('Selected AREA file digest mismatch')
    return raw


class AreaActorDriver(Driver):
    def __init__(self, mem):
        super().__init__(mem)
        self.mutate_engine = False
        self.call6 = CALL6(self._call6)

    def _call6(self, _, pc, a0, a1, a2, a3, a4, a5):
        self.calls.append(('call6',pc,a0,a1,a2,a3,a4,a5))
        if pc != 0x18a330:
            self.errors.append('Unexpected six-register engine boundary')
        if self.mutate_engine:
            # Explicit setup mutation remains visible in returned actor memory.
            self.mem.store(a0+0x2d0,0x12345678)
        return 0

    def external(self, pc, a=0, b=0, c=0, d=0):
        if pc in AREA_EXTERNAL_ARITIES:
            self.calls.append(('call',pc,*(a,b,c,d)[:AREA_EXTERNAL_ARITIES[pc]]))
            if pc == 0x139c90 and self.mutate_engine:
                self.mem.store(a+0x18,255,1)
            return 0
        return super().external(pc,a,b,c,d)


def check_area1(lib, data, segments, raw, packet, coverage):
    for name, (_, symbol, arity) in {**AREA_FUNCTIONS,**RESIDENT_AREA_FUNCTIONS}.items():
        fn = getattr(lib,symbol)
        fn.argtypes = [C.POINTER(ResourceEnv)]+[C.c_uint32]*arity+([CALL6] if name=='area1_actor' else [])
        fn.restype = None
    combined = data+raw
    mapping = segments+[(AREA_BASE,AREA_BASE+len(raw),len(data))]
    rng = random.Random(0xa8a070)
    counts = {'area1_actor':0,'actor_data_init':0}
    functions = {**AREA_FUNCTIONS,**RESIDENT_AREA_FUNCTIONS}
    for name in functions:
        for case in range(128):
            mem = Memory(combined,mapping)
            actor = [0x500000,0x601000,0x1fff000][case%3]
            mem.put(actor,bytes(rng.getrandbits(8) for _ in range(0x340)))
            # Dispatch unusual values as bytes, not signed chars.
            state = [0,1,2,3,4,127,128,255][case%8]
            mem.store(actor+0x18,state,1)
            mem.put(0x3eb058,bytes(rng.getrandbits(8) for _ in range(16)))
            cd,md = AreaActorDriver(clone(mem)),AreaActorDriver(clone(mem))
            cd.mutate_engine = md.mutate_engine = bool((case//8)%2)
            args = [actor] if name=='area1_actor' else [actor,actor+0x2d0]
            getattr(lib,functions[name][1])(C.byref(cd.env),*args,*([cd.call6] if name=='area1_actor' else []))
            if cd.errors:
                raise AssertionError(cd.errors)
            m = machine(md)
            for pc in AREA_EXTERNAL_ARITIES:
                m.hooks[pc] = lambda cpu,pc=pc:md.external(pc,*(cpu.reg[i]&0xffffffff for i in (4,5,6,7)))
            m.hooks[0x18a330] = lambda cpu:md._call6(None,0x18a330,*(cpu.reg[i]&0xffffffff for i in range(4,10)))
            for n,value in enumerate(args,4):
                m.reg[n] = value
            saved = {n:(0x123456789abcdef0<<64)|(n*0x1234567) for n in list(range(16,24))+[30]}
            for n,value in saved.items():
                m.reg[n] = value
            saved.update({28:m.reg[28],29:m.reg[29]})
            m.run(functions[name][0])
            for n,value in saved.items():
                if m.reg[n]!=value:
                    raise AssertionError(f'{name}: original ABI register {n} not restored')
            compare(cd,md,name)
            coverage.update(m.executed)
            counts[name] += 1
    bodies = {int(i['address'],16) for f in packet['functions'] for i in f['instructions']}
    return counts, dict(executed=len(bodies&coverage),total=len(bodies),
        not_executed=[f'0x{x:08x}' for x in sorted(bodies-coverage)])


def configure(lib):
    for _, symbol, arity in FUNCTIONS.values():
        f = getattr(lib, symbol)
        f.argtypes = [C.POINTER(ResourceEnv)] + [C.c_uint32] * arity
        f.restype = None
    f = lib.hg_pack_general_prepare
    f.argtypes = [C.POINTER(ResourceEnv), C.c_uint32]
    f.restype = None
    return lib


def evidence(general_exports, alternative_exports, area_exports, actor_exports=None):
    packet = json.loads((ROOT/'analysis/resource_loader/functions.json').read_text())
    packet.pop('binding', None)
    packet['functions'] = []
    packet['provenance'].update(
        source='HGExport from preserved M2 project; original ELF checked with pinned verifier.',
        retrieval_method='headless Ghidra API; fresh helper export read-only/noanalysis; established M2 general-loader export',
        limitations=['Analyst names; CDVD/kernel/fixup operations remain explicit model boundaries.',
                     'General-loader host preparation/step helpers are not original function entries.',
                     'Original-byte verification and instruction comparison do not establish retail runtime activation.'])
    paths = {}
    selected_functions = {**FUNCTIONS,**(RESIDENT_AREA_FUNCTIONS if actor_exports else {})}
    for name, (entry, symbol, _) in selected_functions.items():
        path = select_export(general_exports if name == 'general' else alternative_exports if name == 'alternative' else actor_exports if name in RESIDENT_AREA_FUNCTIONS else area_exports, entry)
        paths[f'export_{entry:08x}'] = path
        row = json.loads(path.read_text())
        packet['functions'].append(dict(
            entry=row['entry'], name=symbol, complete=True,
            body_ranges=row['body_ranges'], instructions=row['instructions'],
            references=[{**r, 'ghidra_type':r['type'], 'type':'call' if 'CALL' in r['type'] else 'unknown'} for r in row['references_to']],
            interpretation='M5 PACK loading, handle invalidation or resident actor-data preparation; scope in analysis/area_lifecycle/README.md.',
            verification_status='original-byte-verified', source='src/resource_loader.c',
            original_listing_sha256=row['listing_sha256'], snapshot_sha256=digest(path)))
    return packet, paths


class AreaDriver(Driver):
    def __init__(self, mem):
        super().__init__(mem)
        self.resume_phase = None

    def external(self, pc, a=0, b=0, c=0, d=0):
        # A waking-thread fixture can mutate task state at SleepThread. This is
        # an explicit external effect, not validation behavior added to the game.
        answer = super().external(pc, a, b, c, d)
        if pc == 0x1434e0 and self.resume_phase is not None:
            self.mem.store(TASK+0x39, self.resume_phase, 1)
            self.resume_phase = None
        return answer


def fixture(data, segments, identifier=0x102, *, phase=0, area_index=0):
    mem = Memory(data, segments)
    mem.put(TASK, bytes(80))
    mem.store(TASK+0x44, identifier)
    mem.store(TASK+0x39, phase, 1)
    mem.store(TASK+0x4c, 0x3f3b40)
    for i, address in enumerate(range(0x3f4d3c, 0x3f4d60, 4)):
        mem.store(address, 0xc00000+i*0x10000)
    mem.store(0x3dff04, 0xa87800)
    mem.store(0x3fa1b0, area_index//3, 1)
    mem.store(0x3fa1b1, area_index%3, 1)
    for i in range(3):
        mem.store(0x3e01b0+4*i, i*3)
    for i in range(22):
        mem.store(0x3f3a30+8*i, 2000+i*4)
        mem.store(0x3f3a34+8*i, 2048)
    return mem


def paired(seed, *, sync_busy=0, read_failures=0):
    cd, md = AreaDriver(clone(seed)), AreaDriver(clone(seed))
    for d in (cd, md):
        d.sync_busy = sync_busy
        d.read_failures = read_failures
    return cd, md


def install_files(cd, md, identifier=0x102, area_size=2048):
    data = bytes(synthetic_pack(identifier))
    for d in (cd, md):
        d.mem.store(0x3ead20, 1000)
        d.mem.store(0x3ead24, len(data))
        d.files[1000] = data
        for i in range(22):
            area = bytearray(((j*17+i*31)&255) for j in range(area_size))
            area[:4] = b'MWo3'
            area[0x14:0x18] = (32+i).to_bytes(4, 'little')
            d.files[2000+i*4] = bytes(area)
            d.mem.store(0x3f3a34+8*i, area_size)


def execute(lib, cd, md, name, coverage, *, prepare=False):
    entry, symbol, arity = FUNCTIONS[name]
    if prepare:
        lib.hg_pack_general_prepare(C.byref(cd.env), TASK)
    else:
        getattr(lib, symbol)(C.byref(cd.env), *([TASK] if arity else []))
    if cd.errors:
        raise AssertionError(cd.errors)
    m = machine(md)
    m.hooks.pop(0x130900, None)
    m.hooks.pop(0x131420, None)
    m.reg[4] = TASK
    saved = {n:(0x123456789abcdef0<<64)|(n*0x1234567) for n in list(range(16,24))+[30]}
    for n, v in saved.items():
        m.reg[n] = v
    saved.update({28:m.reg[28], 29:m.reg[29]})
    m.run(entry, stop=0x131080 if prepare else 0xfff000, limit=300000)
    if not prepare:
        for n, v in saved.items():
            if m.reg[n] != v:
                raise AssertionError(f'{name}: original ABI register {n} not restored')
    compare(cd, md, name + (' prepare' if prepare else ''))
    coverage.update(m.executed)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for n in ('elf','general-exports','alternative-exports','area-exports','ps2dinfo','output'):
        p.add_argument('--'+n, type=Path, required=True)
    p.add_argument('--packet', type=Path)
    p.add_argument('--image',type=Path,help='Authenticated original image for selected raw AREA consumer checks')
    p.add_argument('--area-code-exports',type=Path)
    p.add_argument('--actor-dependency-exports',type=Path)
    p.add_argument('--area-code-packet',type=Path)
    p.add_argument('--only-area-code',action='store_true',help='Skip loader differential calls while iterating on selected raw consumer')
    p.add_argument('--source', type=Path, help='Disposable C source for a behavioral negative control')
    a = p.parse_args()
    if digest(a.elf) != EXPECTED:
        raise ValueError('Wrong original executable')
    shared = shared_module(a.ps2dinfo)
    data = a.elf.read_bytes()
    segments, endian = shared.segments_from_elf(data)
    if endian != 'little':
        raise ValueError('Unexpected original endian')
    packet, paths = evidence(a.general_exports, a.alternative_exports, a.area_exports, a.actor_dependency_exports)
    byte_check = shared.verify(data, packet, require_all_bytes=True)
    if a.packet:
        with a.packet.open('x') as f:
            json.dump(packet, f, indent=2); f.write('\n')
    lib = configure(library(source=a.source, output=ROOT/'local/m5/area-differential.so'))
    coverage = set()
    counts = {'general':0, 'general_prepare':0, 'alternative':0, 'clear_dynamic':0, 'clear_selected':0}
    rng = random.Random(0x130900)

    def check(name, mem, *, prepare=False, cfg=None, identifier=None, area_size=2048):
        cd, md = paired(mem, **(cfg or {}))
        if identifier is not None:
            install_files(cd, md, identifier, area_size)
        execute(lib, cd, md, name, coverage, prepare=prepare)
        counts[name+'_prepare' if prepare else name] += 1
        return cd, md

    if not a.only_area_code:
        # Every selector ID, signed boundary IDs, and all scratchpad/variant choices.
        # Preparation reaches 131080 before the first state-machine instruction.
        for identifier in list(range(0x112)) + [0xffffffff,0x80000000,0x7fffffff]:
            for variant in (0,1,2,3):
                mem = fixture(data, segments, identifier)
                mem.store(0x46e136, variant, 2)
                mem.store(TASK+0x3e, 0x9876, 2)
                mem.store(0x700000c4, variant)
                mem.store(0x3f4d5c, 0 if variant%2 else 0xc80000)
                check('general', mem, prepare=True)

        # Full AREA -> PACK -> publication including retries and each mode8 ID.
        for case in range(45):
            identifier = 0x102+case%15
            mem = fixture(data, segments, identifier, area_index=case%9)
            mem.put(0xa88000, b'\xa5'*128) # independently check zero-tail effects
            check('general', mem, cfg={'sync_busy':case%3,'read_failures':case%3}, identifier=identifier,
                  area_size=(2048,2049,4095,4096)[case%4])

        # Representative non-AREA full calls cover the alternate phase0 path and
        # common PACK arena modes while retaining their explicit fixup boundaries.
        for identifier in (1,4,7,9,5,0x10,0x4a,0x6c,0x50,0x13,0x6e,0x85):
            for scratch in (0,1,2):
                mem = fixture(data, segments, identifier)
                mem.store(0x700000c4, scratch)
                mem.store(0x46e136, scratch, 2)
                check('general', mem, identifier=identifier)

        # Immediate phase7 publication covers arena alignment/32-bit wrap without
        # dereferencing impossible transfer destinations. It is an original branch.
        mode_ids = [1,4,9,0x4a,5,0x102,0x10,0x6c,0x20,0x50]
        for identifier in mode_ids:
            for size in (0,1,0x7ff,0x800,0x801,0xfffffff0):
                mem = fixture(data, segments, identifier, phase=7)
                mem.store(0x3f3b08, 0)
                mem.store(0x3f3b14, size)
                mem.store(0x3f4444, rng.getrandbits(32))
                for address in range(0x3f4d3c, 0x3f4d60, 4):
                    mem.store(address, rng.choice([0xfffffff0,0x1001,0xb00000]))
                check('general', mem)

        # Enter each original state directly. Unsupported values stay asleep until
        # the explicit external wake fixture changes the phase to empty-directory 4.
        for phase in (0,1,2,3,4,5,6,7,8,127,128,255):
            identifier = 0x102
            mem = fixture(data, segments, identifier, phase=phase)
            mem.store(0x3f3b04,0)
            mem.store(0x3f3b08,0)
            mem.store(0x3eacf0,0)
            cd, md = paired(mem)
            install_files(cd,md,identifier)
            if phase>=8:
                cd.resume_phase = md.resume_phase = 4
            execute(lib,cd,md,'general',coverage)
            counts['general'] += 1

        # The independently discovered startup kind2 path uses 00131420 rather than
        # 00130900. Full alternative calls cover AREA only for kind2, selected-handle
        # clearing for other kinds, completion-flag effects and its raw arena ends.
        for kind in (0,1,2,3,0xffff):
            for state in ((0,0,0),(0,2,1),(0,2,0),(0,1,1),(1,2,1)):
                for retry in (0,2):
                    mem = fixture(data, segments)
                    mem.store(TASK+0x32,kind,2)
                    for i,value in enumerate(state):
                        mem.store(0x3fa1b0+i,value,1)
                    check('alternative',mem,cfg={'sync_busy':retry,'read_failures':retry},
                          identifier=0x102,area_size=2049 if retry else 2048)
        # Direct phase4 empty directories and phase7 arena calculations distinguish
        # the alternative loader's unrounded arithmetic from general mode8 alignment.
        for phase in (4,7):
            for kind in (0,2,3,0xffff):
                for state in ((0,0,0),(0,2,1),(0,2,0),(0,1,1),(1,2,1)):
                    for end in (0,1,0x7ff,0x800,0x801,0xfffffff0):
                        mem = fixture(data,segments,phase=phase)
                        mem.store(TASK+0x32,kind,2)
                        mem.store(TASK+0x48,end)
                        mem.store(0x3f3b14,0x801)
                        for i,value in enumerate(state):
                            mem.store(0x3fa1b0+i,value,1)
                        check('alternative',mem)
        for phase in (0,1,2,3,4,5,6,7,8,127,128,255):
            for kind in (2,3):
                mem = fixture(data,segments,phase=phase)
                mem.store(TASK+0x32,kind,2)
                cd,md = paired(mem)
                install_files(cd,md)
                if phase>=8:
                    cd.resume_phase = md.resume_phase = 4
                execute(lib,cd,md,'alternative',coverage)
                counts['alternative'] += 1
        # Member IDs remain defined through a handle/descriptor alias: the handle
        # store changes descriptor+8 but must not change the fixup's a2 argument.
        for name in ('general','alternative'):
            mem = fixture(data,segments,phase=7)
            mem.store(TASK+0x44,4) # general mode0 leaves this alias table intact
            mem.store(TASK+0x32,3,2)
            mem.store(TASK+0x48,0xb00000)
            mem.store(TASK+0x4c,0x3f431c)
            mem.store(0x3f4324,9)
            mem.store(0x3f431c,0)
            mem.store(0x3f3b08,1)
            mem.store(0x3f3b20,1)
            check(name,mem)

        # Both invalidation helpers compare exact original store ranges, including
        # sentinel words immediately outside them and an arbitrary initial table.
        for name in ('clear_dynamic','clear_selected'):
            for _ in range(32):
                mem = fixture(data, segments)
                mem.put(0x3f42fc, bytes(rng.getrandbits(8) for _ in range(0xb40)))
                check(name, mem)

    bodies = {int(i['address'],16) for f in packet['functions'] for i in f['instructions']}
    area_check = None
    area_coverage = None
    if a.image or a.area_code_exports or a.area_code_packet or a.only_area_code:
        if not a.image or not a.area_code_exports or not a.actor_dependency_exports:
            raise ValueError('Raw AREA checks require --image, --area-code-exports and --actor-dependency-exports together')
        raw = load_area1(a.image)
        area_packet, area_paths = raw_area_evidence(a.area_code_exports)
        area_check = verify_area1_bytes(raw,area_packet,a.ps2dinfo)
        if a.area_code_packet:
            with a.area_code_packet.open('x') as f:
                json.dump(area_packet,f,indent=2);f.write('\n')
        area_counts, area_coverage = check_area1(lib,data,segments,raw,area_packet,coverage)
        counts.update(area_counts)
        paths.update(area_paths)
        paths['original_image'] = a.image
    report = dict(schema='hg-area-instruction-check/v1', result='passed', seed='0x130900',
        cases=counts, total_cases=sum(counts.values()), byte_check=byte_check,
        loader_differential_status='not run' if a.only_area_code else 'passed',
        area_byte_check=area_check,area_instruction_coverage=area_coverage,
        instruction_coverage={'executed':len(bodies&coverage),'total':len(bodies),
            'not_executed':[f'0x{x:08x}' for x in sorted(bodies-coverage)]},
        binding=receipt(ROOT, {'elf':a.elf,'shared_verifier':a.ps2dinfo/'tools/verify_elf_evidence.py',
            **paths, **({'tested_c_source':a.source} if a.source else {})},
            'differential',['verify_area_lifecycle.py'],[
                'Kernel/CDVD/fixup operations use the existing deterministic external mocks.',
                'No concurrent interrupts or overlapping destination/read-context aliases modeled.',
                'Non-stack RAM/scratchpad, ordered external calls and original callee-saved registers compared.',
                'Preparation-only comparisons stop at 00131080 and do not establish non-AREA loader integration.',
                'Loader MWo3 payloads are synthetic fixtures; selected AREA1 actor checks execute separately authenticated raw instructions.',
                'AREA1 engine operations are explicit mocks; six-register18a330 arguments compared exactly; resident18c220 executes original instructions.',
                'No retail runtime activation equivalence follows from the differential alone.']))
    with a.output.open('x') as f:
        json.dump(report,f,indent=2);f.write('\n')
    print('PASS',report['total_cases'],'comparisons;',len(bodies&coverage),'/',len(bodies),'ELF instruction addresses;',area_coverage,'raw AREA coverage')


if __name__ == '__main__':
    main()
