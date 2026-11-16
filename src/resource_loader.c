/* Original Japanese evidence: analysis/resource_loader/functions.json.
 * Analyst reconstruction; external kernel/codec/fixup implementations remain
 * callbacks. No matching compiler or full PS2 runtime is asserted.
 */
#include "resource_loader.h"

static uint32_t r(HGResourceEnv *e, uint32_t a, uint32_t n) { return e->load(e->user,a,n); }
static void w(HGResourceEnv *e, uint32_t a, uint32_t n, uint32_t v) { e->store(e->user,a,n,v); }
static uint32_t call(HGResourceEnv *e, uint32_t pc, uint32_t a, uint32_t b, uint32_t c, uint32_t d) {
    return e->call(e->user,pc,a,b,c,d);
}
static uint32_t r32(HGResourceEnv *e, uint32_t a) { return r(e,a,4); }
static uint32_t r16(HGResourceEnv *e, uint32_t a) { return r(e,a,2); }
static uint32_t r8(HGResourceEnv *e, uint32_t a) { return r(e,a,1); }
static void w32(HGResourceEnv *e, uint32_t a, uint32_t v) { w(e,a,4,v); }
static void w16(HGResourceEnv *e, uint32_t a, uint32_t v) { w(e,a,2,v); }
static void w8(HGResourceEnv *e, uint32_t a, uint32_t v) { w(e,a,1,v); }
static uint32_t align_sector(uint32_t a) { return (a+0x7ffu)&0xfffff800u; }

/* Host memory bridge into M1's original singleton layout. These copies are
 * adapter operations, not claimed original stores; no concurrent observers or
 * interrupts are modeled by this bridge. M1 handles actual disc state logic. */
static void read_setup(HGResourceEnv *e,uint32_t file,uint32_t dst,uint32_t off,uint32_t size) {
    hg_read_setup(e->read_state,file,dst,off,size);
    w32(e,0x3eacf0,0);w32(e,0x3eacf4,0);
    w32(e,0x3eacf8,file);w32(e,0x3eacfc,dst);w32(e,0x3ead00,off);w32(e,0x3ead04,size);
}
static uint32_t read_poll(HGResourceEnv *e) {
    uint32_t words[11];
    for(uint32_t i=0;i<11;i++)words[i]=r32(e,0x3eacf0+i*4);
    /* Explicit field construction avoids aliasing a uint32_t array as a struct. */
    HGReadState s={words[0],words[1],words[2],words[3],words[4],words[5],words[6],words[7],words[8],words[9],words[10]};
    *e->read_state=s;
    e->read_env->inhibit=(uint8_t)r8(e,0x3eace4);
    e->read_env->active=(uint8_t)r8(e,0x3eace8);
    e->read_env->chunk_flag_a=(uint8_t)r8(e,0x3f2c80);
    e->read_env->chunk_flag_b=(uint8_t)r8(e,0x3f2fa0);
    uint32_t result=(uint32_t)hg_read_poll(e->read_state,e->read_env);
    s=*e->read_state;
    uint32_t out[11]={s.state_word,s.error,s.file_record,s.destination,s.byte_offset,s.byte_length,s.next_lba,s.sector_count,s.sectors_issued,s.next_destination,s.between_chunks};
    for(uint32_t i=0;i<11;i++)w32(e,0x3eacf0+i*4,out[i]);
    w8(e,0x3eace8,e->read_env->active);
    return result;
}
static void clear_task_phases(HGResourceEnv *e,uint32_t task) {
    for(uint32_t i=0x38;i<0x3c;i++)w8(e,task+i,0);
    w16(e,task+0x3c,0);w16(e,task+0x3e,0);
}

/* 0012fcf0. The high byte is an arithmetic word shift, not an unsigned
 * table index. All table/address arithmetic remains modulo 2^32. */
void hg_resource_load_indexed(HGResourceEnv *e,uint32_t task) {
    uint32_t id=r32(e,task+0x44),high=id>>8;
    if((id&0x80000000u)!=0)high|=0xff000000u;
    uint32_t table=r32(e,0x3e0200u+((high-2u)<<2));
    w32(e,task+0x44,r32(e,table+((id&255u)<<2)));
    id=r32(e,task+0x44);
    if(id==0xffffffffu || r16(e,0x46e130u+(r16(e,0x46e136)<<1))==id)return;
    clear_task_phases(e,task);
    w16(e,0x46e136,r16(e,0x46e136)+1u);
    if(r16(e,0x46e136)>=3)w16(e,0x46e136,0);
    uint32_t slot=r16(e,0x46e136);
    w16(e,0x46e130u+(slot<<1),r32(e,task+0x44));
    call(e,0x130900,task,0,0,0);
}

/* 0012fde0: context-to-directory lookup; ff and ffffffff are distinct
 * no-load sentinels. The second table uses the original unadjusted index. */
void hg_resource_load_context(HGResourceEnv *e,uint32_t task) {
    uint32_t sub=r8(e,0x3fa201);
    if(sub==255)return;
    uint32_t kind=r8(e,0x3fa200);
    uint32_t address=kind<3?0x3e01c0u+kind*12u+sub*4u:0x3e01e4u+kind*4u;
    w32(e,task+0x44,r32(e,address));
    if(r32(e,task+0x44)==0xffffffffu)return;
    clear_task_phases(e,task);
    call(e,0x130900,task,0,0,0);
}

void hg_resource_prepare_context(HGResourceEnv *e,uint32_t task) {
    if(r32(e,0x700000c4)!=2)return;
    w16(e,0x46e136,0);w16(e,0x46e130,0);
    w16(e,0x46e132,0);w16(e,0x46e134,0);
    if(r8(e,0x46e12d)==2 && r8(e,0x3fa202)!=255) {
        w32(e,task+0x44,r8(e,0x3fa202)|0x200u);
        call(e,0x12fcf0,task,0,0,0);
    }else call(e,0x12fde0,task,0,0,0);
}

