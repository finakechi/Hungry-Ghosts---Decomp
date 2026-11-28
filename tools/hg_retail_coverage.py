"""Coverage by original build, executable image and guest address."""

from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re
import struct

from hg_audio.m24_reference_map import scan as scan_audio
from hg_identify import ISO, elf32, va_to_offset

HASH_PACKETS = {'hg-m19-loader-bodies/v1', 'hg-m20-original-bodies/v1', 'hg-m21-scene-ranges/v1'}
# These labels bind specific original checkpoints to functions. Test success and
# names of exported host symbols do not confer this flag.
RUNTIME = {
    ('EE', 0x158A10): 'evidence/m8/connected-initializer-runtime-comparison.json',
    ('EE', 0x12C790): 'evidence/m17/lifecycle-sequence.json',
    ('/AREA0/A0_B09.BIN', 0xA87880): 'evidence/m18/results.json',
    ('/AREA0/A0_B09.BIN', 0xA88660): 'evidence/m21/title-neutral.json',
    ('/AREA0/A0_B09.BIN', 0xA886E0): 'evidence/m21/title-neutral.json',
    ('/AREA0/A0_B09.BIN', 0xA88B40): 'evidence/m21/title-neutral.json',
    ('/AREA1/A1_B00.BIN', 0xA8A070): 'evidence/m2/verification.json',
}
M21_CODE = {0x141470, 0x25C170, 0x25DDD0, 0xA88660, 0xA886E0, 0xA88A20, 0xA88B40}
INDEPENDENT = {
    'src/audio/audio_iop.c': 'Host IOP/SPU2 service model. M22 checks selected boundaries.',
    'src/audio/audio_sequencer.c': 'HSQ1 sequencer and allocator.',
    'src/scene/world_init.c': 'M23 world harness includes independent collision and scene behavior.',
}


def read_json(path):
    return json.loads(path.read_text())


def check_hash(data, expected, label):
    if sha256(data).hexdigest() != expected:
        raise ValueError(f'{label}: SHA-256 mismatch')
    return data


def address(text):
    value = int(text, 16)
    if value < 0 or value % 4:
        raise ValueError(f'misaligned instruction address {text}')
    return value


def spans(ranges):
    for row in ranges:
        low, high = address(row['start']), address(row['end_exclusive'])
        if high <= low:
            raise ValueError(f'invalid instruction range {low:#x}..{high:#x}')
        yield low, high


def mapped_sources(root, c_entries, c_body):
    sources = defaultdict(dict)
    legacy_names = {symbol: entry for entry, row in c_entries.items() for symbol in row['names']}
    for path in sorted((root / 'src').glob('*.c')):
        text = path.read_text()
        for symbol, entry in legacy_names.items():
            if c_body(text, symbol):
                sources[entry][symbol] = path.relative_to(root).as_posix()
    for folder in ('audio', 'hgscript', 'scene'):
        for path in sorted((root / 'src' / folder).glob('*.c')):
            relative = path.relative_to(root).as_posix()
            if relative in INDEPENDENT:
                continue
            text = path.read_text()
            if folder == 'audio':
                pairs = re.findall(r'^/\* 0x([0-9a-f]{8}) (hg_\w+)\b', text, re.M)
            else:
                # Adjacent address comments in M20/M21. An authenticated
                # packet must also establish the candidate entry below.
                pairs = re.findall(r'^/\* ([0-9a-f]{8})[.:][^*]*(?:\*(?!/)[^*]*)*\*/'
                                   r'\s*(?:static\s+)?\w+\s+(\w+)\s*\(', text, re.M)
            for entry, symbol in pairs:
                if c_body(text, symbol):
                    sources[address(entry)][symbol] = relative
    return sources


