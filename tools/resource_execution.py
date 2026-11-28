"""Host scheduling and optical I/O for the normal EE resource worker."""
import ctypes as C
import hashlib

from hg_identify import ISO, SECTOR, read_at
import lifecycle_differential as L

TASK = 0x3f8e00
TASK_ACTIVE = TASK + 0x30
TASK_ID = TASK + 0x34
BUSY = 0x3eacec
ORIGINAL_ISO = '8ca1513c8417c0a40b838ff5edf22a2360f43bce1b0458693a95a6efdafd994e'
ORIGINAL_ELF = 'a74248c5e2fd2cce9cede6c04d8117ce79e9472d380bbd80eb410370abcf3c61'
READ32 = C.CFUNCTYPE(C.c_uint32, C.c_void_p, C.c_uint32)
STATUS = C.CFUNCTYPE(C.c_int32, C.c_void_p, C.c_int32)
READ = C.CFUNCTYPE(C.c_int32, C.c_void_p, C.c_uint32, C.c_uint32, C.c_uint32,
                  C.POINTER(C.c_uint8))
ERROR = C.CFUNCTYPE(C.c_int32, C.c_void_p)


class ReadEnv(C.Structure):
    _fields_ = [('user', C.c_void_p), ('read32', READ32), ('sync', STATUS),
                ('ready', STATUS), ('read', READ), ('error', ERROR),
                ('inhibit', C.c_uint8), ('active', C.c_uint8),
                ('chunk_flag_a', C.c_uint8), ('chunk_flag_b', C.c_uint8)]


class Job(C.Structure):
    _fields_ = [(name, C.c_uint32) for name in ('stage', 'pack', 'after', 'retained_after')]