void hg_resource_prepare_retained(HGResourceEnv *e,uint32_t task) {
    if(r32(e,0x700000c4)!=2 && r8(e,0x3fa1ba)==0)return;
    uint32_t kind=r8(e,0x3fa1b0),wanted=kind==2?0x4d:0x4c;
    if(wanted!=r8(e,0x3f9fef)) {
        w32(e,task+0x44,kind==0 && r8(e,0x3fa1b1)==3 && r8(e,0x3fa1b2)==3?0x4a:0x6b);
        clear_task_phases(e,task);call(e,0x130900,task,0,0,0);
        /* The first loader may change the selector: reload it as the EE does. */
        w32(e,task+0x44,r8(e,0x3fa1b0)==2?0x4d:0x4c);
        w8(e,0x3f9fef,r32(e,task+0x44));
        clear_task_phases(e,task);call(e,0x130900,task,0,0,0);
    }
    w8(e,0x46e10c,1);
}

void hg_resource_init(HGResourceEnv *e) {
    call(e,0x119ac8,0x3ead30,0,0x2c,0);
    call(e,0x119ac8,0x3f8e00,0,0x50,0);
    call(e,0x119ac8,0x3f6d80,0,0x50,0);
}

void hg_resource_schedule(HGResourceEnv *e,uint32_t kind,uint32_t id,uint32_t entry) {
    uint32_t task,stack,priority;
    if((kind&0xffffu)==100) {task=0x3f6d80;stack=0x3f4d80;priority=3;}
    else {
        task=0x3f8e00;stack=0x3f6e00;priority=5;
        if(r16(e,task+0x30)!=0)return;
    }
    w16(e,task+0x32,kind);w16(e,task+0x30,1);
    for(uint32_t i=0x38;i<0x50;i+=4)w32(e,task+i,0);
    w32(e,task+0x44,id);w32(e,task+4,entry);
    w32(e,task+8,stack);w32(e,task+0xc,0x2000);
    w32(e,task+0x14,priority);w32(e,task+0x10,0x3e7f70);
    uint32_t thread=call(e,0x103d60,task,0,0,0);
    w32(e,task+0x34,thread);
    /* The original does not check CreateThread failure before StartThread. */
    call(e,0x103d80,r32(e,task+0x34),0,0,0);
}

void hg_resource_finish(HGResourceEnv *e) {
    if(r16(e,0x3f8e30)==1) {
        w16(e,0x3f8e30,0);
        call(e,0x103da0,0,0,0,0);
    }
}

void hg_resource_request_raw(HGResourceEnv *e,uint32_t kind,uint32_t id) {
    w8(e,0x3eacec,1);w8(e,0x3eace0,0);
    hg_resource_schedule(e,kind,id,0x130220);
}
void hg_resource_request(HGResourceEnv *e,uint32_t kind,uint32_t id) {
    if((id==0x67 || id==0x4e || id==9) && r16(e,0x3fa1c4)!=0)id++;
    hg_resource_request_raw(e,kind,id);
}

void hg_resource_worker(HGResourceEnv *e) {
    uint32_t task=0x3f8e00;
    switch(r16(e,task+0x32)) {
    case 0:hg_pack_bootstrap(e,task);break;
    case 1:call(e,0x130900,task,0,0,0);break;
    case 2:
        call(e,0x131420,task,0,0,0);
        w32(e,0x3eaf88,r32(e,task+0x44)); /* gp + 3018 */
        if(r8(e,0x3fa1b0)==1 || (r8(e,0x3fa1b0)==0 && r8(e,0x3fa1b1)==8 && r8(e,0x3fa1b2)==1)) {
            w32(e,task+0x44,r16(e,0x3fa1c4)==0?5:6);
            clear_task_phases(e,task);call(e,0x130900,task,0,0,0);
        }
        w32(e,task+0x44,r32(e,0x3eaf88));w16(e,task+0x32,3);
        w32(e,task+0x44,r32(e,task+0x44)+r8(e,0x3fa1b2)+1);
        clear_task_phases(e,task);
        /* falls through */
    case 3:
        call(e,0x131420,task,0,0,0);clear_task_phases(e,task);
        call(e,0x130020,task,0,0,0);call(e,0x12fe90,task,0,0,0);break;
    case 4:call(e,0x12fde0,task,0,0,0);break;
    case 5:call(e,0x12fcf0,task,0,0,0);break;
    case 6:call(e,0x12ff20,task,0,0,0);break;
    case 7:call(e,0x12ffa0,task,0,0,0);break;
    case 8:call(e,0x130020,task,0,0,0);break;
    default:break;
    }
    w8(e,0x3eacec,0);hg_resource_finish(e);
}

uint32_t hg_pack_special_step(HGResourceEnv *e,uint32_t task,uint32_t directory,uint32_t counts) {
    uint32_t cursor,address,result;uint16_t count;
    switch(r8(e,task+0x3b)) {
    case 0:
        w32(e,task+0x4c,directory+0x40);w16(e,task+0x3c,r16(e,counts+0x18));
        if(r16(e,task+0x3c)==0)w8(e,task+0x3b,3);
        else {
            cursor=r32(e,task+0x4c);
            read_setup(e,0x3ead20,r32(e,task+0x48),r32(e,cursor),r32(e,cursor+4));
            w8(e,task+0x3b,1);
        }break;
    case 1:case 5:case 9:
        if(read_poll(e)!=0)w8(e,task+0x3b,r8(e,task+0x3b)+1);
        break;
    case 2:
        result=call(e,0x156a50,r32(e,task+0x48),0,0,0);
        if(result!=0) {
            cursor=r32(e,task+0x4c);w32(e,0x3f4300+r32(e,cursor+8)*4,result);
            w32(e,task+0x48,result);w8(e,task+0x3b,3);w32(e,task+0x4c,cursor+0x10);
        }break;
    case 3:
        w16(e,task+0x3c,r16(e,counts+0x1a));w8(e,task+0x3b,r16(e,task+0x3c)==0?7:4);break;
    case 4:case 8:
        cursor=r32(e,task+0x4c);
        read_setup(e,0x3ead20,r32(e,task+0x48),r32(e,cursor),r32(e,cursor+4));
        w8(e,task+0x3b,r8(e,task+0x3b)+1);break;
    case 6:
        cursor=r32(e,task+0x4c);address=r32(e,task+0x48);
        { uint32_t member_id=r32(e,cursor+8);
        w32(e,0x3f4300+member_id*4,address);
        result=call(e,0x137ed0,r32(e,task+0x48),0,member_id,0); }w32(e,task+0x48,result);
        w32(e,task+0x4c,r32(e,task+0x4c)+0x10);
        count=(uint16_t)(r16(e,task+0x3c)-1);w16(e,task+0x3c,count);w8(e,task+0x3b,count==0?7:4);break;
    case 7:
        w16(e,task+0x3c,r16(e,counts+0x1c));w8(e,task+0x3b,r16(e,task+0x3c)==0?11:8);break;
    case 10:
        call(e,0x131da0,r32(e,task+0x48),0,0,0);
        w32(e,task+0x4c,r32(e,task+0x4c)+0x10);
        count=(uint16_t)(r16(e,task+0x3c)-1);w16(e,task+0x3c,count);w8(e,task+0x3b,count==0?11:8);break;
    case 11:return 1;
    default:break;
    }
    return 0;
}

