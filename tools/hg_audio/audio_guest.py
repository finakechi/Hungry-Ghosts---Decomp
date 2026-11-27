#!/usr/bin/env python3
"""Guest bus + host model driving the reconstructed audio C (ctypes).

Layers (explicitly separated):
  1. Reconstructed original EE functions (src/audio/audio_engine.c).
  2. Modeled external seams: EE kernel syscalls (FlushCache, sceSifSetDma,
     sceSifDmaStat), EI/DI, the SIF-side allocator, SIF0_MSCOM writes, and
     the audio tick/time helpers. These are host models with documented
     observable contracts, NOT claims of original implementation.
  3. Host-only IOP audio service: consumes the DMA'd payload and queued
     channel commands and produces deterministic responses.

The bus memory is a sparse dict over 4-byte words; uninitialized reads of
the audio state area raise (fail-closed), except for a small allowlist of
documented zero-initialized scratch areas.
"""
import ctypes
import struct
import subprocess
import tempfile
from pathlib import Path
from hg_verify_build import build_path

ROOT = Path(__file__).resolve().parent.parent.parent

# --- guest constants (must match audio_engine.h) ---
GATE = 0x003eaa50
PHASE = 0x003eaa51
DESC_COUNT = 0x003eaa53
DESC_CURSOR = 0x003eaa5c
DESC_SOURCE = 0x003eaa60
DESC_SAVED = 0x003eaa64
DESC_DMA = 0x003eaa68
DESC_ID = 0x003eaa70
IDX_BASE = 0x003eaa00
CTX_SAVE = 0x003e0720
SLOT_TABLE = 0x00a87440
SLOT_AUX = 0x00a87450
ID_TABLE = 0x003f2740
CHANNEL_BASE = 0x00a6c4d8
CHANNEL_QUEUE = 0x00a6c530
VOICE_CURSOR = 0x00a80158
COMMAND_BASE = 0x00a82640
COMMAND_CURSOR = 0x00a83040
SEQUENCE = 0x00a832a4
PHASE_TABLE = 0x003db520

X_INTS_OFF = 0x0010cca0
X_INTS_ON = 0x0010cce8
X_FLUSHCACHE = 0x001041e0
X_SIF_SET_DMA = 0x00104320
X_SIF_DMA_STAT = 0x00104300
X_SIF_ALLOC = 0x0010af40
X_SIF_MSCOM = 0x0010b0a8
X_GLOBAL_TICK = 0x002a74d0
X_TIME_A = 0x001165d0
X_TIME_B = 0x00116320
X_PLAY_EXTRA = 0x002ad358
X_FLAG_SVC = 0x002adab0
X_2A7560 = 0x002a7560
X_2A7590 = 0x002ad0a0
X_2B5428 = 0x002b5428
X_2A9D70 = 0x002a9d70
X_CH_INIT_A = 0x002a9050
X_CH_INIT_B = 0x002a9180

MASK32 = 0xFFFFFFFF


class BusMemory:
    def __init__(self):
        self.data = {}

    def _check(self, address, size):
        if address % 4 != 0 and size != 1 and size != 2:
            raise ValueError(f'unaligned access {address:#x}')
        for at in range(address, address + size):
            if at not in self.data:
                raise ValueError(f'unmapped read at {at:#x}')

    def r8(self, address):
        self._check(address, 1)
        return self.data[address] & 0xFF

    def r16(self, address):
        if address % 2:
            raise ValueError(f'odd halfword read {address:#x}')
        self._check(address, 2)
        return (self.data[address] | (self.data[address + 1] << 8)) & 0xFFFF

    def r32(self, address):
        if address % 4:
            raise ValueError(f'word read {address:#x}')
        self._check(address, 4)
        return (self.data[address] | (self.data[address + 1] << 8)
                | (self.data[address + 2] << 16) | (self.data[address + 3] << 24))

    def w8(self, address, value):
        self.data[address] = value & 0xFF

    def w16(self, address, value):
        self.data[address] = value & 0xFF
        self.data[address + 1] = (value >> 8) & 0xFF

    def w32(self, address, value):
        self.data[address] = value & 0xFF
        self.data[address + 1] = (value >> 8) & 0xFF
        self.data[address + 2] = (value >> 16) & 0xFF
        self.data[address + 3] = (value >> 24) & 0xFF

    def load(self, address, blob):
        for i, byte in enumerate(blob):
            self.data[address + i] = byte

    def read_blob(self, address, size):
        return bytes(self.data[address + i] for i in range(size))