class Image:
    def __init__(self, name, digest, read):
        self.name = name
        self.digest = digest
        self.read = read
        self.entries = defaultdict(set)
        self.candidates = set()
        self.candidate_words = set()
        self.verified_words = set()
        self.source_paths = defaultdict(set)

    def candidate(self, entry, ranges):
        ranges = list(spans(ranges))
        for low, high in ranges:
            self.read(low, high - low)
        self.candidates.add(entry)
        self.entries[entry]
        for low, high in ranges:
            self.candidate_words.update(range(low, high, 4))

    def check(self, low, expected, label):
        if len(expected) != 4 or self.read(low, 4) != expected:
            raise ValueError(f'{label}: original word mismatch at {low:08x}')
        self.verified_words.add(low)

    def packet(self, packet, label):
        for row in packet['functions']:
            entry = address(row['entry'])
            flags = self.entries[entry]
            flags.add('reviewed')
            if row.get('source', '').startswith('src/'):
                self.source_paths[entry].add(row['source'])
            words = row.get('instructions', [])
            for word in words:
                self.check(address(word['address']), bytes.fromhex(word['bytes']), label)
            ranges = row.get('body_ranges', [])
            if packet['schema'] in HASH_PACKETS:
                ranges = row.get('ranges')
                if ranges is None:
                    ranges = [dict(start=row['entry'], end_exclusive=row['end_exclusive'], sha256=row['sha256'])]
                for span, (low, high) in zip(ranges, spans(ranges)):
                    check_hash(self.read(low, high - low), span['sha256'], label)
                    self.verified_words.update(range(low, high, 4))
                words = ranges
            if words:
                flags.add('original-byte-verified')
            self.candidate(entry, ranges)

    def report(self, sources, excluded):
        classes, flags, entries = Counter(), Counter(), {}
        for entry, row in self.entries.items():
            symbols = sources.get(entry, ())
            if symbols and entry in self.candidates:
                classification = 'source-mapped'
                row.add('partial-recovery')
            elif 'reviewed' in row:
                classification = 'analysis'
            else:
                classification = 'unknown'
            classes[classification] += 1
            if classification == 'unknown':
                row.add('unknown')
            flags.update(row)
            if row != {'unknown'} or symbols:
                entries[f'{entry:08x}'] = dict(classification=classification, flags=sorted(row),
                                              source=sorted(symbols))
        return {
            'image': self.name, 'sha256': self.digest,
            'denominator': {'candidate_entries': len(self.candidates),
                            'candidate_body_bytes': 4 * len(self.candidate_words)},
            'numerator': {
                'source_mapped_entries': classes['source-mapped'],
                'original_bytes_checked_in_candidates': 4 * len(self.verified_words & self.candidate_words),
                'original_bytes_checked_outside_candidates': 4 * len(self.verified_words - self.candidate_words),
                'recovered_original_instruction_bytes': None,
            },
            'classifications': dict(sorted(classes.items())),
            'evidence_flags': dict(sorted(flags.items())),
            'entries': dict(sorted(entries.items())),
            'exclusions': excluded,
        }


def executable_file_ranges(blob):
    table = struct.unpack_from('<I', blob, 32)[0]
    width, count = struct.unpack_from('<2H', blob, 46)
    for index in range(count):
        _, kind, flags, _, offset, size = struct.unpack_from('<6I', blob, table + width * index)
        if kind == 1 and flags & 4:
            if offset + size > len(blob) or offset % 4 or size % 4:
                raise ValueError('invalid IOP executable section')
            yield offset, offset + size