uint32_t hg_pack_bootstrap_step(HGResourceEnv *e,uint32_t task) {
    uint32_t header=0x3f3b00;
    switch(r8(e,task+0x39)) {
    case 0:
        w32(e,task+0x48,r32(e,task+0x44)==0?0xb00000:r32(e,0x3f4d3c));
        read_setup(e,0x3ead20,header,r32(e,task+0x44)<<11,0x800);
        w8(e,task+0x39,r8(e,task+0x39)+1);break;
    case 1:
        if(read_poll(e)!=0)w8(e,task+0x39,r8(e,task+0x39)+1);
        break;
    case 2:
        if(r32(e,header+4)==0)return 1;
        if(hg_pack_special_step(e,task,header,header)!=0)w8(e,task+0x39,r8(e,task+0x39)+1);
        break;
    case 3:
        read_setup(e,0x3ead20,r32(e,task+0x48),r32(e,header+0x10),r32(e,header+0x14));
        w8(e,task+0x39,r8(e,task+0x39)+1);break;
    case 4:
        if(read_poll(e)!=0)w8(e,task+0x39,r8(e,task+0x39)+1);
        break;
    case 5: {
        uint32_t remaining_fixups=r32(e,header+0x20),i=0;
        if(r32(e,header+8)!=0)do {
            uint32_t cursor=r32(e,task+0x4c),id=r32(e,cursor+8);
            w32(e,0x3f4300+id*4,r32(e,task+0x48)+(r32(e,cursor)-r32(e,header+0x10)));
            if(remaining_fixups!=0) {
                remaining_fixups--;call(e,0x137ed0,r32(e,0x3f4300+id*4),0,id,0);
            }
            i++;w32(e,task+0x4c,r32(e,task+0x4c)+0x10);
        }while(i<r32(e,header+8));
        uint32_t end=align_sector(r32(e,task+0x48)+r32(e,header+0x14));
        w32(e,0x3f4d44,end);w32(e,0x3f4d54,r32(e,0x3f4d44)+0x2f8000);
        w32(e,0x3f4d5c,r32(e,0x3f4d44)+0x348000);
        w32(e,0x3f4d50,r32(e,0x3f4d54));w32(e,0x3f4d4c,r32(e,0x3f4d54));
        w32(e,0x3f4d40,r32(e,0x3f4d44));w32(e,0x3f4d3c,r32(e,0x3f4d44));return 1;
    }
    default:break;
    }
    return 0;
}
void hg_pack_bootstrap(HGResourceEnv *e,uint32_t task) {
    while(hg_pack_bootstrap_step(e,task)==0)call(e,0x1434e0,0,0,0,0);
}

/* 0012fb30 clears handles 0x96..0x28e inclusive. The unrolled original loop
 * runs through 0x28d, then its return delay slot clears the final handle. */
void hg_resource_clear_dynamic_handles(HGResourceEnv *e) {
    for(uint32_t id=0x96;id<=0x28e;id++)w32(e,0x3f4300+id*4,0);
}
void hg_resource_clear_selected_handles(HGResourceEnv *e) {
    w32(e,0x3f45f8,0);w32(e,0x3f4c84,0);w32(e,0x3f4c88,0);
    w32(e,0x3f4c8c,0);w32(e,0x3f460c,0);
}

/* 00130900 entry through 00131080. The original chooses an arena before
 * entering its resumable loop. Clearing a handle does not free its data. */
void hg_pack_general_prepare(HGResourceEnv *e,uint32_t task) {
    uint32_t id=r32(e,task+0x44),dst,mode;
    switch(id) {
    case 0x6c:case 0x68:case 0x67:dst=r32(e,0x3f4d4c);mode=2;break;
    case 0x6a:case 0x54:case 0x4b:case 0x53:case 0x52:case 0x51:case 0x50:
        dst=0x1e00000;mode=1;break;
    case 0xf:case 0xe:case 0xd:case 0xc:case 0xb:dst=r32(e,0x3f4d58);mode=2;break;
    case 0x4f:case 0x4e:dst=r32(e,0x3f4d50);mode=2;break;
    case 3:case 0x69:case 0x62:case 0x61:case 0x60:case 0x5f:case 0x5e:case 0x5d:
    case 0x57:case 0x56:case 0x5c:case 0x5b:case 0x58:case 0x63:case 0x59:case 0x55:
    case 0x5a:case 0x66:case 0x65:case 0x64:case 0x12:
        dst=r32(e,0x3f4d5c);mode=1;break;
    case 0x76:case 0x75:case 0x74:case 0x73:case 0x72:case 0x71:case 0x70:case 0x6f:case 0x6e:
        dst=r32(e,0x3f4d44)+0x60000u;mode=3;break;
    case 0x91:case 0x90:case 0x8f:case 0x8e:case 0x8d:case 0x8c:case 0x8b:
    case 0x8a:case 0x89:case 0x88:case 0x87:case 0x86:case 0x85:
        dst=r32(e,0x3f4d44);mode=0x10;break;
    case 0x4d:case 0x4c:case 0x49:case 0x48:case 0x47:case 0x46:case 0x45:
    case 0x44:case 0x43:case 0x42:case 0x41:case 0x40:case 0x3f:
        dst=r32(e,0x3f4d48);mode=4;break;
    case 0x11:case 0x10:dst=r32(e,0x3f4d48);mode=2;break;
    case 0x4a:case 0x6b:dst=r32(e,0x3f4d44)+0x91000u;mode=5;break;
    case 0x110:case 0x10f:case 0x10e:case 0x10d:case 0x10c:case 0x10b:case 0x10a:
    case 0x109:case 0x108:case 0x107:case 0x106:case 0x105:case 0x104:case 0x103:case 0x102:
        hg_resource_clear_dynamic_handles(e);hg_resource_clear_selected_handles(e);
        dst=r32(e,0x3f4d50);mode=8;break;
    case 0xa:case 9:dst=r32(e,0x3f4d40);mode=6;break;
    case 6:case 5:dst=r32(e,0x3f4d50);mode=7;break;
    case 8:case 7: {
        uint32_t end,middle;
        w32(e,task+0x48,r32(e,0x3f4d40));
        dst=r32(e,task+0x48);end=dst+0x400000u;middle=end+0x2f8000u;
        uint32_t context=r32(e,0x700000c4);
        w32(e,0x3f4d48,end+0x91000u);w32(e,0x3f4d44,end);
        w32(e,0x3f4d4c,middle);w32(e,0x3f4d50,middle);
        if((context==1 && r32(e,0x3f4d5c)==0) || context==2) {
            w32(e,0x3f4d54,middle);w32(e,0x3f4d5c,end+0x348000u);
        }
        w16(e,task+0x3e,9);return;
    }
    case 1:hg_resource_clear_dynamic_handles(e); /* falls through */
    case 4:dst=r32(e,0x3f4d3c);mode=0;break;
    default:
        if((id>=0x13 && id<0x3f) || (id>=0x77 && id<0x85)) {
            uint32_t variant=r16(e,0x46e136),offset=task;
            /* Outside 0/1/2 the original retains the task argument in a0 and
             * does not store the allocation-mode halfword. */
            if(variant<3) {
                w16(e,task+0x3e,0x11u+variant);offset=0x30000u+variant*0x10000u;
            }
            w32(e,task+0x48,r32(e,0x3f4d44)+offset);return;
        }
        dst=r32(e,0x3f4d5c);mode=1;break;
    }
    w32(e,task+0x48,dst);w16(e,task+0x3e,mode);
}