IOP_WINDOW_BASE = 0x40000000


class HostModel:
    """Modeled external seams + host IOP audio service."""

    def __init__(self, memory):
        self.mem = memory
        self.ints = 0
        self.sif_heap = bytearray()
        self.dma_pending = {}
        self.dma_seq = 0
        self.mscom = []
        self.tick = 0
        self.time_seq = 0
        self.iop_audio = IopAudioService()
        self.flush_count = 0
        self.extra_calls = []

    def read_iop(self, address, size):
        """IOP-side heap is EE-visible through the SIF window."""
        offset = address - IOP_WINDOW_BASE
        if 0 <= offset and offset + size <= len(self.sif_heap):
            return self.sif_heap[offset:offset + size]
        raise ValueError(f'unmapped IOP read at {address:#x}')

    def call(self, target, a0, a1, a2, a3, t0):
        if target == X_INTS_OFF:
            self.ints = 1
            return 0
        if target == X_INTS_ON:
            self.ints = 0
            return 0
        if target == X_FLUSHCACHE:
            self.flush_count += 1
            return 0
        if target == X_SIF_SET_DMA:
            return self._sif_set_dma(a0, a1)
        if target == X_SIF_DMA_STAT:
            return self._sif_dma_stat(a0)
        if target == X_SIF_ALLOC:
            return self._sif_alloc(a0)
        if target == X_SIF_MSCOM:
            return self._sif_mscom(a0)
        if target == X_GLOBAL_TICK:
            self.tick += 1
            return self.tick
        if target == X_TIME_A:
            self.time_seq += 1
            return (a0 * 300 + self.time_seq) & MASK32
        if target == X_TIME_B:
            # modeled: returns a0 + a1-derived fixed low word
            return (a0 ^ (a1 >> 16)) & MASK32
        if target == X_2A7560:
            # original: tail-jumps to 2acff0(0x13, a0, a1, a2)
            self.extra_calls.append(('xfer_b_setup', a0, a1, a2))
            self.dma_seq += 1
            return 0x13 + self.dma_seq
        if target in (X_2A7590, 0x002a7590):
            # modeled poll: returns 0 (transfer complete)
            return 0
        if target == X_2B5428:
            self.extra_calls.append(('bank_hook', a0, a1))
            return 0
        if target == X_2A9D70:
            self.extra_calls.append(('channel_param', a0, a1, a2))
            return 0
        if target in (X_CH_INIT_A, X_CH_INIT_B):
            return 0
        if target == X_PLAY_EXTRA:
            self.extra_calls.append(('play_extra', a0, a1, a2, a3))
            return 0
        if target == X_FLAG_SVC:
            self.extra_calls.append(('flag_svc', a0, a1, a2, a3))
            return 0
        raise ValueError(f'unmodeled external call {target:#x}')

    # --- modeled SIF boundary ---
    def _sif_alloc(self, size):
        if size > 0x400000:
            return 0
        base = len(self.sif_heap)
        self.sif_heap += bytearray(size & MASK32)
        # modeled IOP-visible address window
        return (0x40000000 + base) & MASK32

    def _sif_set_dma(self, dma_ptr, sid):
        # The C writes the 4-word {src,dest,size,attr} struct (the SDK
        # SifDmaTransfer_t layout) into guest scratch and passes its
        # address.
        src = self.mem.r32(dma_ptr + 0x00)
        dest = self.mem.r32(dma_ptr + 0x04)
        size = self.mem.r32(dma_ptr + 0x08)
        if size == 0:
            return 0
        self.dma_seq += 1
        handle = self.dma_seq
        self.dma_pending[handle] = (src, dest, size)
        return handle

    def _sif_dma_stat(self, handle):
        if handle not in self.dma_pending:
            return -1
        src, dest, size = self.dma_pending.pop(handle)
        blob = self.mem.read_blob(src, size)
        self._dma_to_iop(dest, blob)
        return -1  # completed (original polls until < 0)

    def _dma_to_iop(self, dest, blob):
        offset = dest - IOP_WINDOW_BASE
        if offset < 0 or offset + len(blob) > len(self.sif_heap):
            raise ValueError(f'unmapped IOP DMA destination {dest:#x}')
        self.sif_heap[offset:offset + len(blob)] = blob
        self.iop_audio.receive_dma(dest, blob)

    def _sif_mscom(self, payload_ptr):
        first = self.read_iop(payload_ptr, 1)[0]
        self.mscom.append(first)
        self.iop_audio.receive_mscom(first, payload_ptr, self.mem)
        return 0

    _dma_hook = None

    def _hook_dma(self, dma_ptr):
        if HostModel._dma_hook is None:
            raise ValueError('DMA struct hook not installed')
        return HostModel._dma_hook(dma_ptr)