class RetailDisc:
    """Read only PACK and AREA sectors on a single authenticated open stream."""

    def __init__(self, stream):
        digest = hashlib.sha256()
        for data in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(data)
        if digest.hexdigest() != ORIGINAL_ISO:
            raise ValueError('Original SCPS-15046 ISO SHA-256 differs')
        self.stream = stream
        self.iso = ISO(stream)
        self.elf = self.iso.read('/SCPS_150.46')
        if (hashlib.sha256(self.elf).hexdigest() != ORIGINAL_ELF or
                b'BOOT2 = cdrom0:\\SCPS_150.46;1\r\n' not in self.iso.read('/SYSTEM.CNF')):
            raise ValueError('Original SCPS-15046 boot identity differs')
        self.pack = self.iso.files['/DATA/PACK.DAT']
        self.areas = {name: rec for name, rec in self.iso.files.items() if name.startswith('/AREA')}
        self.extents = {
            rec['lba']: (rec['lba'] + (rec['size_bytes'] + SECTOR - 1) // SECTOR, name)
            for name, rec in self.areas.items()
        }
        self.extents[self.pack['lba']] = (self.pack['lba'] + self.pack['size_bytes'] // SECTOR, 'PACK')

    def read(self, lba, count):
        if not count or not any(start <= lba and lba + count <= end
                                for start, (end, _) in self.extents.items()):
            raise ValueError(f'CDVD read outside PACK/AREA extents {lba:#x}+{count:#x}')
        return read_at(self.stream, lba * SECTOR, count * SECTOR)


class ResourceScheduler:
    """One normal thread on a shared bus. Vblank start delivers normal wakes.

    A media backend supplies bytes and a positive per-transfer latency in
    scheduler ticks. Only the callback at that tick writes the DMA destination.
    The caller runs the reconstructed interrupt handlers, so only 00104e10
    for this thread releases its SleepThread continuation.
    """

    def __init__(self, lib, env, memory, read_sectors, latency=None, first_thread=1):
        self.lib, self.env, self.memory = lib, env, memory
        self.read_sectors = read_sectors
        self.latency = latency if latency is not None else lambda _lba, _count: 1
        self.next_thread = first_thread
        self.clock = 0
        self.job = self.thread = self.transfer = None
        self.started = self.exited = False
        self.fault = None
        self.requests, self.finished, self.reads = [], [], []
        self.read_state = (C.c_uint32 * 11)()
        self.read_env = ReadEnv(None, READ32(self._read32), STATUS(self._sync),
                                STATUS(self._ready), READ(self._read), ERROR(lambda _: 0))
        env.read_state = C.addressof(self.read_state)
        env.read_env = C.addressof(self.read_env)
        lib.hg_resource_job_resume.argtypes = [C.POINTER(L.Env), C.POINTER(Job), C.c_uint32]
        lib.hg_resource_job_resume.restype = C.c_uint32

    def load(self, addr, width=4):
        return int.from_bytes(self.memory.read(addr, width), 'little')

    def _read32(self, _, addr):
        try:
            return self.load(addr)
        except ValueError as exc:
            self.fault = str(exc)
            return 0

    def _sync(self, _, _arg):
        return int(self.transfer is not None or self.fault is not None)

    def _ready(self, _, _arg):
        return 2

    def _read(self, _, lba, count, dst, mode):
        # The original read issue loops synchronously when CDVD returns 0.
        # A backend error is terminal here: keep sync busy and report the fault.
        try:
            if self.transfer is not None or bytes(mode[:3]) != b'\0\1\0':
                raise ValueError('overlapping or malformed CDVD transfer')
            ticks = self.latency(lba, count)
            if ticks < 1 or dst + count * SECTOR > 0x2000000:
                raise ValueError('invalid CDVD delay or EE DMA window')
            self.transfer = (self.clock + ticks, lba, count, dst)
            self.reads.append(dict(tick=self.clock, lba=lba, sectors=count, destination=dst))
        except (ValueError, OSError) as exc:
            self.fault = str(exc)
        return 1

    def tick(self):
        """Advance optical time before delivering this tick's interrupts."""
        self.clock += 1
        if self.fault or self.transfer is None or self.transfer[0] > self.clock:
            return
        _, lba, count, dst = self.transfer
        try:
            data = self.read_sectors(lba, count)
            if len(data) != count * SECTOR:
                raise ValueError('short CDVD transfer')
            self.memory.write(dst, data)
        except (ValueError, OSError) as exc:
            self.fault = str(exc)
            return
        self.reads[-1]['completed_tick'] = self.clock
        self.transfer = None

    def service(self, pc, a, b):
        """Kernel seams for 00143360, 001434f0 and the vblank handlers."""
        if pc == 0x103d60:
            if a != TASK or self.thread is not None or self.load(TASK + 4) != 0x130220:
                raise ValueError('unsupported loader thread descriptor')
            if (self.load(TASK + 8), self.load(TASK + 0xc), self.load(TASK + 0x10),
                    self.load(TASK + 0x14)) != (0x3f6e00, 0x2000, 0x3e7f70, 5):
                raise ValueError('invalid normal thread stack, GP or priority')
            if self.load(TASK + 0x32, 2) > 8:
                raise ValueError('unsupported normal loader request')
            self.thread = self.next_thread
            self.next_thread += 1
            self.job, self.started, self.exited = Job(), False, False
            return self.thread
        if pc == 0x103d80:
            if a != self.thread or b or self.started or a != self.load(TASK_ID):
                raise ValueError('StartThread for an unexpected loader thread')
            self.started = True
            self.requests.append(dict(tick=self.clock, thread=a, kind=self.load(TASK + 0x32, 2),
                                      directory=self.load(TASK + 0x44), first_read=len(self.reads)))
            self.resume()
            return 0
        if pc == 0x104e10:
            if a != self.thread or not self.started:
                raise ValueError('wake for an unexpected loader thread')
            self.resume()
            return 0
        if pc == 0x103da0:
            if not self.started or self.exited or self.load(TASK_ACTIVE, 2) or self.load(BUSY, 1):
                raise ValueError('ExitDeleteThread before original worker completion')
            self.exited = True
            return 0
        raise ValueError(f'unknown loader kernel callback {pc:08x}')

    def resume(self):
        if self.fault:
            return
        # Reserved within the descriptor's guest stack. C uses this only for
        # local fixup scratch, below its own live resource and file buffers.
        done = self.lib.hg_resource_job_resume(C.byref(self.env), C.byref(self.job), 0x3f8900)
        if self.fault:
            return
        if bool(done) != self.exited or (done and self.transfer is not None):
            raise ValueError('inconsistent loader return/exit or pending DMA')
        if not done:
            if self.load(TASK_ACTIVE, 2) != 1 or self.load(BUSY, 1) != 1:
                raise ValueError('pending worker lost active or busy state')
            return
        self.finished.append(dict(tick=self.clock, thread=self.thread, last_read=len(self.reads),
                                  kind=self.load(TASK + 0x32, 2), directory=self.load(TASK + 0x44)))
        self.thread = self.job = None


def initialize_file_records(memory, disc, area_paths):
    """Title/AREA SDK file-record seam for a cold host start."""
    def record(address, file):
        memory.write(address, file['lba'].to_bytes(4, 'little') + file['size_bytes'].to_bytes(4, 'little'))

    record(0x3ead20, disc.pack)
    for index, path in enumerate(area_paths):
        if path is not None:
            record(0x3f3a30 + index * 8, disc.areas[path])