/* 00131088..0013141c. Phase 0 falls directly into AREA phase 1 for mode 8;
 * all other pending cases return to SleepThread before another iteration. */
uint32_t hg_pack_general_step(HGResourceEnv *e,uint32_t task) {
    uint32_t header=0x3f3b00;
    switch(r8(e,task+0x39)) {
    case 0:
        if(r16(e,task+0x3e)!=8) {w8(e,task+0x39,2);break;}
        w8(e,task+0x39,1);w8(e,task+0x3a,0);
        /* falls through */
    case 1:
        if(hg_area_read_step(e,task)!=0) {
            w8(e,task+0x39,r8(e,task+0x39)+1);w8(e,task+0x3a,0);
        }break;
    case 2:
        read_setup(e,0x3ead20,header,r32(e,task+0x44)<<11,0x800);
        w8(e,task+0x39,r8(e,task+0x39)+1);break;
    case 3:case 6:
        if(read_poll(e)!=0)w8(e,task+0x39,r8(e,task+0x39)+1);
        break;
    case 4:
        if(r32(e,header+4)==0)return 1;
        if(hg_pack_special_step(e,task,header,header)!=0)w8(e,task+0x39,r8(e,task+0x39)+1);
        break;
    case 5:
        read_setup(e,0x3ead20,r32(e,task+0x48),r32(e,header+0x10),r32(e,header+0x14));
        w8(e,task+0x39,r8(e,task+0x39)+1);break;
    case 7: {
        uint32_t remaining=r32(e,header+0x20),i=0;
        if(r32(e,header+8)!=0)do {
            uint32_t cursor=r32(e,task+0x4c),id=r32(e,cursor+8);
            w32(e,0x3f4300+id*4,r32(e,task+0x48)+(r32(e,cursor)-r32(e,header+0x10)));
            if(remaining!=0) {
                remaining--;call(e,0x137ed0,r32(e,0x3f4300+id*4),0,id,0);
            }
            i++;w32(e,task+0x4c,r32(e,task+0x4c)+0x10);
        }while(i<r32(e,header+8));
        uint32_t mode=r16(e,task+0x3e);
        if(mode==0 || mode==6) {
            uint32_t end=align_sector(r32(e,task+0x48)+r32(e,header+0x14));
            /* Preserve store order as well as the original retained arenas. */
            w32(e,0x3f4d48,end+0x91000u);
            if(mode==0) {
                w32(e,0x3f4d5c,end+0x348000u);w32(e,0x3f4d3c,end);w32(e,0x3f4d40,end);
                w32(e,0x3f4d44,end);
            }else {
                w32(e,0x3f4d44,end);w32(e,0x3f4d5c,end+0x348000u);
            }
            w32(e,0x3f4d4c,end+0x2f8000u);w32(e,0x3f4d50,end+0x2f8000u);
            w32(e,0x3f4d54,end+0x2f8000u);
        }else if(mode==5)w32(e,0x3f4d48,align_sector(r32(e,0x3f4444)));
        else if(mode==7)w32(e,0x3f4d50,align_sector(r32(e,task+0x48)+r32(e,header+0x14)));
        else if(mode==8) {
            uint32_t end=align_sector(r32(e,task+0x48)+r32(e,header+0x14));
            w32(e,0x3f4d54,end);w32(e,0x3f4d5c,end+0x50000u);
        }
        return 1;
    }
    default:break;
    }
    return 0;
}
void hg_pack_general(HGResourceEnv *e,uint32_t task) {
    hg_pack_general_prepare(e,task);
    while(hg_pack_general_step(e,task)==0)call(e,0x1434e0,0,0,0,0);
}

/* 00131420's non-kind-2 tail. The empty and bulk cases both retain an
 * unrounded end. The exceptional state reserves two consecutive ranges;
 * storing an arena pointer is not an inferred allocation/free operation. */