class IopAudioService:
    """Host-only model of the game's IOP audio service.

    Observable boundary behavior:
      - receives payload DMA (the bank's sample payload),
      - receives the MSCOM byte at registration completion,
      - receives channel command bytes and replies with deterministic
        status words.
    This is explicitly a bounded host model, not an emulated IOP.
    """

    def __init__(self):
        self.payloads = []
        self.mscom = []
        self.commands = []
        self.banks = {}

    def receive_dma(self, dest, blob):
        self.payloads.append({'dest': dest, 'size': len(blob),
                              'sha256': _sha256(blob), 'blob': blob})

    def receive_mscom(self, command, payload_ptr, mem):
        self.mscom.append(command)

    def queue_command(self, channel, command):
        self.commands.append((channel, command))

    def reset(self):
        self.payloads.clear()
        self.mscom.clear()
        self.commands.clear()
        self.banks.clear()


def _sha256(blob):
    import hashlib
    return hashlib.sha256(blob).hexdigest()


class AudioHarness:
    """Compiles and drives the reconstructed C via ctypes."""

    def __init__(self, model=None):
        self.mem = BusMemory()
        self.model = model or HostModel(self.mem)
        self.lib = self._compile()
        self.errors = []
        self._install_callbacks()
        self._init_bss()

    def _init_bss(self):
        """Zero the audio state areas (the original's boot-time bss)."""
        for base, size in (
                (0x003e0720, 0x20), (0x003eaa00, 0x100),
                (SLOT_TABLE, 0x200), (SLOT_AUX, 0x200),
                (ID_TABLE, 0x800), (CHANNEL_BASE, 32 * 0x9e4),
                (CHANNEL_QUEUE, 32 * 0x9e4),
                (VOICE_CURSOR, 0x10), (COMMAND_BASE, 0xa00),
                (COMMAND_CURSOR, 0x10), (SEQUENCE, 0x10),
                (PHASE_TABLE, 0x20)):
            self.mem.load(base, bytes(size))
        # channels start free: the audio init marks +0 with -1 (the
        # open-channel scan treats -1 as the free marker)
        for i in range(32):
            self.mem.w32(CHANNEL_BASE + i * 0x9e4, 0xffffffff)

    def _raise_errors(self):
        if self.errors:
            exc = self.errors[0]
            self.errors.clear()
            raise exc

    def _compile(self):
        cache = build_path(ROOT / 'local' / 'audio' / 'libhg_audio.so')
        cache.parent.mkdir(parents=True, exist_ok=True)
        src = ROOT / 'src' / 'audio' / 'audio_engine.c'
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                        '-pedantic', '-O1', '-fPIC', '-shared',
                        str(src), '-o', str(cache)], check=True)
        return ctypes.CDLL(str(cache))

    def _install_callbacks(self):
        C = ctypes

        def guard(fn, *args):
            try:
                return fn(*args)
            except Exception as exc:  # noqa: BLE001 - surfaced to caller
                if not self.errors:
                    self.errors.append(exc)
                return 0

        def r8_any(address):
            if IOP_WINDOW_BASE <= address:
                blob = self.model.read_iop(address, 1)
                return blob[0]
            return self.mem.r8(address)

        def r16_any(address):
            if IOP_WINDOW_BASE <= address:
                return int.from_bytes(self.model.read_iop(address, 2), 'little')
            return self.mem.r16(address)

        def r32_any(address):
            if IOP_WINDOW_BASE <= address:
                return int.from_bytes(self.model.read_iop(address, 4), 'little')
            return self.mem.r32(address)

        self._cb_r8 = C.CFUNCTYPE(C.c_uint8, C.c_void_p, C.c_uint32)(
            lambda ctx, a: guard(r8_any, a))
        self._cb_r16 = C.CFUNCTYPE(C.c_uint16, C.c_void_p, C.c_uint32)(
            lambda ctx, a: guard(r16_any, a))
        self._cb_r32 = C.CFUNCTYPE(C.c_uint32, C.c_void_p, C.c_uint32)(
            lambda ctx, a: guard(r32_any, a))
        self._cb_w8 = C.CFUNCTYPE(None, C.c_void_p, C.c_uint32, C.c_uint8)(
            lambda ctx, a, v: guard(self.mem.w8, a, v))
        self._cb_w16 = C.CFUNCTYPE(None, C.c_void_p, C.c_uint32, C.c_uint16)(
            lambda ctx, a, v: guard(self.mem.w16, a, v))
        self._cb_w32 = C.CFUNCTYPE(None, C.c_void_p, C.c_uint32, C.c_uint32)(
            lambda ctx, a, v: guard(self.mem.w32, a, v))
        self._cb_call = C.CFUNCTYPE(
            C.c_uint32, C.c_void_p, C.c_uint32,
            C.c_uint32, C.c_uint32, C.c_uint32, C.c_uint32, C.c_uint32)(
            lambda ctx, t, a0, a1, a2, a3, t0: guard(
                self.model.call, t, a0, a1, a2, a3, t0))

        class Bus(ctypes.Structure):
            _fields_ = [('r8', ctypes.c_void_p), ('r16', ctypes.c_void_p),
                        ('r32', ctypes.c_void_p), ('w8', ctypes.c_void_p),
                        ('w16', ctypes.c_void_p), ('w32', ctypes.c_void_p),
                        ('call', ctypes.c_void_p), ('ctx', ctypes.c_void_p)]
        self._bus = Bus(ctypes.cast(self._cb_r8, ctypes.c_void_p),
                        ctypes.cast(self._cb_r16, ctypes.c_void_p),
                        ctypes.cast(self._cb_r32, ctypes.c_void_p),
                        ctypes.cast(self._cb_w8, ctypes.c_void_p),
                        ctypes.cast(self._cb_w16, ctypes.c_void_p),
                        ctypes.cast(self._cb_w32, ctypes.c_void_p),
                        ctypes.cast(self._cb_call, ctypes.c_void_p), None)
        self._bus_p = ctypes.pointer(self._bus)

    def _bind(self, name, args):
        fn = getattr(self.lib, name)
        fn.restype = ctypes.c_uint32
        fn.argtypes = [ctypes.c_void_p] + [ctypes.c_uint32] * len(args)
        return fn

    # --- high-level operations ---
    def register_resource(self, blob, address):
        """Drive the full registration state machine to completion."""
        self.mem.load(address, blob)
        for _ in range(400):
            result = self._bind('hg_audio_register_entry',
                                ['resource'])(self._bus_p, address)
            self._raise_errors()
            if result != 0:
                return result
        raise TimeoutError('registration did not complete')

    def play(self, slot, param1, param2=0, param3=0, t0=0):
        result = self._bind('hg_audio_play',
                            ['slot', 'p1', 'p2', 'p3', 't0'])(
            self._bus_p, slot, param1, param2, param3, t0)
        self._raise_errors()
        return result

    def cancel(self, index):
        result = self._bind('hg_audio_cancel', ['i'])(self._bus_p, index)
        self._raise_errors()
        return result

    def channel_command(self, channel, command):
        result = self._bind('hg_audio_channel_command', ['ch', 'cmd'])(
            self._bus_p, channel, command)
        self._raise_errors()
        return result


def make_harness():
    return AudioHarness()
