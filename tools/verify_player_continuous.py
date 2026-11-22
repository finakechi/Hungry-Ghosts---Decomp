#!/usr/bin/env python3
"""M13 continuous player movement: 20+ frames from one authenticated starting state.

The reconstructed per-frame path (hg_player_frame_continuous) runs on one carried bus:
coordinator prefix 0012f850 (3fa190, edge sync 0031bb80, 3f9ff8, guarded 46e118), the
M10 dispatcher (walk/turn, C 0015ebc0, C 0015fc10/0015e420, history), the M9 same-frame
camera, the connected pre-dispatch services, external coordinator tail, 001379f0 engine
counter advance (46d930/46e110), and CPU-side packet preparation for the next iteration.
Controller input is supplied from the recording. The corridor timing model alternates
the GS field bit once per frame from its starting value. The render handoff is checked
against the following native stop. No packet data is restored from it.

Comparisons are domain-aware: every address our path writes (player and camera
structures, counters, edge counters, history flag, floor/query scratch, reconstructed
scratch regions) must equal the original capture at each native stop; all remaining
differences are enumerated and must classify into the declared external domains (guest
stack, untouched libpad DMA, kernel low RAM, input system bytes, other-actor/render
engine state). A difference in a reconstructed domain is a failure.
"""
import argparse
import ctypes as C
import gc
import json
import re
import struct
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'tools')]
import verify_player_connected_runtime as V
import verify_player_grounded_runtime as G
from verify_player_grounded_runtime import Captures, observation as m12_observation, STATUS_VALUES
from verify_player_turn_runtime import f
from verify_resource_loader import EXPECTED, shared_module
from resource_harness import ResourceEnv, library
from hg_evidence_safety import digest
import predispatch_differential as PD
import render_node_differential as RN

P, CAM = 0x3f92d0, 0x3f9860
EXTERNAL_SAMPLE = 8
# M15 render nodes reached by 00143150 (four 0x110-byte records at 00528430).
NODE_RANGE = (0x528430, 0x528870)
COORDINATOR_STAGES = ('coordinator-entry', 'coordinator-counter')


def tail_oracle(caps, fr):
    """Original 0012bc70 then 00143150 from fr's native camera-return capture (cached).

    The coordinator calls 0012bc70 at 0012f8f0 and 00143150 at 0012f900, after
    0013b6e0, 001430a0 and 00143100 and around 0018c520. Running exactly these
    two reconstructed services from the camera-return capture is an oracle for
    the bytes they produce: comparing its result with the next native
    coordinator-entry separates those bytes from the inter-frame writes of
    every other, unreconstructed service."""
    if not hasattr(caps, 'tail_oracles'):
        caps.tail_oracles = {}
    key = fr['counter']
    if key not in caps.tail_oracles:
        native = caps.load(fr['captures']['camera-return']['name'])
        row = RN.run_original(native, (RN.MODEL, RN.TAIL), caps.elfdata, caps.segments)
        if row['identity_mismatches']:
            raise ValueError('Model-draw/render-node oracle executed non-ELF instructions')
        caps.tail_oracles[key] = dict(scratch=bytes(row['memory'].scratch),
                                      writes=row['writes'], steps=row['steps'],
                                      rng_calls=row['rng_calls'],
                                      rng_after=RN.rng_state(row['memory']),
                                      rng_before=RN.rng_state(native),
                                      node=bytes(row['memory'].raw[NODE_RANGE[0]:NODE_RANGE[1]]))
    return caps.tail_oracles[key]


def inter_frame_ownership(caps, obs):
    """Data-driven inter-frame ownership from the captures and the oracle.

    external_scratch: scratch offsets whose next coordinator-entry value is
    not the value original 00143150 leaves (other tail services 0013b6e0,
    0012bc70, 0013ce80 ... and the main loop wrote them). These, and only
    these, are excluded at the coordinator stages, and afterwards until
    reconstructed code (the M14 window or later frame code) rewrites them.

    rng_external: per frame, 0011ad58 draws made after 00143150 by
    unreconstructed code (the LCG distance from camera-return to the next
    coordinator-entry minus the oracle's own draws). The native draws of
    0019c8a0 at 10172 come from the camera-return state with zero prior
    steps, so the external consumer runs after 00143150. The host replays
    these draws through the original 0011ad58 at the 0013ce80 seam.

    node_unexplained must be zero: every inter-frame change of the four node
    records is produced by 00143150, so the records are guarded at every stage."""
    frames = sorted(obs['frames'], key=lambda fr: fr['counter'])
    external, rng_external, unexplained = set(), {}, 0
    for fr, nxt in zip(frames, frames[1:]):
        oracle = tail_oracle(caps, fr)
        entry = caps.load(nxt['captures']['coordinator-entry']['name'])
        external.update(o for o in range(0x4000) if entry.scratch[o] != oracle['scratch'][o])
        node = entry.raw[NODE_RANGE[0]:NODE_RANGE[1]]
        unexplained += sum(x != y for x, y in zip(node, oracle['node']))
        steps = RN.lcg_distance(oracle['rng_before'], RN.rng_state(entry)) - oracle['rng_calls']
        if steps < 0 or RN.lcg_distance(oracle['rng_after'], RN.rng_state(entry)) != steps:
            raise ValueError(f'RNG draws at {fr["counter"]} precede 00143150')
        rng_external[fr['counter']] = steps
    caps.cache.clear()
    return dict(external_scratch=sorted(external), rng_external=rng_external,
                node_unexplained=unexplained)