static void alternative_tail(HGResourceEnv *e,uint32_t task,uint32_t bulk) {
    uint32_t state=r8(e,0x3fa1b0);
    uint32_t end=r32(e,task+0x48)+(bulk?r32(e,0x3f3b14):0);
    w32(e,0x3f4d54,end);
    if(state==0 && r8(e,0x3fa1b1)==2 && r8(e,0x3fa1b2)==1) {
        end=r32(e,task+0x48)+(bulk?r32(e,0x3f3b14):0);
        w32(e,0x3f4d58,end+0x50000u);
        w32(e,0x3f4d5c,r32(e,0x3f4d58)+0x200000u);
    }else {
        end=r32(e,task+0x48)+(bulk?r32(e,0x3f3b14):0);
        w32(e,0x3f4d5c,end+0x50000u);
    }
}
uint32_t hg_pack_alternative_step(HGResourceEnv *e,uint32_t task) {
    uint32_t header=0x3f3b00;
    switch(r8(e,task+0x39)) {
    case 0:
        if(r16(e,task+0x32)!=2) {w8(e,task+0x39,2);break;}
        w8(e,task+0x39,1);w8(e,task+0x3a,0);
        /* falls through */
    case 1:
        if(hg_area_read_step(e,task)!=0) {
            w8(e,task+0x39,r8(e,task+0x39)+1);w8(e,task+0x3a,0);
        }break;
    case 2:
        if(r16(e,task+0x32)==2) {
            hg_resource_clear_dynamic_handles(e);w32(e,task+0x48,r32(e,0x3f4d4c));
        }else {
            hg_resource_clear_selected_handles(e);w32(e,task+0x48,r32(e,0x3f4d50));
        }
        read_setup(e,0x3ead20,header,r32(e,task+0x44)<<11,0x800);
        w8(e,task+0x39,r8(e,task+0x39)+1);break;
    case 3:
        if(read_poll(e)!=0)w8(e,task+0x39,r8(e,task+0x39)+1);
        break;
    case 4:
        if(r32(e,header+4)==0) {
            if(r16(e,task+0x32)==2)w32(e,0x3f4d50,r32(e,task+0x48));
            else {alternative_tail(e,task,0);w8(e,0x3eace0,1);}
            return 1;
        }
        if(hg_pack_special_step(e,task,header,header)!=0)w8(e,task+0x39,r8(e,task+0x39)+1);
        break;
    case 5:
        read_setup(e,0x3ead20,r32(e,task+0x48),r32(e,header+0x10),r32(e,header+0x14));
        w8(e,task+0x39,r8(e,task+0x39)+1);break;
    case 6:
        if(read_poll(e)!=0) {
            if(r16(e,task+0x32)==3)w8(e,0x3eace0,1);
            w8(e,task+0x39,r8(e,task+0x39)+1);
        }break;
    case 7: {
        uint32_t remaining=r32(e,header+0x20),i=0;
        if(r32(e,header+8)!=0)do {
            uint32_t cursor=r32(e,task+0x4c),id=r32(e,cursor+8);
            w32(e,0x3f4300+id*4,r32(e,task+0x48)+(r32(e,cursor)-r32(e,header+0x10)));
            if(remaining!=0) {
                remaining--;call(e,0x137ed0,r32(e,0x3f4300+id*4),0,id,0);
            }
            i++;w32(e,task+0x4c,r32(e,task+0x4c)+0x10);
        }while(i<r32(e,header+8));
        if(r16(e,task+0x32)==2)
            w32(e,0x3f4d50,r32(e,task+0x48)+r32(e,header+0x14));
        else alternative_tail(e,task,1);
        return 1;
    }
    default:break;
    }
    return 0;
}
void hg_pack_alternative(HGResourceEnv *e,uint32_t task) {
    while(hg_pack_alternative_step(e,task)==0)call(e,0x1434e0,0,0,0,0);
}

uint32_t hg_area_index(HGResourceEnv *e) {
    return r32(e,0x3e01b0+r8(e,0x3fa1b0)*4)+r8(e,0x3fa1b1);
}
void hg_area_finalize(HGResourceEnv *e,uint32_t buffer,uint32_t size) {
    uint32_t zero_size=r32(e,buffer+0x14);
    call(e,0x1041e0,2,0,0,0);
    if(zero_size!=0)call(e,0x119ac8,buffer+size,0,zero_size,0);
}
uint32_t hg_area_read_step(HGResourceEnv *e,uint32_t task) {
    uint32_t phase=r8(e,task+0x3a);
    if(phase==2)return 1;
    if(phase==0) {
        uint32_t index=hg_area_index(e);
        read_setup(e,0x3f3a30+index*8,r32(e,0x3dff04),0,0xffffffff);
        w8(e,task+0x3a,r8(e,task+0x3a)+1);
    }else if(phase==1 && read_poll(e)!=0) {
        uint32_t index=hg_area_index(e);
        hg_area_finalize(e,r32(e,0x3dff04),r32(e,0x3f3a34+index*8));
        w8(e,task+0x3a,r8(e,task+0x3a)+1);
    }
    return 0;
}

/* A1_B00.BIN code at its observed fixed load address. An actor callback,
 * not a fabricated general AREA activation operation. The resident game
 * invokes this entry with an actor pointer in a0. */
void hg_area1_actor(HGResourceEnv *e,uint32_t actor,HGArea1Call6 setup) {
    uint32_t state=r8(e,actor+0x18),data=actor+0x2d0;
    switch(state) {
    case 0:
        w16(e,actor+0x2a,0x233);w32(e,actor+4,8);
        hg_actor_data_init(e,actor,data);w32(e,data,0x24);
        setup(e->user,0x18a330,actor,0x1000000,0x9000001,0,0,0);
        call(e,0x144340,actor+0x50,actor+0x30,actor+0x40,0);
        call(e,0x119ac8,data+0x30,0,0x10,0);
        w32(e,actor+0xa8,data+0x30);
        call(e,0x1235c0,actor+0x90,5,0,0);call(e,0x123660,actor+0x90,1,0,0);
        w32(e,actor+0xdc,0x3f800000);w32(e,data+0x38,0xbecccccd);
        w32(e,data+0x3c,0);w32(e,0x70000b9c,0x3f800000);
        w32(e,0x70000b90,0x41c00000);w32(e,0x70000b94,0x41c00000);
        w32(e,0x70000b98,0x41c00000);
        call(e,0x139c90,actor,0x70000b90,0,0);
        w8(e,actor+0x18,r8(e,actor+0x18)+1);break;
    case 1:
        call(e,0x119ac8,0x3eb058,0,0xc,0);
        w32(e,0x3eb058,actor);w32(e,0x3eb05c,r32(e,actor+0x2bc));
        call(e,0x18c280,1,0,0,0);break;
    case 2:w8(e,actor+0x18,3);break;
    case 3:call(e,0x142e90,actor,0,0,0);break;
    default:break;
    }
}
void hg_actor_data_init(HGResourceEnv *e,uint32_t actor,uint32_t data) {
    call(e,0x119ac8,data,0,0x30,0);
    w32(e,data+4,0x3f800000);w32(e,data+0x20,0x3f800000);
    w32(e,data+0x1c,0x3f800000);w32(e,actor+0x2bc,data);
}

