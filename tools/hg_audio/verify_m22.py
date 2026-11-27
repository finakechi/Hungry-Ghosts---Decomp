#!/usr/bin/env python3
"""Compare the selected SG2 RPC lifecycle to authenticated EE/IOP instructions."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys
from zipfile import ZipFile
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ee_subset import Machine
from hg_identify import ISO
from hg_audio.audio_runner import authenticate, fetch_member, KNOWN
from hg_audio.audio_guest import COMMAND_BASE, ID_TABLE
from hg_audio.connected import ConnectedAudio, VoiceParams

IDENTITY = {
    'SCPS_150.46': 'a74248c5e2fd2cce9cede6c04d8117ce79e9472d380bbd80eb410370abcf3c61',
    'SG2IOP.IRX': 'a8ac82dc1f12b7960187d344d2bd5167383172c77f3ed15482b1183d30a9c1c1',
    'LIBSD.IRX': '57cbd80b7c2f966985e14ca18c03e849e3b3eb1216f06a6f4c1e6ca85084ea37',
    'SDRDRV.IRX': '37b615869fed71ec90760543d83170dc7b7608142a4117d665a0cfbe78c3cc52',
    'IOPRP271.IMG': '8d752d01d3dda7d418ae174ffe645787f4bf3fab4e035ca1c3300a0522ee22f1',
}
SAVED_SHA = '1d8798faaeaf042caddf5f933c083dc7654f420ea26223db8bec41214926808d'
FRESH_SHA = {
    'title-input': '4c32e376633fe1e9fa922bb1760d441af938df567414c7c7321b10f29a02a1bb',
    'cursor-down': '815393198648ded45e6b704eba9d8087a44ce5d2d8b958e20e13324c9ed8dda2',
    'cursor-up': 'a05d36829abacd4c5f139e95a8b7c1362222a8377e6d120e40c9af9a801ddd14',
    'cursor-confirm': 'a549596bd96641dde84e6132293205ee8520abc3a21b6579638d7652729ba9e9',
}
STOP, STACK, WIRE = 0xfff000, 0x210000, 0x220000
SG_BASE = 0x56230
SPU_RAM_OFFSET = 0x10004
VOICE_COMMANDS = {1, 2, 3, 4, 7, 8, 9, 10}
CURSOR, PACKETS = 0xa80180, 0xa801c0
MASK32 = 0xffffffff


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def args(machine, count):
    return tuple(x & MASK32 for x in machine.reg[4:4 + count])


def invoke(machine, pc, *arguments):
    for index, argument in enumerate(arguments, 4):
        machine.setreg(index, argument)
    machine.reg[29] = STACK
    machine.run(pc, STOP, limit=300000)
    return machine.reg[2] & MASK32


def segments(blob):
    phoff = struct.unpack_from('<I', blob, 28)[0]
    phnum = struct.unpack_from('<H', blob, 44)[0]
    for index in range(phnum):
        kind, offset, va, _, size, memsz, _, _ = struct.unpack_from(
            '<8I', blob, phoff + index * 32)
        if kind == 1:
            yield offset, va, size, memsz


def machine_for(blob, machine=None):
    machine = machine or Machine()
    for offset, va, size, memsz in segments(blob):
        machine.put(va, blob[offset:offset + size] + bytes(memsz - size))
    machine.put(STACK - 0x1000, bytes(0x2000))
    return machine


class LibsdMachine(Machine):
    def __init__(self, log):
        super().__init__()
        self.log = log
        self.reads = []

    def load(self, address, size):
        if 0xbf900000 <= address < 0xbf900800 and size == 2:
            self.reads.append(address)
            return (address >> 1) & 0x7fff
        return super().load(address, size)

    def store(self, address, value, size):
        if 0xbf900000 <= address < 0xbf900800 and size == 2:
            self.log.append(('write', address, value & 0xffff))
        else:
            super().store(address, value, size)


class Sleep(Exception):
    pass


class OriginalIop:
    """Run SG2 queue/dispatch/work instructions at link base zero.

    Run the original linked LIBSD implementation of parameter, switch and
    address accesses. External fixtures provide register reads, SIF and
    successful single-transfer DMA completion.
    """
    def __init__(self, sg2, libsd):
        self.log = []
        self.sd = machine_for(libsd, LibsdMachine(self.log))
        self.sg = machine_for(sg2)
        self.sg.hooks[0x459c] = lambda _: 0  # WakeupThread
        self.sg.hooks[0x449c] = self.transfer
        self.sg.hooks[0x44ac] = lambda _: 1  # VoiceTransStatus, DMA fixture
        for stub, export in [(0x446c, 0x3660), (0x4474, 0x36bc),
                             (0x447c, 0x371c), (0x4484, 0x378c)]:
            self.sg.hooks[stub] = self.link(export)

    def link(self, export):
        return lambda machine: invoke(self.sd, export, *args(machine, 2))

    def transfer(self, machine):
        self.log.append(('dma', *args(machine, 4), machine.load(machine.reg[29] + 16, 4)))
        return self.log[-1][-1]

    def rpc(self, wire):
        self.sg.put(WIRE, wire)
        addr = invoke(self.sg, 0x3de0, 0, WIRE, len(wire))
        assert addr == 0x6fb8
        return struct.unpack('<48I', self.sg.get(addr, 48 * 4)), self.sg.load(addr + 0x184, 4)

    def complete_dma(self):
        def sleep(machine):
            if machine.steps > 30:
                raise Sleep
            return 0
        self.sg.hooks[0x4594] = sleep
        try:
            invoke(self.sg, 0x474)
        except Sleep:
            return
        raise AssertionError('DMA worker did not return to sleep')


def original_ee(blob):
    machine = Machine()
    for start, end in ((0x2a7560, 0x2a75a4), (0x2acf60, 0x2ad0c0),
                       (0x2b1188, 0x2b11cc), (0x2b1668, 0x2b16b8),
                       (0x3e0720, 0x3e0734)):
        offset = start - 0x100000 + 0x800
        machine.put(start, blob[offset:offset + end - start])
    machine.put(STACK - 0x1000, bytes(0x2000))
    machine.hooks[0x10cca0] = lambda _: 0
    machine.hooks[0x10cce8] = lambda _: 0
    machine.put(CURSOR, bytes(4))
    machine.put(PACKETS, bytes(0x2200))
    machine.put(0xa823a8, bytes(8))
    machine.put(0xa823c0, bytes(0x300))
    machine.put(0xa83290, bytes(0x60))
    return machine


def collect_ee(machine):
    wire = machine.get(PACKETS, 16 * machine.load(CURSOR, 4))
    machine.store(CURSOR, 0, 4)
    return list(struct.iter_unpack('<4I', wire))


def voice_packets(machine, voice, start, left, right, note, scale):
    # Original SSA/ADSR producers; volume and pitch use 002b2388's
    # precomputed inputs at the verified packet-post boundary.
    invoke(machine, 0x2b1668, voice, 0x8080, 0x808a)
    invoke(machine, 0x2b1188, voice, start)
    invoke(machine, 0x2acf60, 1, voice, left, right)
    invoke(machine, 0x2acf60, 4, voice, (60 << 24) | (note << 16) | 0x40,
           0x01000000 | scale)
    return collect_ee(machine)


def runtime_mapping(blob, ram, base):
    sections = struct.unpack_from('<I', blob, 32)[0]
    count = struct.unpack_from('<H', blob, 48)[0]
    loads = list(segments(blob))
    assert len(loads) == 1
    offset, va, size, memsz = loads[0]
    assert va == 0
    relocations = set()
    for index in range(count):
        _, kind, _, _, start, length, *_ = struct.unpack_from('<10I', blob, sections + index * 40)
        if kind == 9:
            relocations.update(x[0] for x in struct.iter_unpack('<II', blob[start:start + length]))
    # Import linkage rewrites some jump words that are outside relocations.
    # Compare text/data words where relocations and import slots leave them
    # static, and report other words explicitly.
    equal = [i for i in range(0, size, 4) if i not in relocations and
             blob[offset + i:offset + i + 4] == ram[base + i:base + i + 4]]
    assert len(equal) > 500
    return {'runtime_base': hex(base), 'file_load_offset': hex(offset),
            'file_load_size': hex(size), 'memory_size': hex(memsz),
            'nonrelocated_matching_words': len(equal),
            'relocation_sites': len(relocations),
            'remaining_words': size // 4 - len(equal) - len(relocations)}


def native_checkpoint(path, expected):
    assert digest(path.read_bytes()) == expected
    with ZipFile(path) as saved:
        ee_ram = saved.read('eeMemory.bin')
        iop_ram = saved.read('iopMemory.bin')
        spu = saved.read('SPU2.bin')
    head, tail = struct.unpack_from('<2I', iop_ram, SG_BASE + 0x6ba0)
    assert head == tail
    ring = [struct.unpack_from('<4I', iop_ram, SG_BASE + 0x4ba0 + (i % 512) * 16)
            for i in range(tail - 512, tail)]
    iop_env = struct.unpack_from('<48I', iop_ram, SG_BASE + 0x6fb8)
    assert iop_env == struct.unpack_from('<48I', ee_ram, 0xa821c0)
    metadata = {'state_sha256': expected, 'iop_sha256': digest(iop_ram),
                'spu2_sha256': digest(spu), 'ee_sha256': digest(ee_ram),
                'command_head_tail': [head, tail],
                'nonzero_envx': [[voice, value] for voice, value in enumerate(iop_env) if value]}
    return ring, spu, ee_ram, metadata


def stage_native_cursor(harness, slot, records, boot_base):
    # An original EE play request points at the title cursor event sequence.
    # Use resolved fields from the fresh IOP input to check the caller seam.
    request = harness.play(slot, 1, 3, 0, 1)
    assert request < 32
    start = harness.mem.r32(COMMAND_BASE + request * 0x50 + 0x40)
    for at in range(0, len(records), 6):
        adsr, address, gain, pitch, wet, key = records[at:at + 6]
        assert [r[0] for r in records[at:at + 6]] == [2, 3, 1, 4, 9, 7]
        voice = address[1]
        assert adsr[1] == gain[1] == pitch[1] == voice
        notes, tuning = pitch[2:4]
        fine = (notes >> 8 & 0xff) - (256 if notes & 0x8000 else 0)
        params = VoiceParams(notes >> 16 & 0xff, notes >> 24, tuning & 0xffff,
                             gain[2], gain[3], adsr[2], adsr[3], fine,
                             notes & 0xff, tuning >> 24)
        assert harness.stage_play_voice(request, voice, address[2] - boot_base, params)
        assert harness.post(wet) and harness.stage_key(voice, True)
    return start


def instruction_anchors(inputs):
    ranges = {
        'SCPS_150.46': [0x10af78, 0x10b074, 0x1572bc, 0x157368, 0x2acf90, 0x2ad204,
                        0x2ad2e0, 0x2ad2e8, 0x2b50b0, 0x2b50c0, 0x2b2c90, 0x2b2d78],
        'SG2IOP.IRX': [0x1690, 0x16f0, 0x1704, 0x3cd4, 0x3d9c, 0x3da0, 0x3eb0,
                       0x3f80, 0x4034, 0x4180, 0x534, 0x55c, 0x4b1c],
        'LIBSD.IRX': [0x36b8, 0x373c, 0x3750, 0x37cc, 0x37e4],
        'SDRDRV.IRX': [0x3a8, 0x3ac, 0x3b8],
    }
    result = {}
    for name, addresses in ranges.items():
        blob = inputs[name]
        offset, base, size, _ = next(segments(blob))
        rows = []
        for address in addresses:
            assert base <= address <= base + size - 4
            file_offset = offset + address - base
            rows.append({'address': hex(address), 'file_offset': hex(file_offset),
                         'little_endian_bytes': blob[file_offset:file_offset + 4].hex()})
        result[name] = rows
    return result


def verify(args_):
    extracted = args_.retail
    inputs = {name: (extracted / 'IRX' / name if name != 'SCPS_150.46'
                     else extracted / name).read_bytes() for name in IDENTITY}
    assert {name: digest(data) for name, data in inputs.items()} == IDENTITY
    with args_.iso.open('rb') as stream:
        authenticate(stream)
        disc = ISO(stream)
        banks = {key: fetch_member(stream, disc, spec['dir'], spec['flags'])
                 for key, spec in KNOWN.items()}
        boot_bank = fetch_member(stream, disc, 0x6d, 0x90)
        # Check extracted bytes independently against the selected disc.
        for name, file in inputs.items():
            path = '/' + name if name == 'SCPS_150.46' else '/IRX/' + name
            assert disc.read(path) == file

    assert digest(args_.state.read_bytes()) == SAVED_SHA
    with ZipFile(args_.state) as saved:
        ee_ram = saved.read('eeMemory.bin')
        iop_ram = saved.read('iopMemory.bin')
        spu_state = saved.read('SPU2.bin')

    elf, sg2, libsd = (inputs[name] for name in ('SCPS_150.46', 'SG2IOP.IRX', 'LIBSD.IRX'))
    bases = {'SG2IOP.IRX': 0x56230, 'LIBSD.IRX': 0x50830, 'SDRDRV.IRX': 0x90630}
    mapping = {name: runtime_mapping(inputs[name], iop_ram, base) for name, base in bases.items()}
    sg_base, sd_base = bases['SG2IOP.IRX'], bases['LIBSD.IRX']
    for sg_stub, libsd_export in [(0x446c, 0x3660), (0x4474, 0x36bc), (0x447c, 0x371c),
                                  (0x4484, 0x378c), (0x449c, 0x1890), (0x44ac, 0x1ae0)]:
        word = struct.unpack_from('<I', iop_ram, sg_base + sg_stub)[0]
        assert word == 0x08000000 | ((sd_base + libsd_export) >> 2)
    assert struct.unpack_from('<I', iop_ram, sg_base + 0x4ad0 + 4 * 0x13)[0] == sg_base + 0x1690
    ee = original_ee(elf)
    destinations = struct.unpack('<5I', ee.get(0x3e0720, 20))
    assert destinations == struct.unpack_from('<5I', ee_ram, 0x3e0720)
    read_index, write_index = struct.unpack_from('<2I', iop_ram, sg_base + 0x6ba0)
    worker_history = list(struct.iter_unpack('<4I', iop_ram[sg_base + 0x6ba8:sg_base + 0x6fa8]))
    assert read_index == write_index
    native = {name: native_checkpoint(args_.native / (name + '.p2s'), identity)
              for name, identity in FRESH_SHA.items()}
    boot_uploads = native['title-input'][0][-2:]
    assert [record[0] for record in boot_uploads] == [0x13, 0x13]

    original = OriginalIop(sg2, libsd)
    bank_rows, cases, slots = [], [], []
    ConnectedAudio.build(args_.library)
    with ConnectedAudio(destinations, args_.library, lambda address: (address >> 1) & 0x7fff) as h:
        def compare_rpc(records, reply):
            wire = b''.join(struct.pack('<4I', *record) for record in records)
            env, busy = original.rpc(wire)
            assert reply and reply.upload_busy == busy and tuple(reply.envelope) == env
            assert h.events == original.log, (
                [(index, actual, expected) for index, (actual, expected)
                 in enumerate(zip(h.events, original.log)) if actual != expected][:8],
                len(h.events), len(original.log))
            return busy

        def register_bank(key, blob, address, expected_endpoint, fresh_spu=None):
            count = struct.unpack_from('<I', blob, 12)[0]
            data_at = struct.unpack_from('<I', blob, 0x18)[0]
            ids = [struct.unpack_from('<I', blob, 0x28 + index * 16)[0]
                   for index in range(count)]
            descriptor = 0
            dest = destinations[ids[0]]
            uploads = []
            h.mem.load(address, blob)
            for _ in range(8 * count + 5):
                endpoint = h.register_step(address)
                if endpoint:
                    break
                reply = h.flush()
                assert reply is not None
                if not reply.upload_busy:
                    continue
                size = struct.unpack_from('<I', blob, 0x20 + descriptor * 16)[0]
                bank_id = ids[descriptor]
                if descriptor and bank_id != ids[descriptor - 1]:
                    dest = destinations[bank_id]
                payload = blob[data_at:data_at + size]
                source = h.ee.upload.arg1
                ticket = invoke(ee, 0x2a7560, source, dest, size)
                transfer = struct.unpack('<5I', ee.get(0xa823c0 + ticket * 20, 20))
                assert transfer[-1] == MASK32
                assert compare_rpc([transfer[:4]], reply) == 1
                assert h.register_step(address) == 0
                assert compare_rpc([], h.flush()) == 1
                original.complete_dma()
                assert h.step_dma() and h.events == original.log
                assert h.register_step(address) == 0
                assert compare_rpc([], h.flush()) == 0
                spu_offset = SPU_RAM_OFFSET + dest
                assert h.spu(dest, size) == payload == spu_state[spu_offset:spu_offset + size]
                assert any(row[0] == 0 and row[2:] == (dest, size) for row in worker_history)
                if fresh_spu is not None:
                    assert boot_uploads[descriptor][1:] == (0xc2400, dest, size)
                    assert payload == fresh_spu[spu_offset:spu_offset + size]
                uploads.append({'bank_id': bank_id, 'member_payload_offset': data_at,
                                'spu_destination': dest, 'size': size,
                                'payload_sha256': digest(payload)})
                descriptor += 1
                dest += size
                data_at += size
            else:
                raise AssertionError(f'{key} registration timeout')
            assert endpoint == expected_endpoint and descriptor == count
            first_slot = h.mem.r32(ID_TABLE + ids[0] * 0x78)
            assert h.sample_address(first_slot, 16) == destinations[ids[0]] + 16
            bank_rows.append({'member': key, 'sha256': digest(blob), 'uploads': uploads,
                              'endpoint': endpoint})
            return first_slot

        for key, blob in banks.items():
            spec = KNOWN[key]
            slots.append(register_bank(key, blob, spec['load'], spec['endpoint']))
        boot_slot = register_bank('6d90', boot_bank, 0xb55000, 0xb5ab40, native['title-input'][1])
        boot_base = destinations[0]
        assert h.sample_address(boot_slot, 0xf340) == 0x14350
        assert h.model.frees == 4 and not h.model.sif_heap

        def compare_staged(name, records, before):
            cursor = h.mem.r32(CURSOR)
            assert cursor == len(records)
            actual = h.mem.read_blob(PACKETS, cursor * 16)
            assert list(struct.iter_unpack('<4I', actual)) == records
            compare_rpc(records, h.flush())
            cases.append({'case': name, 'commands': [x[0] for x in records],
                          'register_writes': len(h.events) - before})

        def stage_original_voice(voice, bank, note, left, right):
            assert h.stage_voice(voice, slots[bank], 16, note, 60, 0x116b, left, right,
                                 0x8080, 0x808a)
            return voice_packets(ee, voice, bank_rows[bank]['uploads'][0]['spu_destination'] + 16,
                                 left, right, note, 0x116b)

        before = len(h.events)
        records = stage_original_voice(0, 0, 60, 0x2800, 0x1000)
        assert h.post((9, 0, 1, 0)) and h.post((0x0a, 0, 0, 0))
        assert h.stage_key(0, True)
        compare_staged('start-core-0', records + [(9, 0, 1, 0), (0x0a, 0, 0, 0), (7, 0, 1, 0)],
                       before)

        before = len(h.events)
        records = stage_original_voice(47, 1, 55, MASK32 - 31, 0x2000)
        assert h.post((9, 0, 1, 1 << 23)) and h.stage_key(47, True)
        compare_staged('overlap-core-1', records + [(9, 0, 1, 1 << 23), (7, 0, 0, 1 << 23)],
                       before)

        for voice, name, record in [(0, 'stop-core-0', (8, 0, 1, 0)),
                                    (47, 'stop-core-1', (8, 0, 0, 1 << 23))]:
            before = len(h.events)
            assert h.stage_key(voice, False)
            compare_staged(name, [record], before)

        before = len(h.events)
        assert h.post((4, 47, 0x3c400040, 0x01001000)) and h.stage_key(47, True)
        compare_staged('pitch-and-retrigger', [(4, 47, 0x3c400040, 0x01001000),
                                              (7, 0, 0, 1 << 23)], before)
        assert h.stage_key(47, False)
        compare_rpc([(8, 0, 0, 1 << 23)], h.flush())
        assert original.sd.reads, 'original must read hardware ENVX'
        controls = {}
        for name, records in (
                ('wrong-key-id', [(8, 0, 1, 0)]),
                ('wrong-core', [(7, 0, 0, 1)]),
                ('reversed-edges', [(8, 0, 1, 0), (7, 0, 1, 0)])):
            reference = OriginalIop(sg2, libsd)
            reference.rpc(b''.join(struct.pack('<4I', *r) for r in
                                   ([(7, 0, 1, 0), (8, 0, 1, 0)]
                                    if name == 'reversed-edges' else [(7, 0, 1, 0)])))
            with ConnectedAudio(destinations, args_.library) as control:
                for packet in records:
                    assert control.post(packet)
                assert control.flush()
                controls[name] = control.events != reference.log
        assert all(controls.values())

        # Fresh runtime supplies packet inputs and an independent EE/IOP
        # reply observation. Register effects still come from original code.
        native_rows = {}
        pcm = []
        for name, start_at in [('cursor-down', 500), ('cursor-up', 494), ('cursor-confirm', 429)]:
            ring, native_spu, native_ee, metadata = native[name]
            records = [record for record in ring[start_at:] if record[0] in VOICE_COMMANDS]
            for cmd, _, addr, _ in records:
                if cmd == 3:
                    assert boot_base <= addr < boot_base + 342832
                    assert h.spu(addr, 32) == native_spu[
                        SPU_RAM_OFFSET + addr:SPU_RAM_OFFSET + addr + 32]
            before = len(h.events)
            if name in ('cursor-down', 'cursor-up'):
                initial_sequence = stage_native_cursor(h, boot_slot, records, boot_base)
                # Original command records carry the initial sequence root in
                # +0x3c even after +0x40 advances through the sequence.
                retail_roots = {struct.unpack_from('<I', native_ee,
                                COMMAND_BASE + ix * 0x50 + 0x3c)[0] for ix in range(32)}
                assert initial_sequence in retail_roots, (name, hex(initial_sequence), sorted(retail_roots))
                compare_staged(name, records, before)
                metadata['ee_play_sequence'] = hex(initial_sequence)
                pcm.extend(h.render(800))
            else:
                stop_seen = False
                for record in records:
                    assert h.post(record)
                    compare_rpc([record], h.flush())
                    if record[0] in (7, 8):
                        pcm.extend(h.render(800))
                    stop_seen |= record[0] == 8
                assert stop_seen
            metadata.update({'selected_records': len(records), 'excluded_records': 512 - start_at - len(records),
                             'register_writes': len(h.events) - before,
                             'command_wire_sha256': digest(b''.join(struct.pack('<4I', *r) for r in records))})
            native_rows[name] = metadata

        pcm.extend(h.render(12000))
        assert any(left or right for left, right in pcm)
        pcm_bytes = b''.join(struct.pack('<hh', *frame) for frame in pcm)
        pcm_metadata = {'frames': len(pcm), 'rate': 48000, 'sha256': digest(pcm_bytes),
                        'nonzero_frames': sum(bool(left or right) for left, right in pcm)}
        if args_.wav:
            with wave.open(str(args_.wav), 'wb') as output:
                output.setnchannels(2)
                output.setsampwidth(2)
                output.setframerate(48000)
                output.writeframes(pcm_bytes)

    return {'result': 'pass', 'inputs': IDENTITY, 'module_mapping': mapping,
            'instruction_anchors': instruction_anchors(inputs), 'staging_allocations_released': 4,
            'saved_state_sha256': SAVED_SHA,
            'saved_iop_sha256': digest(iop_ram), 'saved_spu2_sha256': digest(spu_state),
            'saved_command_ring_indices': [read_index, write_index],
            'bank_comparisons': bank_rows, 'instruction_sequences': cases,
            'fresh_boot': native['title-input'][3], 'fresh_native_sequences': native_rows,
            'selected_original_register_reads': len(original.sd.reads),
            'instruction_negative_controls': controls, 'modeled_pcm': pcm_metadata}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('iso', 'retail', 'state', 'native', 'library', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--wav', type=Path, help='optional private stereo PCM preview')
    arguments = parser.parse_args()
    result = verify(arguments)
    arguments.output.write_text(json.dumps(result, indent=2) + '\n')
    print(result['result'])


if __name__ == '__main__':
    main()
