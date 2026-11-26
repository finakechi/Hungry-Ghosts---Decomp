"""Drive the bounded SG2IOP C service on the existing EE audio guest bus."""
import ctypes as C
import os
import struct
import subprocess
from pathlib import Path

from .audio_guest import AudioHarness, CTX_SAVE, HostModel, IOP_WINDOW_BASE, ROOT

U = C.c_uint32
P = C.c_void_p
SW = C.CFUNCTYPE(None, P, U, C.c_uint16)
SR = C.CFUNCTYPE(C.c_uint16, P, U)
DMA = C.CFUNCTYPE(None, P, U, U, U, U, U)
READ = C.CFUNCTYPE(C.c_bool, P, U, P, C.c_size_t)
UPLOAD_START, UPLOAD_POLL, BANK_BIND = 0x2a7560, 0x2a7590, 0x2b5428
RPC_ID = 0x11223344


class Packet(C.Structure):
    _fields_ = [(name, U) for name in ('command', 'arg1', 'arg2', 'arg3')]


class Reply(C.Structure):
    _fields_ = [('envelope', U * 48), ('upload_busy', U)]


class VoiceParams(C.Structure):
    _fields_ = [(name, U) for name in ('note', 'root', 'scale', 'left', 'right')]
    _fields_ += [('adsr1', C.c_uint16), ('adsr2', C.c_uint16), ('fine_offset', C.c_int8),
                 ('bend', C.c_uint8), ('bend_scale', C.c_uint8)]


class Io(C.Structure):
    _fields_ = [('ctx', P), ('read_iop', READ), ('write16', SW), ('transfer', DMA), ('read16', SR)]


class Ee(C.Structure):
    _fields_ = [('bus', P), ('iop', P), ('upload', Packet), ('upload_state', U)]


class ConnectedHost(HostModel):
    def __init__(self, memory, harness):
        super().__init__(memory)
        self.harness = harness
        self.frees = 0

    def _sif_alloc(self, size):
        if self.sif_heap or not 0 < size <= 0x400000:
            return 0
        return super()._sif_alloc(size)

    def call(self, target, a0, a1, a2, a3, t0):
        if target in (UPLOAD_START, UPLOAD_POLL, BANK_BIND):
            result = self.harness.lib.hg_audio_ee_bank_call(
                C.byref(self.harness.ee), target, a0, a1, a2)
            if result == 0xffffffff:
                raise ValueError(f'SG2 EE hook rejected {target:#x}')
            return result
        if target == 0x10b0a8:
            if a0 != IOP_WINDOW_BASE or not self.sif_heap or self.dma_pending:
                raise ValueError(f'IOP heap release rejected {a0:#x}')
            self.sif_heap.clear()
            self.iop_audio.reset()
            self.frees += 1
            return 0
        return super().call(target, a0, a1, a2, a3, t0)