/* 00142810: reload tail after actor stores; aliases can change it. */
void hg_actor_register_list(HGResourceEnv *e,uint32_t actor,uint32_t base,uint32_t priority) {
    uint32_t head=base+(r8(e,actor+0x29)==2 ? 0x61308u : 0x612f8u);
    if(priority!=0)head+=8;
    uint32_t tail=head+4;
    w32(e,actor+0x20,r32(e,tail));w32(e,actor+0x24,0);
    uint32_t previous=r32(e,tail);
    if(previous==0)w32(e,head,actor);
    else w32(e,previous+0x24,actor);
    w32(e,tail,actor);
}
void hg_actor_construct_short(HGResourceEnv *e,uint32_t actor,uint32_t kind,uint32_t callback,uint32_t priority) {
    hg_actor_register_list(e,actor,0x4d8150,priority);
    w8(e,actor,2);w8(e,actor+2,kind);w8(e,actor+0x28,priority);
    w32(e,actor+0x1c,callback);
}
void hg_actor_construct(HGResourceEnv *e,uint32_t actor,uint32_t kind,uint32_t callback,uint32_t priority) {
    hg_actor_construct_short(e,actor,kind,callback,priority);
    call(e,0x119ac8,actor+0x90,0,0x1c0,0);
    w32(e,actor+0x23c,0);w8(e,actor+0xad,0);w8(e,actor+0x245,0xff);
    w32(e,actor+0x24c,0x3e4ccccd);w8(e,actor+0x29c,0xff);
}
uint32_t hg_actor_allocate(HGResourceEnv *e,uint32_t pool,uint32_t kind,uint32_t callback) {
    uint32_t index=pool&0x7fu,priority=pool&0x80u;
    if(index>1)index=2;
    uint32_t head=0x539430+index*4,count=head+12;
    uint32_t actor=r32(e,head);
    if(actor==0)return 0;
    w32(e,count,r32(e,count)-1);w32(e,head,r32(e,actor+0x24));
    if(index==2)hg_actor_construct_short(e,actor,kind,callback,priority);
    else hg_actor_construct(e,actor,kind,callback,priority);
    return actor;
}
static void actor_update_list(HGResourceEnv *e,uint32_t head) {
    uint32_t actor=r32(e,head);
    w16(e,0x3eaed8,0);
    while(actor!=0) {
        uint32_t callback=r32(e,actor+0x1c),next=r32(e,actor+0x24);
        call(e,callback,actor,0,0,0);
        w16(e,0x3eaed8,r16(e,0x3eaed8)+1);
        actor=next;
    }
}
void hg_actor_update_main(HGResourceEnv *e) {
    actor_update_list(e,0x539448);w8(e,0x3f9772,0);
}
void hg_actor_update_priority(HGResourceEnv *e) { actor_update_list(e,0x539450); }
void hg_actor_update_type2(HGResourceEnv *e) { actor_update_list(e,0x539458); }
void hg_actor_update_type2_priority(HGResourceEnv *e) { actor_update_list(e,0x539460); }

/* 001385b0. Rebase header +08/+0c, call the discarded-result address permutation,
 * then update each
 * linked record's +34 address. No inferred format checks or bounds are added.
 * Header/callback alias effects and the two separate +34 stores are preserved. */
void hg_resource_rebase(HGResourceEnv *e,uint32_t block,uint32_t base) {
    uint32_t old_end=r32(e,block+0xc),old_base=r32(e,block+8);
    w32(e,block+0xc,base+(old_end-old_base));
    w32(e,block+8,base);
    uint32_t end=r32(e,block+0xc);
    if((end&31u)!=0)end=(end&0xffffffe0u)+32u;
    call(e,0x1389d0,end,0,0,0);
    for(;;) {
        w32(e,block+0x34,r32(e,block+0x34)&0xffffc000u);
        w32(e,block+0x34,r32(e,block+0x34)|base);
        uint32_t product=r32(e,block+0x50)*r32(e,block+0x54)*4u;
        /* Signed word divide by 256, truncating toward zero, without host
         * signed overflow or implementation-defined uint-to-int conversion. */
        uint32_t step=(product&0x80000000u) ? 0u-((0u-product)/256u) : product/256u;
        uint32_t tag=r32(e,block);
        base+=step;
        if((tag&0xf0000000u)==0x60000000u)return;
        block+=(tag&0xffffu)*16u+16u;
    }
}

/* FLK5 helpers use the low 24 bits of the index and encoded offset. */
uint32_t hg_flk5_entry(HGResourceEnv *e,uint32_t block,uint32_t index) {
    if(r32(e,block)!=0x354b4c46u)return block;
    return block+(r32(e,block+16+(index&0xffffffu)*4)&0xffffffu);
}
uint32_t hg_flk5_type(HGResourceEnv *e,uint32_t block,uint32_t index) {
    if(r32(e,block)!=0x354b4c46u)return 0xffff;
    return r32(e,block+16+(index&0xffffffu)*4)&0xff000000u;
}
uint32_t hg_flk5_count(HGResourceEnv *e,uint32_t block) {
    return r32(e,block)==0x354b4c46u?r32(e,block+4):0xffff;
}
uint32_t hg_flk5_size(HGResourceEnv *e,uint32_t block) {
    return r32(e,block)==0x354b4c46u?r32(e,block+8):0xffff;
}
void hg_flk5_disable(HGResourceEnv *e,uint32_t block,uint32_t index) {
    if(r32(e,block)==0x354b4c46u)w32(e,block+16+(index&0xffffffu)*4,0xff000000u);
}
uint32_t hg_resource_base_delta(HGResourceEnv *e,uint32_t block,uint32_t base) {
    return base-r32(e,block+8);
}
static int64_t signed_word(uint32_t v) {
    return (v&0x80000000u)?(int64_t)v-INT64_C(0x100000000):(int64_t)v;
}
static int positive_word(uint32_t v) { return v!=0 && (v&0x80000000u)==0; }

/* Selected finite, normal radius arithmetic. Each original multiply/add/root
 * rounds toward zero. Integer significands avoid host rounding and libm.
 * Exceptional FPU/VU flags and non-finite inputs are outside this path. */