OBSERVATION = ROOT / 'evidence/m13/continuous-observation.json'
PAD_INPUT = (0x3eaf00, 0x3eaf40)
# Coordinator seam events (HG_GUEST_FRAME bytes from hg_frame_advance).
COORD_STAGES = {(0x50, 1): 'coordinator-entry', (0x50, 0): 'coordinator-counter'}
RENDER_FIELD = 0xfffffff8
# RAM addresses the reconstructed path writes outside the player/camera structures.
GUARDED_GLOBALS = {
    '3fa190': (0x3fa190, 4), '3f9ff8': (0x3f9ff8, 2),
    '46e118': (0x46e118, 4), '46e110': (0x46e110, 4), '46d930': (0x46d930, 4),
    '3fa040': (0x3fa040, 4), '3fa050': (0x3fa050, 4),
    '3f9e34-210': (0x3f9e34 + 0x210, 4), '3f9e34-220': (0x3f9e34 + 0x220, 4),
    '3f9e38-210': (0x3f9e38 + 0x210, 4), '3f9e38-220': (0x3f9e38 + 0x220, 4),
    '3f9e3c-210': (0x3f9e3c + 0x210, 4), '3f9e3c-220': (0x3f9e3c + 0x220, 4),
    '95e57c': (0x95e57c, 4), '3f9f38': (0x3f9f38, 1), '3f9f6e': (0x3f9f6e, 1),
    '3f9ff2': (0x3f9ff2, 2),
}
GUARDED_STRUCT = [(P, P + 0x540), (CAM, CAM + 0x150)]


def external_domain(address):
    """Classify a non-guarded RAM address for the external-difference report.

    The guest stack is split at the verified 0012f8a8 sp (PD.NATIVE_SP):
    below it are transient callee frames; at and above it are the live
    coordinator and outer frames, which the host-C coordinator does not
    materialize. Both remain external, counted separately."""
    if 0x01fe0000 <= address < PD.NATIVE_SP: return 'guest-stack'
    if PD.NATIVE_SP <= address < 0x02000000: return 'guest-stack-caller-frames'
    if 0x0095e340 <= address < 0x0095e440: return 'libpad-dma'
    if address < 0x00100000: return 'low-ram'
    if PAD_INPUT[0] <= address < PAD_INPUT[1]: return 'input-pad'
    if 0x3f9f00 <= address < 0x3fb000: return 'engine-globals'
    return 'engine-other'


def guarded_ranges():
    ranges = list(GUARDED_STRUCT)
    for _, (start, size) in GUARDED_GLOBALS.items():
        ranges.append((start, start + size))
    # 00199300/0c/28 updates each sprite record's carried phase. The native
    # dispatch value becomes the next frame's post-counter input unchanged.
    ranges.extend((0x39e96c+0x20*i,0x39e970+0x20*i) for i in range(12))
    return ranges


GUARDED_RANGES = tuple(guarded_ranges())
COMPILED_SERVICE_CONTROLS = ('store-width-pre-fix', 'phase-step-sign', 't1-omitted')
RENDER_HANDOFF_CONTROL = 'omitted-packet-bank-switch'
VERIFICATION_SOURCES = ('src/resource_loader.c', 'src/resource_loader.h',
                        'tools/verify_player_continuous.py', 'tools/predispatch_differential.py',
                        'tools/render_node_differential.py',
                        'tools/verify_player_connected_runtime.py', 'tools/ee_subset.py')
M15_COMPILED_CONTROLS = RN.MUTANTS


def in_guarded(address, ranges=GUARDED_RANGES):
    for start, end in ranges:
        if start <= address < end: return True
    return False


def render_handoff(actual, expected):
    """Compare the persistent inputs produced by the inter-frame render setup."""
    fields = {}
    for name, gp_offset, offsets in (
        ('render_context', 0x3098, (0x53c, 0x540, *PD.CURSORS, 0x598, 0x59c)),
        ('sort_queue', 0x3094, (0x2000, 0x2004)),
    ):
        left = actual.peek_word(PD.GP + gp_offset)
        right = expected.peek_word(PD.GP + gp_offset)
        fields[name] = dict(actual=hex(left), expected=hex(right))
        if left == right:
            for offset in offsets:
                fields[f'{name}+{offset:#x}'] = dict(
                    actual=hex(actual.peek_word(left + offset)),
                    expected=hex(expected.peek_word(right + offset)))
    differences = [name for name, row in fields.items() if row['actual'] != row['expected']]
    return dict(result='failed' if differences else 'passed', fields=fields, differences=differences)


