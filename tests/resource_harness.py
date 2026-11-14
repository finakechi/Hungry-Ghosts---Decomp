"""Host-only RAM/CDVD/kernel seams shared by synthetic and retail checks."""
import ctypes as C
import subprocess
from pathlib import Path
from disc_harness import State, Env, READ32, SYNC, READ, ERROR

ROOT=Path(__file__).resolve().parents[1]
LOAD=C.CFUNCTYPE(C.c_uint32,C.c_void_p,C.c_uint32,C.c_uint32)
STORE=C.CFUNCTYPE(None,C.c_void_p,C.c_uint32,C.c_uint32,C.c_uint32)
CALL=C.CFUNCTYPE(C.c_uint32,C.c_void_p,*([C.c_uint32]*5))
class ResourceEnv(C.Structure):
    _fields_=[('user',C.c_void_p),('load',LOAD),('store',STORE),('call',CALL),
              ('read_state',C.POINTER(State)),('read_env',C.POINTER(Env))]

ENTRIES={'init':0x1431f0,'schedule':0x143360,'finish':0x1434f0,'request_raw':0x130150,
         'request':0x130170,'worker':0x130220,'special':0x1303e0,'bootstrap':0x1306a0,
         'area_index':0x12fbc0,'area_step':0x131810,'area_finalize':0x131dd0}
NAMES={'init':'resource_init','schedule':'resource_schedule','finish':'resource_finish',
       'request_raw':'resource_request_raw','request':'resource_request','worker':'resource_worker',
       'special':'pack_special_step','bootstrap':'pack_bootstrap','area_index':'area_index',
       'area_step':'area_read_step','area_finalize':'area_finalize','bootstrap_step':'pack_bootstrap_step'}
ARITIES={'init':0,'schedule':3,'finish':0,'request_raw':2,'request':2,'worker':0,'special':3,
         'bootstrap':1,'bootstrap_step':1,'area_index':0,'area_step':1,'area_finalize':2}
RETURNS={'special','area_index','area_step','bootstrap_step'}
EXTERNAL_ARITIES={0x119ac8:3,0x1041e0:1,0x103d60:1,0x103d80:2,0x103da0:0,
                  0x1434e0:0,0x137ed0:2,0x156a50:1,0x131da0:1,
                  0x130900:1,0x131420:1,0x130020:1,0x12fe90:1,0x12fde0:1,
                  0x12fcf0:1,0x12ff20:1,0x12ffa0:1}


def library(source=None,output=None,sanitize=False):
    output=output or ROOT/'local/build/resource_loader.so'
    output.parent.mkdir(parents=True,exist_ok=True)
    args=['cc','-std=c11','-O2','-Wall','-Wextra','-Werror','-pedantic','-fPIC','-shared',
          '-I'+str(ROOT/'src'),str(source or ROOT/'src/resource_loader.c'),str(ROOT/'src/disc_read.c'),'-o',str(output)]
    if sanitize:args[1:1]=['-fsanitize=undefined','-fno-sanitize-recover=all']
    subprocess.run(args,check=True)
    lib=C.CDLL(str(output))
    for key,n in ARITIES.items():
        f=getattr(lib,'hg_'+NAMES[key]);f.argtypes=[C.POINTER(ResourceEnv)]+[C.c_uint32]*n
        f.restype=C.c_uint32 if key in RETURNS else None
    return lib


class Memory:
    """Sparse COW pages, zero-initialized declared EE RAM and scratchpad.

    Optional immutable ELF mapping is supplied by the shared ELF parser, not
    guessed from a fixed file offset. Stores never mutate source bytes.
    """
    def __init__(self,elf=b'',segments=()):self.elf=elf;self.segments=segments;self.pages={}
    def valid(self,address,size):
        return 0<=address and size>=0 and (address+size<=0x2000000 or 0x70000000<=address and address+size<=0x70004000)
    def __getitem__(self,address):
        if not self.valid(address,1):raise ValueError(f'Unmapped test memory {address:#x}')
        page=self.pages.get(address>>12)
        if page is not None:return page[address&4095]
        for start,end,offset in self.segments:
            if start<=address<end:return self.elf[offset+address-start]
        return 0
    def __setitem__(self,address,value):
        if not self.valid(address,1):raise ValueError(f'Unmapped test store {address:#x}')
        page=address>>12
        if page not in self.pages:
            base=page<<12;self.pages[page]=bytearray(self[base+i] for i in range(4096))
        self.pages[page][address&4095]=value
    def update(self,pairs):
        for address,value in pairs:self[address]=value
    def get(self,address,size):return bytes(self[address+i] for i in range(size))
    def put(self,address,data):
        if not self.valid(address,len(data)):raise ValueError('Out of range test memory write')
        for i,value in enumerate(data):self[address+i]=value
    def load(self,address,width=4):return int.from_bytes(self.get(address,width),'little')
    def store(self,address,value,width=4):self.put(address,(value&((1<<(width*8))-1)).to_bytes(width,'little'))