static uint32_t radius_encode(uint64_t m,int exponent) {
    if(m==0)return 0;
    unsigned top=0;uint64_t copy=m;
    while((copy>>=1)!=0)top++;
    int field=exponent+(int)top+127;
    if(field<=0)return 0;
    if(field>=255)return 0x7f7fffffu;
    uint64_t fraction=top>23?m>>(top-23):m<<(23-top);
    return ((uint32_t)field<<23)|((uint32_t)fraction&0x7fffffu);
}
static uint32_t radius_square(uint32_t value) {
    uint32_t field=(value>>23)&255u;
    if(field==0)return 0;
    uint64_t m=(value&0x7fffffu)|0x800000u;
    return radius_encode(m*m,2*((int)field-150));
}
static uint32_t radius_add(uint32_t a,uint32_t b) {
    if(a<b) {uint32_t swap=a;a=b;b=swap;}
    if(b==0)return a;
    unsigned difference=(a>>23)-(b>>23);
    if(difference>31)return a;
    uint64_t sum=((uint64_t)((a&0x7fffffu)|0x800000u)<<31)
        +((uint64_t)((b&0x7fffffu)|0x800000u)<<(31-difference));
    return radius_encode(sum,(int)(a>>23)-181);
}
/* 00143530: f12 -> VU0 Q -> f0, adapted to raw bits. Q latency, flags and
 * untouched vector lanes are not outputs of this host API. */
uint32_t hg_resource_sqrt(uint32_t value) {
    uint32_t field=(value>>23)&255u;
    if(field==0)return 0;
    uint64_t m=(value&0x7fffffu)|0x800000u;
    int exponent=(int)field-150;
    if(exponent%2!=0) {m<<=1;exponent--;}
    uint64_t remainder=m<<26,root=0,bit=UINT64_C(1)<<62;
    while(bit>remainder)bit>>=2;
    while(bit!=0) {
        if(remainder>=root+bit) {remainder-=root+bit;root=(root>>1)+bit;}
        else root>>=1;
        bit>>=2;
    }
    return radius_encode(root,exponent/2-13);
}
/* 00138690: scan words until a material tag or terminal packet. */
uint32_t hg_resource_material_next(HGResourceEnv *e,uint32_t cursor) {
    for(;;cursor+=4) {
        uint32_t tag=r32(e,cursor);
        if((tag&0xe0000000u)==0x60000000u)return cursor+4;
        if((tag&0xff000000u)==0x13000000u)return 0;
    }
}
/* 00138a80: sign extend the wrapped word sums BEFORE the 64-bit ORs.
 * Do not remask to 14 bits: overflow can affect other descriptor fields. */
uint64_t hg_resource_descriptor_address(uint64_t descriptor,uint32_t delta) {
    uint32_t low=((uint32_t)descriptor&0x3fffu)+delta;
    uint32_t high=((uint32_t)(descriptor>>37)&0x3fffu)+delta;
    return (descriptor&UINT64_C(0xfff8001fffffc000))
        |(uint64_t)signed_word(low)|((uint64_t)signed_word(high)<<37);
}
static void material_address(HGResourceEnv *e,uint32_t address,uint32_t delta) {
    uint64_t value=r32(e,address);
    value|=(uint64_t)r32(e,address+4)<<32;
    value=hg_resource_descriptor_address(value&UINT64_C(0xffffffe7ffffffff),delta);
    w32(e,address,(uint32_t)value);w32(e,address+4,(uint32_t)(value>>32));
}
/* 001386e0. Radius uses max absolute X/Z bounds. Kinds 0/3/4 traverse tagged
 * descriptors; kind 2 uses fixed records. Header fields reload after writes.
 * Selected finite-normal FP effects are reconstructed; exceptional flags and
 * arbitrary aliases into omitted original stack-save slots are not claimed. */
void hg_resource_geometry(HGResourceEnv *e,uint32_t block,uint32_t delta) {
    uint32_t x=r32(e,block+0x14)&0x7fffffffu,z=r32(e,block+0x1c)&0x7fffffffu;
    uint32_t other=r32(e,block+0x24)&0x7fffffffu;
    if(other>=x)x=other;
    other=r32(e,block+0x2c)&0x7fffffffu;
    if(other>=z)z=other;
    w32(e,block+0x20,hg_resource_sqrt(radius_add(radius_square(x),radius_square(z))));
    uint32_t kind=r8(e,block+0x10),cursor=block+0x40;
    if(kind==2) {
        for(uint32_t i=0;positive_word(r32(e,block)) && i<r32(e,block);i++,cursor+=0x80)
            material_address(e,cursor+0xc,delta);
    }else if(kind==0 || kind==3 || kind==4) {
        while((cursor=hg_resource_material_next(e,cursor))!=0) {
            material_address(e,cursor,delta);
            uint32_t count=r32(e,cursor+8);
            kind=r8(e,block+0x10);
            cursor+=0x24+count*0x24u;
            if(kind==4)cursor+=8+r8(e,block+0x11)*count*16u;
            else if(kind==3)cursor+=8+r8(e,block+0x11)*count*12u;
        }
    }
}

/* 001389d0: signed quotient/remainder address permutation, not allocation.
 * frame is the original helper's adjusted sp. Negative inputs can read BEFORE
 * its 32-byte table: use the caller-provided guest scratch, never a host array.
 * The table is copied before lookup, preserving aliasing with the source. */
uint32_t hg_resource_map_address(HGResourceEnv *e,uint32_t address,uint32_t frame) {
    uint32_t table[8];
    for(uint32_t i=0;i<8;i++)table[i]=r32(e,0x3e0260+i*4);
    for(uint32_t i=0;i<8;i++)w32(e,frame+i*4,table[i]);
    int64_t value=signed_word(address),q=value/32,rem=value%32;
    int64_t group=rem/8+(q/4)*4,offset=rem%8+(q%4)*8;
    uint32_t byte=r8(e,frame+(uint32_t)offset);
    return (uint32_t)(group*32)+(byte>=128?byte-256u:byte);
}

/* 00137ed0. frame is guest scratch at the original adjusted sp, with at least
 * [frame,frame+0x240) available. Keeping original stack slots in the existing
 * bus preserves word wrap, table aliasing and malformed indices without host
 * array overflows. ABI register saves are interpreter concerns, not C locals.
 * This isolated seam retains geometry/upload calls; the composition adapter
 * resolves geometry. Upload 00152ae0 remains external. */