class ContinuousDriver(G.GroundedDriver):
    """GroundedDriver plus coordinator seam snapshots and the external-call log.

    M15: the 00143150 service marks bracket the C render-node list (snapshot
    and write set for the same-input oracle); the camera chain is bracketed
    from its first call 0015e1d0 to the 0013b6e0 seam. rng_external original
    0011ad58 draws are replayed at the 0013ce80 seam (after 00143150)."""
    def __init__(self, mem, elfdata, segments, status):
        super().__init__(mem, elfdata, segments, status)
        self.external_calls = []
        self.coordinator_entry = None
        self.service_c = []
        self.window_writes = None
        self._frame_writes = None
        self.frame_scratch_writes = None
        self.tail_before = self.tail_after = self.tail_writes = None
        self.model_before = self.model_after = self.model_writes = None
        self._tail_outer = self._chain_outer = None
        self.chain_writes = None
        self.rng_external = 0
        self.rng_replayed = 0
        self.in_tail = False

    def snap(self):
        return super().snap()

    def external(self, pc, a=0, b=0, c=0, d=0):
        if pc == RENDER_FIELD:
            if a not in (0, 1):
                raise ValueError(f'Invalid GS field at render handoff: {a:#x}')
            self.frame_scratch_writes = {addr - 0x70000000 for addr in self.mem.writes
                                         if 0x70000000 <= addr < 0x70004000}
            return 1 - a
        if pc == 0xfffffff6:
            self.service_c.append((a,b))
            if a in (RN.TAIL, RN.MODEL) and b == 1:
                if self.in_tail: raise ValueError('Nested M15 service marks')
                self.in_tail = True
                before = self.snap()
                self._tail_outer, self.mem.writes = self.mem.writes, set()
                if a == RN.TAIL: self.tail_before = before
                else: self.model_before = before
            elif a in (RN.TAIL, RN.MODEL) and b == 0:
                writes = self.mem.writes
                self.mem.writes = self._tail_outer | writes
                if a == RN.TAIL: self.tail_writes, self.tail_after = writes, self.snap()
                else: self.model_writes, self.model_after = writes, self.snap()
                self.in_tail = False
            return 0
        if pc == V.GUEST_FRAME and self.in_tail:
            # The 00143150 (0x20) and 0012bc70-chain frames must not alias the
            # 0015fc10 (0x20) grounded-entry/return stage keys of GroundedDriver.
            return super(G.GroundedDriver, self).external(pc, a, b, c, d)
        if pc == 0xfffffff7:
            # Writes between coordinator-counter and here are the window's.
            self.window_writes = self.mem.writes
            self.mem.writes = self._frame_writes | self.window_writes
            self.stage['pre-dispatch'] = self.snap();return 0
        if pc == V.GUEST_FRAME and (a, b) in COORD_STAGES:
            self.stage[COORD_STAGES[(a, b)]] = self.snap()
            if (a, b) == (0x50, 0):
                self._frame_writes, self.mem.writes = self.mem.writes, set()
            if (a, b) == (0x50, 1): self.coordinator_entry = self.stage['coordinator-entry']
            return 0
        if pc == 0x15e1d0 and self.camera_entry is None:
            self._chain_outer, self.mem.writes = self.mem.writes, set()
        if pc == 0xfffffff3:
            self.external_calls.append(hex(a))
            # 0013b6e0 is the first coordinator tail service after the camera chain
            # returns (0012f8d8); the original's camera-return stop is 0012f8d4,
            # immediately before it. Snapshot here: end-of-frame memory would also
            # include the 001379f0 counter advance, which runs after the stop.
            if a == 0x13b6e0:
                self.chain_writes = self.mem.writes
                self.mem.writes = self._chain_outer | self.chain_writes
                self.stage['camera-return'] = self.snap()
            if a == 0x13ce80:
                for _ in range(self.rng_external):
                    V.ConnectedRuntimeDriver.external(self, RN.RNG)
                    self.rng_replayed += 1
            return 0
        return super().external(pc, a, b, c, d)


def observation():
    return json.loads(OBSERVATION.read_text())