class ConnectedAudio(AudioHarness):
    def __init__(self, destinations, library, read16=None):
        self.library = library
        super().__init__()
        self.model = ConnectedHost(self.mem, self)
        self.mem.load(CTX_SAVE, struct.pack('<5I', *destinations))
        self.register = self._bind('hg_audio_register_entry', ['resource'])
        self.events = []
        self._read = READ(self._read_iop)
        self._write = SW(lambda _, addr, data: self.events.append(('write', addr, data)))
        self._dma = DMA(lambda _, *args: self.events.append(('dma', *args)))
        self._read16 = SR(lambda _, address: read16(address)) if read16 else SR()
        self.ee = Ee()
        self.iop = self.lib.hg_audio_iop_create(
            Io(None, self._read, self._write, self._dma, self._read16))
        if not self.iop:
            raise MemoryError('SG2 service allocation failed')
        self.lib.hg_audio_ee_service_init(C.byref(self.ee), self._bus_p, self.iop)

    @staticmethod
    def build(output):
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-O2', '-Wall', '-Wextra',
                        '-Werror', '-pedantic', '-fPIC', '-shared', '-Wl,--no-undefined',
                        str(ROOT / 'src/audio/audio_engine.c'),
                        str(ROOT / 'src/audio/audio_iop.c'), '-lm', '-o', str(output)], check=True)

    def _compile(self):
        library = C.CDLL(str(Path(self.library).resolve()))
        for name, result, args in [
            ('hg_audio_iop_create', P, [Io]),
            ('hg_audio_iop_destroy', None, [P]),
            ('hg_audio_iop_rpc', C.c_bool, [P, U, U, P, C.c_size_t, C.POINTER(Reply)]),
            ('hg_audio_iop_step_dma', C.c_bool, [P]),
            ('hg_audio_iop_read_spu', C.c_bool, [P, U, P, C.c_size_t]),
            ('hg_audio_iop_render', None, [P, P, C.c_size_t]),
            ('hg_audio_ee_service_init', None, [C.POINTER(Ee), P, P]),
            ('hg_audio_ee_post', C.c_bool, [C.POINTER(Ee), Packet]),
            ('hg_audio_ee_flush', C.c_bool, [C.POINTER(Ee), C.POINTER(Reply)]),
            ('hg_audio_ee_bank_call', U, [C.POINTER(Ee), U, U, U, U]),
            ('hg_audio_ee_sample_address', C.c_bool, [C.POINTER(Ee), U, U, C.POINTER(U)]),
            ('hg_audio_ee_stage_voice', C.c_bool, [C.POINTER(Ee), U, U, U, VoiceParams]),
            ('hg_audio_ee_stage_play_voice', C.c_bool, [C.POINTER(Ee), U, U, U, VoiceParams]),
            ('hg_audio_ee_stage_key', C.c_bool, [C.POINTER(Ee), U, C.c_bool]),
        ]:
            function = getattr(library, name)
            function.restype = result
            function.argtypes = args
        return library

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.lib.hg_audio_iop_destroy(self.iop)

    def _read_iop(self, _, source, out, size):
        try:
            data = self.model.read_iop(source, size)
        except ValueError:
            return False
        C.memmove(out, bytes(data), size)
        return True

    def register_step(self, address):
        result = self.register(self._bus_p, address)
        self._raise_errors()
        return result

    def post(self, record):
        result = self.lib.hg_audio_ee_post(C.byref(self.ee), Packet(*record))
        self._raise_errors()
        return result

    def flush(self):
        reply = Reply()
        result = self.lib.hg_audio_ee_flush(C.byref(self.ee), C.byref(reply))
        self._raise_errors()
        return reply if result else None

    def rpc(self, wire=b'', server=RPC_ID, function=0):
        reply = Reply()
        if self.lib.hg_audio_iop_rpc(self.iop, server, function, wire, len(wire), C.byref(reply)):
            return reply
        return None

    def step_dma(self):
        return self.lib.hg_audio_iop_step_dma(self.iop)

    def sample_address(self, slot, offset):
        address = U()
        result = self.lib.hg_audio_ee_sample_address(C.byref(self.ee), slot, offset, C.byref(address))
        self._raise_errors()
        return address.value if result else None

    def spu(self, dest, size):
        buffer = C.create_string_buffer(size)
        if not self.lib.hg_audio_iop_read_spu(self.iop, dest, buffer, size):
            raise ValueError('unmapped SPU2 span')
        return buffer.raw

    def render(self, frames):
        buffer = (C.c_int16 * (frames * 2))()
        self.lib.hg_audio_iop_render(self.iop, buffer, frames)
        return list(zip(buffer[::2], buffer[1::2]))

    def stage_voice(self, voice, slot, offset, note, root, scale, left, right, adsr1, adsr2,
                    fine_offset=0, bend=64, bend_scale=1):
        params = VoiceParams(note, root, scale, left, right, adsr1, adsr2,
                             fine_offset, bend, bend_scale)
        result = self.lib.hg_audio_ee_stage_voice(C.byref(self.ee), voice, slot, offset, params)
        self._raise_errors()
        return result

    def stage_key(self, voice, on):
        result = self.lib.hg_audio_ee_stage_key(C.byref(self.ee), voice, on)
        self._raise_errors()
        return result

    def stage_play_voice(self, play_index, voice, offset, params):
        result = self.lib.hg_audio_ee_stage_play_voice(
            C.byref(self.ee), play_index, voice, offset, params)
        self._raise_errors()
        return result