def report(root, elf_path, iso_path, c_entries, callees, c_body):
    build = read_json(root / 'evidence/builds/japan.json')
    elf_id = build['elf']['sha256']
    elf = check_hash(elf_path.read_bytes(), elf_id, 'SCPS-15046 ELF')
    mapping = elf32(elf)

    def resident_read(low, size):
        offset = va_to_offset(mapping, low, size)
        return elf[offset:offset + size]

    resident = Image('EE', elf_id, resident_read)
    automatic = read_json(root / 'analysis/function-inventory.json')
    if automatic['elf_sha256'] != elf_id:
        raise ValueError('foreign Ghidra inventory')
    excluded_automatic = []
    for row in automatic['functions']:
        try:
            entry = address(row['entry'])
            resident_read(entry, 4)
            resident.candidate(entry, row['body_ranges'])
        except ValueError:
            # The historical Ghidra inventory includes overlay/unbacked
            # candidates and sub-word artifacts. Neither describes resident code.
            excluded_automatic.append(row['entry'])
            continue
        if row['status'] != 'auto-discovered-unreviewed':
            resident.entries[entry].add('reviewed')
    for entry in callees & resident.candidates:
        resident.entries[entry].add('external-dependency')

    overlays, iop = {}, []
    m22 = read_json(root / 'evidence/m22/retail-check.json')
    packets = []
    for path in sorted((root / 'analysis').glob('*/*.json')):
        packet = read_json(path)
        schema = packet.get('schema')
        program = packet.get('program', {})
        if schema == 'ps2-evidence/v1' and program.get('sha256') == elf_id:
            resident.packet(packet, path.name)
        elif schema in HASH_PACKETS:
            if packet['elf_sha256'] != elf_id:
                raise ValueError('foreign range-hash packet')
            resident.packet(packet, path.name)
        elif schema == 'hg-m18-overlay-evidence/v1':
            info = packet['overlay']
            packets.append((packet, info['path'], info['sha256'], address(info['link_address'])))
        elif schema == 'ps2-evidence/v1' and 'raw_resource' in program:
            raw = program['raw_resource']
            if raw['source_elf_sha256'] != elf_id:
                raise ValueError('foreign overlay packet')
            packets.append((packet, raw['iso_path'], program['sha256'], address(program['load_address'])))

    title = read_json(root / 'evidence/m21/title-neutral.json')
    if title['elf_sha256'] != elf_id or title['iso_sha256'] != build['image']['sha256']:
        raise ValueError('foreign M21 scene ranges')
    title_overlay = title['publication']['overlay_sha256']

    def add_scene_ranges(image, file):
        functions = []
        for row in title['ranges']:
            if row['file'] == file and address(row['start']) in M21_CODE:
                start = row['start']
                functions.append(dict(entry=start, source='src/scene/title_scene.c',
                                      ranges=[dict(start=start, end_exclusive=row['end'], sha256=row['sha256'])]))
        image.packet(dict(schema='hg-m21-scene-ranges/v1', functions=functions), 'M21 code ranges')

    add_scene_ranges(resident, '/SCPS_150.46')
    for item in m22['instruction_anchors']['SCPS_150.46']:
        resident.check(address(item['address']), bytes.fromhex(item['little_endian_bytes']), 'M22 EE')

    audio = scan_audio(elf_path)
    if audio != read_json(root / 'evidence/m24/reference-map.json'):
        raise ValueError('M24 audio dependency map differs from authenticated ELF')
    for label, sites in audio['calls_in'].items():
        routine = 'channel_a_note' if label == 'channel_a_note_allocation' else label
        entry = address(audio['functions'][routine]['address'])
        for site, _ in sites:
            resident.verified_words.add(address(site))
        if entry in resident.candidates:
            resident.entries[entry].update(('reviewed', 'original-byte-verified'))

    collision_path = root / 'evidence/realignment/collision.json'
    if collision_path.exists():
        collision = read_json(collision_path)
        if (collision['schema'] != 'hg-collision-snap/v1' or collision['elf_sha256'] != elf_id
                or collision['same_run']['passed'] is not True):
            raise ValueError('unverified collision receipt')
        flags = resident.entries[0x1E3440]
        flags.update(('reviewed', 'runtime-tested', 'interpreted-original-instructions', 'external-dependency'))
        # The replay itself checks each word against the ELF and captured RAM.
        execution = collision['same_run']
        executed = set()
        fingerprint = sha256()
        for low, high in spans(execution['executed_ranges']):
            fingerprint.update(resident_read(low, high - low))
            executed.update(range(low, high, 4))
        if (len(executed) != execution['distinct_instruction_addresses']
                or fingerprint.hexdigest() != execution['executed_words_sha256']):
            raise ValueError('collision execution ranges do not match authenticated ELF')
        resident.verified_words.update(executed)
        flags.add('original-byte-verified')
    else:
        executed = set()

    if iso_path:
        with iso_path.open('rb') as stream:
            digest = sha256()
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
            if digest.hexdigest() != build['image']['sha256']:
                raise ValueError('SCPS-15046 ISO: SHA-256 mismatch')
            stream.seek(0)
            disc = ISO(stream)
            for packet, name, digest, base in packets:
                data = check_hash(disc.read(name), digest, name)

                def overlay_read(low, size, data=data, base=base):
                    if not base <= low < low + size <= base + len(data):
                        raise ValueError(f'outside overlay at {low:08x}')
                    return data[low - base:low - base + size]

                key = name, digest, base
                if key not in overlays:
                    overlays[key] = Image(name, digest, overlay_read)
                overlays[key].packet(packet, name)
                if digest == title_overlay and name == '/AREA0/A0_B09.BIN':
                    add_scene_ranges(overlays[key], name)
            for name, items in sorted(m22['instruction_anchors'].items()):
                if not name.endswith('.IRX'):
                    continue
                digest = m22['inputs'][name]
                data = check_hash(disc.read('/IRX/' + name), digest, name)
                module = Image(name, digest, lambda low, size: data[low:low + size])
                executable = list(executable_file_ranges(data))
                instruction_pcs = set()
                # M22 addresses are module-relative. Bind each recorded offset
                # to its module load segment before accepting the word.
                module_map = m22['module_mapping'][name]
                for item in items:
                    pc = address(item['address'])
                    offset = int(item['file_offset'], 16)
                    if (offset != int(module_map['file_load_offset'], 16) + pc
                            or pc + 4 > int(module_map['file_load_size'], 16)):
                        raise ValueError(f'{name}: invalid M22 file mapping')
                    module.check(offset, bytes.fromhex(item['little_endian_bytes']), name)
                    if any(low <= offset and offset + 4 <= high for low, high in executable):
                        instruction_pcs.add(pc)
                iop.append(dict(image=name, sha256=digest, original_instruction_bytes=4 * len(instruction_pcs),
                                checked_noncode_anchor_bytes=4 * (len(module.verified_words) - len(instruction_pcs)),
                                module_executable_section_bytes=sum(hi - lo for lo, hi in executable),
                                address_context='verified module-relative addresses',
                                evidence='M22 selected original anchors'))

    for image in (resident, *overlays.values()):
        for (name, entry), receipt in RUNTIME.items():
            if name == image.name and entry in image.candidates and (root / receipt).exists():
                image.entries[entry].add('runtime-tested')

    sources = mapped_sources(root, c_entries, c_body)

    def overlay_sources(image):
        return {entry: {symbol for symbol, path in sources.get(entry, {}).items() if path in paths}
                for entry, paths in image.source_paths.items()}

    out = resident.report(sources, ['Unreviewed words outside candidates, BSS, overlays, IOP and VU microcode.',
                                    'Coverage of recovered semantics has no measured instruction-byte numerator.'])
    out['interpreted_original_bytes'] = 4 * len(executed)
    out['excluded_automatic_entries'] = excluded_automatic
    return {
        'schema': 'hg-coverage-inventory/v2', 'build_elf_sha256': elf_id,
        'EE': out,
        'overlays': {
            'status': 'checked' if iso_path else 'unrun (provide --retail-iso)',
            'images': [
                dict(load_address=f'{base:08x}', **image.report(
                    overlay_sources(image),
                    ['Unmapped functions, overlay data and other overlays.']))
                for (_, _, base), image in sorted(overlays.items())
            ],
            'excluded': 'No whole-overlay denominator. Only authenticated AREA0 and AREA1 packets are supported.',
        },
        'IOP': {'status': 'checked' if iso_path else 'unrun (provide --retail-iso)',
                'images': iop, 'denominator': None, 'recovered_original_instruction_bytes': None,
                'excluded': 'No IOP census. Module load images contain data and relocations; modeled PCM and host code add zero.'},
        'VU': {'original_instruction_bytes': None, 'denominator': None,
               'excluded': 'EE COP2 macro instructions are EE words. No authenticated VU microprogram census.'},
        'independent_models': INDEPENDENT,
    }