class ContinuousRun:
    def __init__(self, lib, caps, obs, pad_delta=None, ownership=None, replay_rng=True):
        self.lib = lib; self.caps = caps; self.obs = obs
        self.frames = sorted(obs['frames'], key=lambda fr: fr['counter'])
        start_stage = 'coordinator-entry' if 'coordinator-entry' in self.frames[0]['captures'] else 'dispatch-entry'
        self.start_stage = start_stage
        self.mem = caps.load(self.frames[0]['captures'][start_stage]['name'])
        self.mem.initial = bytes(self.mem.raw)
        self.external = V.pad_dma_buffers(self.mem)
        self.mem.watch = [(e['start'], e['end']) for e in self.external]
        # M15 RAM guards: the four render-node records (every inter-frame change
        # explained by 00143150, see inter_frame_ownership) and the RNG state.
        rng = RN.rng_state_address(self.mem)
        self.guarded_ranges = GUARDED_RANGES + (NODE_RANGE, (rng, rng + 8))
        self.ownership = ownership if ownership is not None else inter_frame_ownership(caps, obs)
        if self.ownership['node_unexplained']:
            raise ValueError('Render-node records changed outside 00143150')
        self.inter_external = set(self.ownership['external_scratch'])
        self.rng_external = self.ownership['rng_external'] if replay_rng else {}
        self.pad_delta = self._pad_deltas() if pad_delta is None else pad_delta
        if not hasattr(caps, 'window_oracles'):
            caps.window_oracles = {}
        self.oracles = caps.window_oracles

    def window_oracle(self, fr, native_before, native_after):
        """Original-instruction write set of this frame's native window
        (0012f8a8 -> 00158560 from the captured post-counter stop)."""
        key = fr['counter']
        if key not in self.oracles:
            row = PD.window_oracle(native_before, self.caps.elfdata, self.caps.segments)
            if row['identity_mismatches']:
                raise ValueError('Window oracle executed non-ELF instructions')
            self.oracles[key] = dict(
                writes=row['writes'], steps=row['steps'],
                ram_differences=len(PD.changed(row['memory'], native_after, PD.STACK[1])),
                scratch_differences=sum(x != y for x, y in
                                        zip(row['memory'].scratch, native_after.scratch)))
        return self.oracles[key]

    def compare_window(self, d, fr):
        """Mandatory pre-dispatch window-output comparison (see
        predispatch_differential.compare_window): every byte either the
        reconstruction or the original writes in the window, outside the guest
        stack and libpad DMA, at its absolute address or packet-stream offset."""
        native_before = self.caps.load(fr['captures']['coordinator-counter']['name'])
        native_after = self.caps.load(fr['captures']['dispatch-entry']['name'])
        oracle = self.window_oracle(fr, native_before, native_after)
        before, after = d.stage['coordinator-counter'], d.stage['pre-dispatch']
        dma = [(e['start'], e['end']) for e in self.external]
        result = PD.compare_window(before, after, d.window_writes, native_before, native_after,
                                   oracle['writes'], dma)
        result.update(capture=fr['captures']['dispatch-entry']['name'],
                      oracle_capture=fr['captures']['coordinator-counter']['name'],
                      oracle_steps=oracle['steps'],
                      oracle_capture_differences=dict(ram=oracle['ram_differences'],
                                                      scratch=oracle['scratch_differences']))
        if oracle['ram_differences'] or oracle['scratch_differences']:
            result.update(result='failed', diagnosis=dict(kind='native-oracle-mismatch'))
        elif result['result'] == 'failed':
            result['diagnosis'] = self.diagnose_window(
                before, after, d.window_writes, native_before, native_after, result, dma)
        return result

    def diagnose_window(self, before, after, writes, native_before, native_after, result, dma):
        try:
            replay = PD.window_oracle(before, self.caps.elfdata, self.caps.segments, trace_writes=True)
        except ValueError as exc:
            return dict(kind='same-input-replay-error', error=str(exc))
        if replay['identity_mismatches']:
            return dict(kind='same-input-replay-error', error='Carried input executed non-ELF instructions')
        same_input = PD.compare_window(before, after, writes, before, replay['memory'], replay['writes'], dma)
        if same_input['result'] == 'failed':
            kind = 'window-reconstruction-difference'
            samples = PD.window_difference_writers(same_input, after, replay['memory'], replay['writers'])
        else:
            kind = 'carried-input-difference'
            native = PD.window_oracle(native_before, trace_writes=True)
            samples = PD.window_difference_writers(result, after, native_after, native['writers'])
        return dict(kind=kind, same_input_result=same_input['result'], sampled_differences=samples)

    def same_input(self, before, after, writes, entry, label):
        """M15 same-input differential: original instructions for entry on
        the reconstruction's own input snapshot, compared byte for byte."""
        try:
            oracle = RN.run_original(before, entry, self.caps.elfdata, self.caps.segments)
        except ValueError as exc:
            return dict(result='failed', service=label, error=str(exc)[:400])
        result = RN.compare_same_input(after, writes, oracle, label)
        if oracle['identity_mismatches']:
            result['result'] = 'failed'
        return result

    def scratch_excluded(self, fr, label, capture):
        """Scratch offsets excluded at one native stop (M15 ownership rule).

        Only the inter-frame writes of unreconstructed services are excluded:
        at both coordinator stops all of them; at later stops those not yet
        rewritten by reconstructed code, i.e. minus the original M14 window's
        write set and minus every offset the original changed since the
        coordinator-counter stop (all code between that stop and camera return
        is reconstructed in C or runs original instructions)."""
        if label in COORDINATOR_STAGES:
            return self.inter_external
        counter = self.caps.load(fr['captures']['coordinator-counter']['name'])
        native_after = self.caps.load(fr['captures']['dispatch-entry']['name'])
        window = self.window_oracle(fr, counter, native_after)['writes']
        stop = self.caps.load(capture)
        rewritten = {a - 0x70000000 for a in window if 0x70000000 <= a < 0x70004000}
        rewritten.update(o for o in range(0x4000) if stop.scratch[o] != counter.scratch[o])
        return self.inter_external - rewritten

    def stage_results(self, d, fr, only_failures=False):
        """Every stage comparison for one frame, including the pre-dispatch
        stage (against dispatch-entry), its window-output comparison and the
        M15 same-input render-node and camera-chain differentials."""
        stages = {}
        for label, snapshot in d.stage.items():
            row = fr['captures'].get('dispatch-entry' if label == 'pre-dispatch' else label)
            if row is None:
                if label == 'pre-dispatch': raise AssertionError('Missing native dispatch-entry capture')
                continue
            expected = self.caps.load(row['name'])
            result = self.compare_stage(snapshot, expected, self.scratch_excluded(fr, label, row['name']))
            result['capture'] = row['name']
            stages[label] = result
            if label == 'coordinator-counter':
                stages['render-handoff'] = render_handoff(snapshot, expected)
            if label == 'pre-dispatch':
                stages['pre-dispatch-window'] = self.compare_window(d, fr)
            if only_failures and any(r['result'] != 'passed' for r in stages.values()):
                return stages
        stages['camera-chain'] = self.same_input(d.camera_entry, d.stage['camera-return'],
                                                 d.chain_writes, RN.CAMERA, '0012c790')
        if only_failures and stages['camera-chain']['result'] != 'passed':
            return stages
        stages['model-draw'] = self.same_input(d.model_before, d.model_after, d.model_writes,
                                               RN.MODEL, '0012bc70')
        if only_failures and stages['model-draw']['result'] != 'passed':
            return stages
        stages['render-node-tail'] = self.same_input(d.tail_before, d.tail_after, d.tail_writes,
                                                     RN.TAIL, '00143150')
        stages['render-node-tail']['rng_replayed'] = d.rng_replayed
        return stages

    def _pad_deltas(self):
        """Pad-area bytes the original input system changed between frames, per
        target frame: {counter: {address: value}}. Controller input evidence only."""
        deltas = {}
        for fr, nxt in zip(self.frames, self.frames[1:]):
            a = self.caps.load(fr['captures']['camera-return']['name'])
            b = self.caps.load(nxt['captures']['dispatch-entry']['name'])
            out = {}
            for address in range(*PAD_INPUT):
                if a.raw[address] != b.raw[address]:
                    out[address] = b.raw[address]
            deltas[nxt['counter']] = out
        self.caps.cache.clear()
        return deltas

    def inject_input(self, counter):
        delta = self.pad_delta.get(counter, {})
        for address, value in delta.items():
            self.mem.raw[address] = value

    def run_frame(self, fr, mutate=None):
        self.mem.writes.clear()
        self.inject_input(fr['counter'])
        for address, (value, width) in (mutate or {}).items():
            self.mem.store(address, value, width)
        driver = ContinuousDriver(self.mem, self.caps.elfdata, self.caps.segments, STATUS_VALUES[0])
        driver.rng_external = self.rng_external.get(fr['counter'], 0)
        fn = getattr(self.lib, 'hg_player_frame_continuous')
        fn.argtypes = [C.POINTER(ResourceEnv), C.c_uint32]; fn.restype = None
        fn(C.byref(driver.env), 0x1ff8000)
        if driver.errors: raise AssertionError(driver.errors)
        if driver.guest_frame != 0: raise AssertionError('Unbalanced guest frame')
        driver.stage['history-copy'] = driver.history
        driver.stage['camera-entry'] = driver.camera_entry
        if 'camera-return' not in driver.stage:
            raise AssertionError('Missing camera-return seam coverage')
        if 'pre-dispatch' not in driver.stage or driver.service_c != [
                (0x198f60,1),(0x198f60,0),(RN.MODEL,1),(RN.MODEL,0),(RN.TAIL,1),(RN.TAIL,0)]:
            raise AssertionError('Missing or incomplete C pre-dispatch or render-node service')
        if driver.declared_registers != [(0x14f060, regs) for _, regs in driver.declared_registers] or \
                len(driver.declared_registers) != 12 or driver.pending_registers is not None:
            raise AssertionError('Incomplete 0014f060 non-ABI register contract')
        if driver.pending_wide is not None or driver.declared_wide.count((0x14d4f0, RN.SHADOW_TAG)) != 1 or \
                any(row not in ((0x14d4f0, RN.PARTICLE_TAG), (0x14d4f0, RN.SHADOW_TAG))
                    for row in driver.declared_wide):
            raise AssertionError('Incomplete 0014d4f0 wide a3 contract')
        if driver.rng_replayed != driver.rng_external:
            raise AssertionError('External RNG draws not replayed')
        for target in (0x17f6d0,0x17f2d0):
            if not any(row['entry']==hex(target) for row in driver.modeled_calls):
                raise AssertionError(f'Missing interpreted pre-dispatch service {target:#x}')
        return driver

    def compare_stage(self, actual, expected, excluded):
        """Guarded-domain comparison plus the external-difference enumeration.

        RAM: the guarded domains (player/camera structures, counters, edge
        counters, history flag, sprite phases, M15 render-node records and RNG
        state) must match; all other differences are classified into external
        domains. Scratch: the complete 16 KiB is compared except the offsets
        excluded by scratch_excluded for this stop.

        The 32 MB RAM image is compared in 64 KB chunks (memcmp on equal chunks);
        only divergent regions are scanned byte-wise. Divergent addresses are
        classified into guarded violations (kept in full) and external domains
        (counted, with a bounded address sample retained) so results stay small
        even when carried state diverges broadly from the capture."""
        guarded_ram = []
        external_counts = {}
        external_samples = {}
        chunk = 0x10000
        for base in range(0, 0x2000000, chunk):
            window = slice(base, base + chunk)
            if actual.raw[window] == expected.raw[window]:
                continue
            for a in range(base, base + chunk):
                if actual.raw[a] == expected.raw[a]:
                    continue
                if in_guarded(a, self.guarded_ranges):
                    guarded_ram.append(a)
                    continue
                domain = external_domain(a)
                external_counts[domain] = external_counts.get(domain, 0) + 1
                sample = external_samples.get(domain)
                if sample is None:
                    external_samples[domain] = sample = []
                if len(sample) < EXTERNAL_SAMPLE:
                    sample.append(a)
        guarded_scratch = []
        external_scratch = []
        external_scratch_total = 0
        for off in range(0x4000):
            if actual.scratch[off] == expected.scratch[off]: continue
            if off not in excluded: guarded_scratch.append(off)
            else:
                external_scratch_total += 1
                if len(external_scratch) < EXTERNAL_SAMPLE: external_scratch.append(off)
        return dict(guarded_ram=[hex(a) for a in guarded_ram[:16]], guarded_ram_count=len(guarded_ram),
                    guarded_scratch=[hex(0x70000000 + o) for o in guarded_scratch[:16]],
                    guarded_scratch_count=len(guarded_scratch),
                    scratch_compared=0x4000 - len(excluded),
                    external_ram={d: c for d, c in sorted(external_counts.items())},
                    external_ram_count=len(external_counts),
                    external_ram_samples={d: [hex(a) for a in v] for d, v in sorted(external_samples.items())},
                    external_scratch_count=external_scratch_total,
                    external_scratch_samples=[hex(0x70000000 + o) for o in external_scratch],
                    result='passed' if not guarded_ram and not guarded_scratch else 'failed')