class Driver:
    def __init__(self,mem=None):
        self.mem=mem or Memory();self.calls=[];self.files={};self.errors=[]
        self.sync_busy=0;self.ready_result=2;self.error_result=0;self.read_failures=0
        self.audio_busy=0;self.thread_id=17;self.ticks=0;self.fixup_advance=32
        self.state=State()
        self.disc=Env(None,READ32(self._read32),SYNC(self._sync),SYNC(self._ready),READ(self._read),ERROR(self._error),0,0,0,0)
        self.env=ResourceEnv(None,LOAD(self._load),STORE(self._store),CALL(self._external),C.pointer(self.state),C.pointer(self.disc))
    def _guard(self,fn,*args):
        try:return fn(*args)
        except Exception as exc:self.errors.append(repr(exc));return 0
    def _load(self,_,address,width):return self._guard(self.mem.load,address,width)
    def _store(self,_,address,width,value):self._guard(self.mem.store,address,value,width)
    def _read32(self,_,address):return self._guard(self.mem.load,address)
    def _sync(self,_,arg):return self.sync(arg)
    def _ready(self,_,arg):return self.ready(arg)
    def _error(self,_):self.calls.append(('error',));return self.error_result
    def _read(self,_,lba,count,dst,mode):return self._guard(self.read,lba,count,dst,bytes(mode[:3]))
    def _external(self,_,pc,a,b,c,d):return self._guard(self.external,pc,a,b,c,d)
    def sync(self,arg):
        self.calls.append(('sync',arg))
        if self.sync_busy:self.sync_busy-=1;return 1
        return 0
    def ready(self,arg):self.calls.append(('ready',arg));return self.ready_result
    def read(self,lba,count,dst,mode):
        self.calls.append(('read',lba,count,dst,mode))
        if self.read_failures:self.read_failures-=1;return 0
        size=count*2048
        if size>1024*1024:raise ValueError('Test transfer ceiling exceeded')
        block=bytes(size)
        for base,data in self.files.items():
            if base<=lba and (lba-base)*2048<len(data):
                off=(lba-base)*2048;block=data[off:off+size].ljust(size,b'\0');break
        self.mem.put(dst,block);return 1
    def external(self,pc,a=0,b=0,c=0,d=0):
        n=EXTERNAL_ARITIES[pc];args=(a,b,c,d)[:n]
        self.calls.append(('call',pc,*args))
        if pc==0x119ac8:
            if c>65536:raise ValueError('Test memset ceiling exceeded')
            self.mem.put(a,bytes([b&255])*c);return a
        if pc==0x103d60:
            self.calls.append(('thread_descriptor',self.mem.get(a,0x50)));return self.thread_id&0xffffffff
        if pc==0x137ed0:return (a+self.fixup_advance)&0xffffffff
        if pc==0x156a50:
            if self.audio_busy:self.audio_busy-=1;return 0
            return (a+0x80)&0xffffffff
        if pc==0x1434e0:
            self.ticks+=1
            if self.ticks>500:
                self.errors.append('Nonterminating host operation exceeded test budget')
                self.mem.store(0x3f8e39,5,1) # fail closed: terminate then report error
        return 0
    def invoke(self,lib,key,*args):
        result=getattr(lib,'hg_'+NAMES[key])(C.byref(self.env),*args)
        if self.errors:raise AssertionError(self.errors)
        return result


def synthetic_pack(directory_id=0):
    """Two raw members, including a fixup-marked first member; no retail data."""
    import struct
    base=(directory_id+1)*2048;header=directory_id*2048
    data=bytearray(base+4096)
    struct.pack_into('<9I',data,header,directory_id,2,2,base,base,4096,0,0,1)
    struct.pack_into('<4I',data,header+64,base,2048,4,0)
    struct.pack_into('<4I',data,header+80,base+2048,2048,7,0)
    data[base:base+4]=b'TEST';data[base+2048:base+2052]=b'DATA'
    return data


def setup_pack(d,id=0,pack=None):
    d.mem.store(0x3ead20,1000);d.mem.store(0x3ead24,len(pack or synthetic_pack(id)))
    d.mem.store(0x3f8e44,id);d.mem.store(0x3f4d3c,0xc00000)
    d.files[1000]=bytes(pack or synthetic_pack(id))


def machine(d):
    import sys
    sys.path.insert(0,str(ROOT/'tools'))
    from ee_subset import Machine
    m=Machine();m.mem=d.mem;m.reg[28]=0x3e7f70;m.reg[29]=0x1ff0800
    def ext(pc):
        return lambda cpu:d.external(pc,*(cpu.reg[i]&0xffffffff for i in (4,5,6,7)))
    m.hooks={pc:ext(pc) for pc in EXTERNAL_ARITIES}
    m.hooks.update({0x10f520:lambda cpu:d.sync(cpu.reg[4]&0xffffffff),
                    0x10fd28:lambda cpu:d.ready(cpu.reg[4]&0xffffffff),
                    0x110268:lambda cpu:d._error(None),
                    0x10ff30:lambda cpu:d.read(*(cpu.reg[i]&0xffffffff for i in (4,5,6)),cpu.get(cpu.reg[7]&0xffffffff,3))})
    # Reconstructed entries execute their actual instructions, including the
    # worker -> bootstrap call and worker -> finish call. Other loaders stay mocks.
    for entry in ENTRIES.values():m.hooks.pop(entry,None)
    return m
