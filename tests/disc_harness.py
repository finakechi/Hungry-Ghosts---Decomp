"""Compile original reconstruction into ignored local build, expose ctypes ABI."""
import ctypes as C
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from hg_verify_build import build_path

class State(C.Structure):
    _fields_ = [(n,C.c_uint32) for n in ('state_word','error','file_record','destination','byte_offset','byte_length','next_lba','sector_count','sectors_issued','next_destination','between_chunks')]
READ32 = C.CFUNCTYPE(C.c_uint32,C.c_void_p,C.c_uint32)
SYNC = C.CFUNCTYPE(C.c_int32,C.c_void_p,C.c_int32)
READ = C.CFUNCTYPE(C.c_int32,C.c_void_p,C.c_uint32,C.c_uint32,C.c_uint32,C.POINTER(C.c_uint8))
ERROR = C.CFUNCTYPE(C.c_int32,C.c_void_p)
class Env(C.Structure):
    _fields_ = [('user',C.c_void_p),('read32',READ32),('sync',SYNC),('disk_ready',SYNC),('read',READ),('get_error',ERROR),
                ('inhibit',C.c_uint8),('active',C.c_uint8),('chunk_flag_a',C.c_uint8),('chunk_flag_b',C.c_uint8)]

def library():
    out = build_path(ROOT / 'local/build')
    out.mkdir(parents=True,exist_ok=True)
    shared = out/'disc_read.so'
    subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror','-pedantic','-fPIC','-shared',str(ROOT/'src/disc_read.c'),'-o',str(shared)],check=True)
    lib = C.CDLL(str(shared))
    lib.hg_read_setup.argtypes = [C.POINTER(State)] + [C.c_uint32]*4
    lib.hg_read_setup.restype = None
    for name in ('start','continue','poll'):
        f = getattr(lib,'hg_read_'+name)
        f.argtypes = [C.POINTER(State),C.POINTER(Env)]
        f.restype = C.c_int32
    return lib

class Driver:
    def __init__(self, file_lba=1000, file_size=600000, sync=0, ready=2, error=0, failures=0):
        self.lba, self.size = file_lba, file_size
        self.sync_result, self.ready_result, self.error_result = sync,ready,error
        self.failures = failures
        self.calls = []

    def read32(self,address):
        self.calls.append(('read32',address))
        return {0x500000:self.lba,0x500004:self.size}[address]

    def sync(self,arg):
        self.calls.append(('sync',arg))
        return self.sync_result

    def ready(self,arg):
        self.calls.append(('ready',arg))
        return self.ready_result

    def error(self):
        self.calls.append(('error',))
        return self.error_result

    def read(self,lba,count,dst,mode):
        self.calls.append(('read',lba,count,dst,bytes(mode)))
        if self.failures:
            self.failures-=1
            return 0
        return 1

    def environment(self, inhibit=0,active=0,flag_a=0,flag_b=0):
        callbacks = (READ32(lambda _,a:self.read32(a)),SYNC(lambda _,a:self.sync(a)),SYNC(lambda _,a:self.ready(a)),
                     READ(lambda _,l,c,d,m:self.read(l,c,d,bytes(m[:3]))),ERROR(lambda _:self.error()))
        env = Env(None,*callbacks,inhibit,active,flag_a,flag_b)
        env._keepalive = callbacks
        return env