def sequence(lib, caps, obs, ownership=None, pad_delta=None):
    run = ContinuousRun(lib, caps, obs, pad_delta, ownership)
    rows = []
    failures = []
    for fr in run.frames:
        d = run.run_frame(fr)
        stages = run.stage_results(d, fr)
        failures.extend((fr['counter'], label) for label, result in stages.items()
                        if result['result'] != 'passed')
        for index, (snapshot, row) in enumerate(zip(d.pushes, fr['pushes'])):
            expected = caps.load(row['name'])
            result = run.compare_stage(snapshot, expected, run.scratch_excluded(fr, 'push', row['name']))
            result['capture'] = row['name']
            if result['result'] != 'passed': failures.append((fr['counter'], f'push-{index}'))
            stages[f'push-{index}-{row["tier"]}'] = result
        rows.append(dict(counter=fr['counter'], phase=fr['phase'], stages=stages,
                         external_calls=d.external_calls, rng_replayed=d.rng_replayed))
        print(fr['counter'], 'passed' if not any(c == fr['counter'] for c, _ in failures) else 'FAILED',
              flush=True)
        del d; caps.cache.clear(); gc.collect()
    return dict(frames=rows, failures=[dict(counter=c, stage=s) for c, s in failures],
                inter_frame_external_scratch=[hex(0x70000000 + o) for o in sorted(run.inter_external)],
                rng_external={str(k): v for k, v in sorted(run.ownership['rng_external'].items())},
                node_unexplained=run.ownership['node_unexplained'],
                result='passed' if not failures else 'failed')