uint32_t hg_resource_fixup(HGResourceEnv *e,uint32_t block,uint32_t index,uint32_t id,uint32_t frame) {
    uint32_t textures=0,models=0,model_cursor=frame+0x140,texture_cursor=frame+0x100;
    w32(e,frame+0xfc,index);
    if(id==999999u) {
        if(r8(e,block+12)!=0)return 0;
        w8(e,block+12,1);
    }
    if(id==0x3d || id==0x7d || id==0x92) {
        hg_resource_rebase(e,block,id==0x3d?0x3f00u:id==0x7d?0x3b00u:0x3600u);
        call(e,0x152ae0,block,0,0,0);return block;
    }
    w32(e,frame+0xf8,hg_flk5_count(e,block));
    w32(e,frame+0xc0,id==0x7c?0x3b00u:(id==0x96 || id==0x22f)?0x1e00u:0x3200u);
    w32(e,frame+0xb0,id==0x7c?1u:0u);
    w32(e,frame+0xe0,0);w32(e,frame+0xd0,0);
    for(uint32_t i=0;i<64;i++)w32(e,frame+0x140+i*4,0);
    w32(e,frame+0xa0,frame+0x100);
    for(uint32_t i=0;i<16;i++)w32(e,frame+0x100+i*4,0);
    for(uint32_t i=0;positive_word(r32(e,frame+0xf8)) && i<r32(e,frame+0xf8);i++) {
        uint32_t entry=hg_flk5_entry(e,block,i),type=hg_flk5_type(e,block,i);
        if(type==0x01000000u) {
            w32(e,model_cursor,entry);model_cursor+=4;models++;
        }else if(type==0x09000000u && id!=999999u) {
            if(r32(e,frame+0xb0)==1)hg_flk5_disable(e,block,i);
            if(r32(e,frame+0xd0)==0)w32(e,frame+0xd0,entry);
            w32(e,texture_cursor,entry);textures++;texture_cursor+=4;
        }
    }
    model_cursor=frame+0x140;
    if(models!=0 || textures!=0) {
        if(textures!=0 && id!=999999u)
            w32(e,frame+0xe0,hg_resource_base_delta(e,r32(e,frame+0x100+(r32(e,frame+0xfc)<<2)),r32(e,frame+0xc0)));
        else w32(e,frame+0xe0,r32(e,frame+0xc0));
        for(uint32_t i=0;i<models;i++,model_cursor+=4) {
            uint32_t entry=r32(e,model_cursor);
            if(r32(e,entry)!=0xffffffffu) {
                call(e,0x1386e0,entry,r32(e,frame+0xe0),0,0);
                entry=r32(e,model_cursor);
                uint32_t link=r32(e,entry+0x30);
                if(link!=0)call(e,0x1386e0,entry+link,r32(e,frame+0xe0),0,0);
            }
        }
        for(uint32_t i=0;i<textures;i++) {
            hg_resource_rebase(e,r32(e,r32(e,frame+0xa0)),r32(e,frame+0xc0));
            w32(e,frame+0xa0,r32(e,frame+0xa0)+4);
        }
        if(r32(e,frame+0xb0)==1)
            call(e,0x152ae0,r32(e,frame+0x100+(r32(e,frame+0xfc)<<2)),0,0,0);
    }
    if(textures!=0 && id!=999999u) {
        if(r32(e,frame+0xd0)==0 || r32(e,frame+0xb0)==0)
            w32(e,frame+0xd0,block+hg_flk5_size(e,block));
        w32(e,block+8,r32(e,frame+0xe0));
        uint32_t end=r32(e,frame+0xd0);
        if((end&127u)!=0)w32(e,frame+0xd0,end+(128u-(end&127u)));
    }
    return r32(e,frame+0xd0);
}

/* Host-only composition adapter. The M2 boundary remains usable in isolation;
 * this entry resolves both general/alternative PACK/AREA loading and M4/M3 using the
 * SAME bus and disc environment.
 * Original fixup frame: caller sp-0x240; rebase/helper consume another 0x50.
 * Callers reserve scratch away from resource, loader and disc state. */
typedef struct { HGResourceEnv *outer; HGResourceEnv env; uint32_t frame; } HGProcessing;
static uint32_t process_load(void *user,uint32_t a,uint32_t n) {
    HGProcessing *p=user;return p->outer->load(p->outer->user,a,n);
}
static void process_store(void *user,uint32_t a,uint32_t n,uint32_t v) {
    HGProcessing *p=user;p->outer->store(p->outer->user,a,n,v);
}
static uint32_t process_call(void *user,uint32_t pc,uint32_t a,uint32_t b,uint32_t c,uint32_t d) {
    HGProcessing *p=user;
    if(pc==0x130900) {hg_pack_general(&p->env,a);return 0;}
    if(pc==0x131420) {hg_pack_alternative(&p->env,a);return 0;}
    if(pc==0x130020) {hg_resource_prepare_retained(&p->env,a);return 0;}
    if(pc==0x12fe90) {hg_resource_prepare_context(&p->env,a);return 0;}
    if(pc==0x12fcf0) {hg_resource_load_indexed(&p->env,a);return 0;}
    if(pc==0x12fde0) {hg_resource_load_context(&p->env,a);return 0;}
    if(pc==0x137ed0)return hg_resource_fixup(&p->env,a,b,c,p->frame);
    if(pc==0x1386e0) {hg_resource_geometry(&p->env,a,b);return 0;}
    if(pc==0x1389d0)return hg_resource_map_address(&p->env,a,p->frame-0x50);
    return p->outer->call(p->outer->user,pc,a,b,c,d);
}
void hg_resource_worker_fixed(HGResourceEnv *e,uint32_t frame) {
    HGProcessing p={e,{0},frame};p.env=*e;p.env.user=&p;
    p.env.load=process_load;p.env.store=process_store;p.env.call=process_call;
    hg_resource_worker(&p.env);
}

/* Host-only continuation for the measured pair at 00130368..00130378,
 * after the second alternative loader and its phase reset. Uses exactly the
 * worker's resolver, without replaying the preceding PACK/audio operation. */
void hg_resource_prepare_engine_fixed(HGResourceEnv *e,uint32_t task,uint32_t frame) {
    HGProcessing p={e,{0},frame};p.env=*e;p.env.user=&p;
    p.env.load=process_load;p.env.store=process_store;p.env.call=process_call;
    hg_resource_prepare_retained(&p.env,task);
    hg_resource_prepare_context(&p.env,task);
}