def controls(lib, caps, obs, folder, ownership=None, pad_delta=None):
    folder.mkdir(parents=True, exist_ok=True)
    rows = []
    frames = sorted(obs['frames'], key=lambda fr: fr['counter'])
    # Use the first real handoff for every control. The uninterrupted acceptance
    # run independently compares every subsequent frame.
    previous, target = frames[:2]
    if ownership is None:
        ownership = inter_frame_ownership(caps, obs)

    def measure(name, kind, mutate=None, restore=False, post_mutate=None, replay_rng=True, at=None):
        run = ContinuousRun(lib, caps, obs, pad_delta, ownership, replay_rng)
        goal = target if at is None else next(fr for fr in frames if fr['counter'] == at)
        for fr in run.frames:
            caps.cache.clear()
            if restore and fr['counter'] == goal['counter']:
                # Restore the authentic frame-start state (the coordinator-entry
                # capture: the frame begins there, before any reconstructed write).
                stage = fr['captures'].get('coordinator-entry') or fr['captures']['dispatch-entry']
                fresh = caps.load(stage['name'])
                run.mem.raw[:] = fresh.raw
                run.mem.scratch[:] = fresh.scratch
            run.mem.watch = [(e['start'], e['end']) for e in run.external]
            if fr['counter'] == goal['counter'] and mutate:
                run.mem.initial = bytes(run.mem.raw)
            d = run.run_frame(fr, mutate if fr['counter'] == goal['counter'] else None)
            if fr['counter'] == previous['counter'] and post_mutate:
                for address,(value,width) in post_mutate.items():
                    run.mem.store(address,value,width)
            if fr['counter'] != goal['counter']:
                del d; caps.cache.clear(); continue
            for label, result in run.stage_results(d, fr, only_failures=True).items():
                if result['result'] != 'passed':
                    return dict(control=name, kind=kind, frame=fr['counter'], result='rejected',
                                first_divergent_stage=label, detail=result)
            # The clean restoration control isolates the target frame's window.
            if kind == 'null-control':
                return dict(control=name, kind=kind, frame=goal['counter'], result='clean',
                            note='restored target frame matched all compared stages')
            return dict(control=name, kind=kind, frame=fr['counter'], result='ESCAPED')
        return dict(control=name, kind=kind, frame=goal['counter'], result='error', detail='target frame not reached')

    cases = [
        ('null-capture-restoration', 'null-control', None, dict(restore=True)),
        ('corrupt-player-carry', 'continuity-corruption', {P + 0x30: (0x42280000, 4)}, {}),
        ('corrupt-camera-carry', 'continuity-corruption', {CAM + 0x0: (0xc2c80000, 4)}, {}),
        ('corrupt-service-phase-carry', 'continuity-corruption', None,
         dict(post_mutate={0x39e98c: (0, 4)})),
        ('omitted-counter-advance', 'continuity-corruption', {0x46e110: (0, 4)}, {}),
        ('omitted-frame-advance', 'continuity-corruption', {0x3fa190: (0, 4)}, {}),
        ('wrong-input-stick', 'input-counterfactual', {0x3eaf10: (0, 4)}, {}),
        # M15: the anchor node's carried accumulator (node+60) after frame 10164.
        ('corrupt-render-node-carry', 'continuity-corruption', None,
         dict(post_mutate={0x528490: (0x3f800000, 4)})),
        # M15: without the external RNG draws the carried LCG state diverges.
        ('omitted-external-rng-draws', 'input-counterfactual', None, dict(replay_rng=False)),
        ('null-capture-restoration-substate-1', 'null-control', None, dict(restore=True, at=10173)),
    ]
    for name, kind, mutate, kw in cases:
        try:
            row = measure(name, kind, mutate, **kw)
        except Exception as exc:
            row = dict(control=name, kind=kind, frame=target['counter'], result='error', detail=str(exc)[:400])
        rows.append(row); print(name, row['result'], row.get('first_divergent_stage'), flush=True)
        with (folder / (name + '.result.json')).open('x') as out:
            json.dump(row, out, indent=2); out.write('\n')
        gc.collect()
    # Compiled defects (source mutation builds): the M12 gravity step, then
    # 00198f60 store-width, arithmetic and register-contract defects, then the
    # M15 projection defects. None mutates memory after execution.
    source = (ROOT / 'src/resource_loader.c').read_text()
    before = 'uint32_t gravity=r8(e,a+0x4ad)==0?0x3da3d70au:0x3cf5c28fu;'
    after = 'uint32_t gravity=r8(e,a+0x4ad)==0?0x3cf5c28fu:0x3cf5c28fu;'
    compiled = [('wrong-gravity-step', before, after, None)]
    for name in COMPILED_SERVICE_CONTROLS:
        _, old, new = PD.mutants(source)[name]
        compiled.append((name, old, new, None))
    compiled.append((RENDER_HANDOFF_CONTROL, 'w32(e,ctx+0x598,1u-bank);',
                     'w32(e,ctx+0x598,bank);', None))
    for name, _, old, new, at in M15_COMPILED_CONTROLS:
        compiled.append((name, old, new, at))
    for name, before, after, at in compiled:
        try:
            if source.count(before) != 1: raise AssertionError(f'{name} target not unique')
            path = folder / f'{name}.c'
            path.open('x').write(source.replace(before, after))
            mlib = library(source=path, output=folder / f'{name}.so')
            goal = target if at is None else next(fr for fr in frames if fr['counter'] == at)
            row = measure_compiled(mlib, name, caps, obs, goal, ownership, pad_delta)
        except Exception as exc:
            row = dict(control=name, kind='compiled-defect', frame=target['counter'],
                       result='error', detail=str(exc)[:400])
        rows.append(row); print(row['control'], row['result'], row.get('first_divergent_stage'), flush=True)
        with (folder / (row['control'] + '.result.json')).open('x') as out:
            json.dump(row, out, indent=2); out.write('\n')
        gc.collect()
    return rows


def measure_compiled(mlib, name, caps, obs, target, ownership, pad_delta=None):
    run = ContinuousRun(mlib, caps, obs, pad_delta, ownership)
    for fr in run.frames:
        caps.cache.clear()
        try:
            d = run.run_frame(fr)
        except (ValueError, AssertionError) as exc:
            return dict(control=name, kind='compiled-defect', frame=fr['counter'], result='error',
                        first_divergent_stage='execution-fault', detail=str(exc)[:400])
        if fr['counter'] != target['counter']: continue
        if name == RENDER_HANDOFF_CONTROL:
            window = run.compare_window(d, fr)
            handoff = render_handoff(d.stage['coordinator-counter'],
                                     caps.load(fr['captures']['coordinator-counter']['name']))
            detected = ('render_context+0x598' in handoff['differences'] and
                        window.get('diagnosis', {}).get('kind') == 'carried-input-difference')
            return dict(control=name, kind='compiled-defect', frame=fr['counter'],
                        result='rejected' if detected else 'ESCAPED',
                        first_divergent_stage='render-handoff' if detected else None,
                        detail=dict(handoff=handoff, window=window))
        stages = run.stage_results(d, fr)
        failed = [label for label, result in stages.items() if result['result'] != 'passed']
        return dict(control=name, kind='compiled-defect', frame=fr['counter'],
                    result='rejected' if failed else 'ESCAPED',
                    first_divergent_stage=failed[0] if failed else None, failed_stages=failed,
                    detail={label: stages[label] for label in failed[:3]})
    return dict(control=name, kind='compiled-defect', frame=target['counter'], result='error')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf', type=Path, required=True)
    p.add_argument('--ps2dinfo', type=Path, required=True)
    p.add_argument('--capture-dir', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--negative-controls', action='store_true')
    p.add_argument('--max-frames', type=int, default=None,
                   help='smoke run: first N frames only (never a milestone receipt)')
    a = p.parse_args()
    if a.max_frames is not None and a.max_frames < (2 if a.negative_controls else 1):
        p.error('--max-frames requires a positive count, or at least two with --negative-controls')
    if a.output.exists() or a.output.with_suffix('.so').exists(): raise FileExistsError('Fresh output required')
    if digest(a.elf) != EXPECTED: raise ValueError('Wrong original ELF')
    obs = observation()
    if a.max_frames is not None:
        obs['frames'] = sorted(obs['frames'], key=lambda fr: fr['counter'])[:a.max_frames]
    caps = Captures(a.capture_dir); caps.elfdata = a.elf.read_bytes()
    for fr in obs['frames']:
        for row in list(fr['captures'].values()) + fr['pushes']:
            if digest(caps.path(row['name'])) != row['sha256']: raise ValueError('Observation/capture digest mismatch')
            for shot in row['screenshots']:
                if digest(a.capture_dir.parent / 'stops' / shot['name']) != shot['sha256']: raise ValueError('Screenshot digest mismatch')
    caps.segments, _ = shared_module(a.ps2dinfo).segments_from_elf(caps.elfdata)
    for fr in obs['frames']:
        if caps.load(fr['captures']['dispatch-entry']['name']).load(0x46e118) != fr['counter']:
            raise ValueError('Wrong capture frame')
    caps.cache.clear()
    lib = library(output=a.output.with_suffix('.so'))
    ownership = inter_frame_ownership(caps, obs)
    probe = ContinuousRun(lib, caps, obs, None, ownership)
    result = sequence(lib, caps, obs, ownership, probe.pad_delta)
    if a.negative_controls:
        result['negative_controls'] = controls(lib, caps, obs,
                                               a.output.parent / (a.output.stem + '-controls'),
                                               ownership, probe.pad_delta)
    ok = result['result'] == 'passed'
    if a.negative_controls:
        ok &= all(c['result'] == ('clean' if c['kind'] == 'null-control' else 'rejected')
                  for c in result['negative_controls'])
    result.update(schema='hg-player-continuous/v5', result='passed' if ok else 'failed', elf_sha256=EXPECTED,
                  observation_sha256=digest(OBSERVATION), frame_count=len(obs['frames']),
                  smoke_max_frames=a.max_frames, native_sp=hex(PD.NATIVE_SP),
                  verification_sources={n: digest(ROOT / n) for n in VERIFICATION_SOURCES},
                  library_sha256=digest(a.output.with_suffix('.so')))
    with a.output.open('x') as out:
        json.dump(result, out, indent=1); out.write('\n')
    print(result['result'], 'frames', len(obs['frames']), 'failures', result.get('failures', []))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
