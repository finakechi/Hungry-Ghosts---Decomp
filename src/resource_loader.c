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

void hg_actor_setup(HGResourceEnv *e,uint32_t actor,uint32_t model,uint32_t animation,
                    uint32_t attachment,uint32_t mode,uint32_t sequence) {
    hg_actor_setup_resources(e,actor,model,animation,attachment);
    hg_actor_setup_animation(e,actor,mode,sequence);
}
uint32_t hg_actor_resource_lookup(HGResourceEnv *e,uint32_t block,uint32_t id) {
    uint32_t count=call(e,0x138b70,block,0,0,0);
    if(count==0xffff)return 0;
    uint32_t type=id&0xff000000u,wanted=type;
    if(type==0x2000000 || type==0x3000000 || type==0x4000000 || type==0x5000000)wanted=0x1000000;
    uint32_t entry=call(e,0x138af0,block,id,0,0);
    if(call(e,0x138b30,block,id,0,0)!=wanted) {
        uint32_t i=0;
        if(count!=0 && count<0x80000000u) {
            for(;i<count;i++) {
                if(call(e,0x138b30,block,i,0,0)==wanted) {
                    entry=call(e,0x138af0,block,i,0,0);break;
                }
            }
        }
        if(i==count)return 0;
    }
    if(entry!=0) {
        uint32_t offset;
        switch(type) {
        case 0x2000000:offset=0x30;break;
        case 0x3000000:offset=0x34;break;
        case 0x4000000:offset=0x38;break;
        case 0x5000000:offset=0xc;break;
        default:return entry;
        }
        uint32_t relative=r32(e,entry+offset);
        entry=relative!=0?entry+relative:0;
    }
    return entry;
}
uint32_t hg_actor_allocate_nodes(HGResourceEnv *e,uint32_t object,uint32_t count) {
    if(count==0 || (count^0x80000000u)>(r32(e,0x4d8144)^0x80000000u))return 0;
    if(count<0x80000000u) {
        for(uint32_t i=0;i<count;i++) {
            uint32_t node=call(e,0x141de0,0,0,0,0);
            w32(e,object+(r32(e,object+0x54)+i)*4+0x60,node);
        }
    }
    return count;
}
uint32_t hg_actor_take_node(HGResourceEnv *e) {
    uint32_t top=r32(e,0x4d8140);
    w32(e,0x4d8144,r32(e,0x4d8144)-1);
    w32(e,0x4d8140,top-4);
    return r32(e,top-4);
}
/* Exact power-of-two scaling for the verified finite-normal/zero domain,
 * excluding overflow. No host float conversion or rounding is involved. */
static uint32_t actor_scale_128(uint32_t bits) {
    return (bits&0x7fffffffu)==0?0:bits+0x03800000u;
}
void hg_actor_bind_resources(HGResourceEnv *e,uint32_t actor,uint32_t model,uint32_t animation) {
    uint32_t object=actor+0x90;
    uint32_t count=call(e,0x141e40,object,r32(e,model+8),0,0);
    w32(e,object+0x58,object+r32(e,object+0x54)*4+0x60);
    w32(e,object+0x54,count);
    uint32_t offset=r32(e,model+0x38),record=model+offset;
    if(offset==0) {
        if(r8(e,actor+2)==4)call(e,0x120490,actor,0x3f92d0,0,0);
    }else if(r32(e,record)!=0 && r32(e,record+0x10)==1) {
        for(uint32_t i=0;i<3;i++)w32(e,actor+0x220+i*4,r32(e,record+0x20+i*4));
        for(uint32_t i=0;i<3;i++)w32(e,actor+0x22c+i*4,actor_scale_128(r32(e,record+0x30+i*4)));
        uint32_t value=r32(e,record+0x40);
        w32(e,actor+0x238,value);
        if((value&0x80000000u)!=0 || value<0x3dcccccdu)w32(e,actor+0x238,0x3dcccccd);
        else if(value>0x42480000u)w32(e,actor+0x238,0x42480000);
        w32(e,actor+0x23c,r8(e,actor+2)==2 && r32(e,actor+4)==2?0x40800000u:0x40000000u);
    }
    w32(e,object+0x10,model);w32(e,object+0x14,animation);
    for(uint32_t off=0x40;off<=0x4c;off+=4)w32(e,object+off,0x3f800000);
    w8(e,object+0xf,0);call(e,0x1330f0,object+0x20,1,0,0);
    for(uint32_t i=0;r32(e,model+8)<0x80000000u && i<r32(e,model+8);i++) {
        for(uint32_t off=0x40;off<=0x6c;off+=4) {
            uint32_t node=r32(e,r32(e,object+0x58)+i*4);
            w32(e,node+off,off==0x4c || off>=0x5c?0x3f800000u:0u);
        }
    }
}
void hg_actor_default_pose(HGResourceEnv *e,uint32_t object) {
    w8(e,object+0x39,r8(e,object+0x39)|1);
    uint32_t model=r32(e,object+0x10),record=model+r32(e,model+0xc);
    w32(e,object+0x20,0);w16(e,object+0x24,r32(e,model+8));
    uint32_t i=0;
    while(r16(e,object+0x24)<0x8000u && i<r16(e,object+0x24)) {
        call(e,0x143e60,r32(e,r32(e,object+0x58)+i*4)+0x80,record+0x10,0,0);
        if((r8(e,object+0x38)&1)!=0) {
            for(uint32_t off=0;off<12;off+=4) {
                uint32_t value=r32(e,record+0x40+off);
                w32(e,r32(e,r32(e,object+0x58)+i*4)+0x90+off,value);
            }
            for(uint32_t off=0x9c;off<=0xa4;off+=4)w32(e,r32(e,r32(e,object+0x58)+i*4)+off,0x3f800000);
            uint32_t value=r32(e,record+4);
            w32(e,r32(e,r32(e,object+0x58)+i*4)+0x70,value);
        }
        i++;record+=0x50;
    }
}
void hg_actor_bind_attachment(HGResourceEnv *e,uint32_t object,uint32_t resource) {
    w32(e,object+0x24,resource);
    if(resource==0)return;
    for(uint32_t off=0x10;off<=0x20;off+=4)w32(e,object+off,0);
    uint32_t buffer;
    if(r16(e,resource)!=0 && (buffer=call(e,0x266850,0,0,0,0))!=0) {
        w32(e,object+0x10,buffer);
        w32(e,object+0x14,buffer+r32(e,resource+0xc)-r32(e,resource+8));
        w32(e,object+0x18,buffer+r32(e,resource+0x14)-r32(e,resource+8));
        uint32_t begin=r32(e,resource+8),end=r32(e,resource+0x10);
        call(e,0x119910,buffer,resource+begin,end-begin,0);
        call(e,0x119910,object+0x28,resource+0x2c,12,0);
    }
    if(r16(e,resource+2)!=0 && (buffer=call(e,0x266a20,object,0,0,0))!=0) {
        w32(e,object+0x1c,buffer);
        call(e,0x119910,buffer,resource+r32(e,resource+0x18),r16(e,resource+2)*32,0);
        call(e,0x119910,object+0x34,resource+0x38,12,0);
    }
    if(r16(e,resource+4)!=0 && (buffer=call(e,0x266c40,object,0,0,0))!=0) {
        w32(e,object+0x20,buffer);
        call(e,0x119910,buffer,resource+r32(e,resource+0x1c),r16(e,resource+4)*80,0);
        call(e,0x119910,object+0x40,resource+0x44,12,0);
    }
}
void hg_actor_setup_resources(HGResourceEnv *e,uint32_t actor,uint32_t model,
                             uint32_t animation,uint32_t attachment) {
    uint32_t handle=r32(e,0x3f4300+r16(e,actor+0x2a)*4);
    uint32_t first=0,second=0,third=0;
    if(model)first=call(e,0x138370,handle,model,0,0);
    if(animation)second=call(e,0x138370,handle,animation,0,0);
    if(attachment)third=call(e,0x138370,handle,attachment,0,0);
    call(e,0x139d20,actor,first,second,0);
    call(e,0x1235c0,actor+0x90,0,0,0);
    call(e,0x267300,actor+0x250,third,0,0);
}
void hg_actor_setup_animation(HGResourceEnv *e,uint32_t actor,uint32_t mode,uint32_t sequence) {
    uint32_t handle=r32(e,0x3f4300+r16(e,actor+0x2a)*4);
    call(e,0x139fc0,actor+0x90,mode,0,0);
    if(sequence)call(e,0x139ff0,actor+0x90,handle,sequence,0);
    else call(e,0x13a0a0,actor+0x90,0,0,0);
}
void hg_actor_set_mode(HGResourceEnv *e,uint32_t object,uint32_t mode) {
    w8(e,object+0xc,mode);
}
void hg_actor_set_palette(HGResourceEnv *e,uint32_t object,uint32_t index) {
    uint32_t palette=r32(e,0x37b9a0+index*4);
    w32(e,object+0x50,palette);
    w32(e,object+0x40,0x3f800000);w32(e,object+0x44,0x3f800000);
    w32(e,object+0x48,0x3f800000);w32(e,object+0x4c,0x3f800000);
}
/* Compare raw finite-normal/zero single precision with 1.0, without host
 * floating-point rounding or aliasing. Exceptional PS2 FP behavior is outside
 * this entry's currently verified input domain. */
static int actor_less_than_one(uint32_t bits) {
    return (bits&0x80000000u)!=0 || bits<0x3f800000u;
}
/* Ordinary player update. Resource/animation, movement, collision and selected
 * transition bodies remain explicit original-address dependencies. */
/* Host-only stage boundary, original 159c44..159ce0. Entry requires the caller's
 * +4ad==0 and successful 15fe00 checks. Useful for original paused comparisons;
 * it is not another reconstructed retail function. */
void hg_player_ordinary_input_stage(HGResourceEnv *e,uint32_t a) {
    if(r8(e,0x3eaee8)!=0)w8(e,a+0x449,4);
    else {
        uint32_t edge=r16(e,0x3eaf04);
        if((edge&r16(e,0x3eaec8))!=0 || r8(e,0x3f9e3f)!=0) {
            uint32_t target=call(e,0x187e10,a,0,0,0);
            if(target!=0) {call(e,0x15b1e0,a,target,0,0);w8(e,a+0x44b,2);}
        } else if((edge&r16(e,0x3eaed0))!=0)w8(e,a+0x449,1);
    }
    if(r8(e,a+0x449)!=255)call(e,0x159380,a,0,0,0);
}
/* M9 correction: the delay slot at 00159b08 (li a1,1) always executes, so both
 * 0015fe00 calls receive a1=1 (movement allowed). M8 passed 0, which was
 * indistinguishable under neutral input but suppresses turning and walking. */
void hg_player_ordinary_update(HGResourceEnv *e,uint32_t a) {
    uint32_t status=call(e,0x13a050,0x3f800000,a+0x90,0,0);
    w32(e,a+0x428,status);
    uint32_t state=r8(e,a+0x19);
    if(state==2) {call(e,0x159d00,a,0,0,0);return;}
    if(state==0) {
        w8(e,a+0x44b,0);
        call(e,0x15e190,0,a,0x06000000,0);
        w8(e,a+0x19,r8(e,a+0x19)+1u);w8(e,a+0x1a,0);
        return;
    }
    if(state!=1)return;
    uint32_t substate=r8(e,a+0x1a);
    if(substate==0) {
        w8(e,a+0x464,0);
        if(r8(e,0x3eaee8)!=0) {
            w8(e,a+0x449,4);call(e,0x159380,a,0,0,0);
        } else if(r8(e,0x3eaeb0)==0) {
            if(call(e,0x15fe00,a,1,0,0)==0)w8(e,a+0x1a,r8(e,a+0x1a)+1u);
            else {w32(e,a+0x4cc,r32(e,a+0x18));w8(e,a+0x19,2);w8(e,a+0x1a,0);}
        }
        return;
    }
    if(substate!=1)return;
    uint32_t blocked=call(e,0x15fe00,a,1,0,0);
    if(blocked==0)call(e,0x161110,a,0,0,0);
    else {w32(e,a+0x4cc,r32(e,a+0x18));w8(e,a+0x19,2);w8(e,a+0x1a,0);}
    call(e,0x188650,a,0,0,0);call(e,0x15bf30,a,0,0,0);
    if(r8(e,a+0x4ad)!=0 || blocked!=0)return;
    hg_player_ordinary_input_stage(e,a);
}
/* The original keeps a0 live through these calls, despite omitted decompiler
 * arguments. No state-specific transition body is implied by this dispatcher. */
void hg_player_apply_transition(HGResourceEnv *e,uint32_t a) {
    w8(e,0x3f9f38,0);
    uint32_t state=r8(e,a+0x449),pc=0x158d90;
    if(state==4)pc=0x159350;
    else if(state==3)pc=0x158dc0;
    else if(state==2 || state==1)pc=0x158e20;
    call(e,pc,a,0,0,0);
}
void hg_player_reset_transition(HGResourceEnv *e,uint32_t a) {
    call(e,0x158ce0,a,0,0,0);
    w8(e,a+0x19,0);w8(e,a+0x1a,0);w8(e,a+0x1b,0);
    w8(e,a+0x44d,0);w8(e,a+0x4ac,0);w8(e,a+0x449,255);w8(e,a+0x44b,0);
    call(e,0x15e190,0,a,0x06000000,0);
}
uint32_t hg_player_input_transitions(HGResourceEnv *e,uint32_t actor) {
    if(r8(e,0x3fa067)!=0) {w8(e,0x3f9fee,0x10);return 0x80000000u;}
    for(uint32_t i=0;i<2;i++) {
        uint32_t input=i==0?0x3eaf1c:0x3eaf0c;
        uint32_t x=r32(e,input)&0x7fffffffu,y=r32(e,input+4)&0x7fffffffu;
        uint32_t magnitude=x>y?x:y;
        uint32_t timer=actor+0x4a8+i*2,phase=actor+0x4a4+i;
        uint32_t remaining=r16(e,timer);
        if(remaining==0) {
            w8(e,phase,0);
            if(i==0)w8(e,actor+0x4a6,0);
        }else w16(e,timer,remaining-1);
        uint32_t state=r8(e,phase);
        if(((state&1)==0 && magnitude>0x3f000000u) ||
           ((state&1)!=0 && magnitude<=0x3f000000u)) {
            w16(e,timer,0x28);w8(e,phase,r8(e,phase)+1);
        }
    }
    if((r16(e,0x3eaf04)&0xf0)!=0) {
        w16(e,actor+0x4a8,0x28);w8(e,actor+0x4a6,r8(e,actor+0x4a6)+1);
    }
    uint32_t total=r8(e,actor+0x4a4)+r8(e,actor+0x4a5)+r8(e,actor+0x4a6);
    uint32_t result=total>2?1u:0u;
    if(total>=r8(e,0x3f9fee)) {result|=0x80000000u;w8(e,0x3f9fee,0x10);}
    call(e,0x15e0c0,actor,0,0,0);
    return result;
}
void hg_actor_active(HGResourceEnv *e,uint32_t mode) {
    uint32_t actor=r32(e,0x3eb058);
    if(actor==0)return;
    uint32_t data=r32(e,0x3eb05c);
    if(data==0) {
        call(e,0x13a240,actor+0x90,actor+0x50,0,0);
        call(e,0x267450,actor,0,0,0);
        if(call(e,0x18c9c0,actor,0,0,0)==1)call(e,0x120110,actor,0,0,0);
        return;
    }
    uint32_t flags=r8(e,data+0x2b);
    if((flags&0x20)==0 && (r32(e,data)&0x800000)==0) {
        if(actor_less_than_one(r32(e,actor+0xdc)) || actor_less_than_one(r32(e,data+0x1c))) {
            if((r32(e,actor+0xe0)&0x1000000)!=0) {
                if((flags&0xf0)==0x10)call(e,0x1235f0,actor+0x90,1,0,0);
                else if((flags&0xf0)==0)call(e,0x123660,actor+0x90,1,0,0);
            }
        }else if((r32(e,actor+0xe0)&0x1000000)==0)call(e,0x123660,actor+0x90,0,0,0);
    }
    if(mode!=0 && mode!=1 && mode!=2)return;
    actor=r32(e,0x3eb058);
    uint32_t deferred=mode!=0 && (r32(e,actor+0xe0)&0x1000000)!=0;
    if(call(e,0x18b6c0,actor,r32(e,0x3eb05c),0,0)==0)return;
    if(mode==2)call(e,0x18c1c0,r32(e,0x3eb058),r32(e,0x3eb05c),0,0);
    else if(deferred) {
        uint32_t count=r32(e,0x3eb054);
        /* slti is signed, including the original malformed negative-index
         * behavior. All address arithmetic remains wrapped guest words. */
        if(count>=0x400 && count<0x80000000u)return;
        uint32_t slot=0x9e75e0+count*4;
        w32(e,slot,0x9e85e0+count*12);
        call(e,0x119910,r32(e,slot),0x3eb058,12,0);
        w32(e,0x3eb054,r32(e,0x3eb054)+1);
    }else call(e,0x18c120,r32(e,0x3eb058),r32(e,0x3eb05c),0,0);
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

static uint32_t actor_process_call(void *user,uint32_t pc,uint32_t a,uint32_t b,uint32_t c,uint32_t d) {
    HGProcessing *p=user;
    if(pc==0x138370)return hg_actor_resource_lookup(&p->env,a,b);
    if(pc==0x138b70)return hg_flk5_count(&p->env,a);
    if(pc==0x138af0)return hg_flk5_entry(&p->env,a,b);
    if(pc==0x138b30)return hg_flk5_type(&p->env,a,b);
    if(pc==0x139d20) {hg_actor_bind_resources(&p->env,a,b,c);return 0;}
    if(pc==0x141e40)return hg_actor_allocate_nodes(&p->env,a,b);
    if(pc==0x141de0)return hg_actor_take_node(&p->env);
    if(pc==0x267300) {hg_actor_bind_attachment(&p->env,a,b);return 0;}
    if(pc==0x13a0a0) {hg_actor_default_pose(&p->env,a);return 0;}
    if(pc==0x1235c0) {hg_actor_set_palette(&p->env,a,b);return 0;}
    if(pc==0x139fc0) {hg_actor_set_mode(&p->env,a,b);return 0;}
    if(pc==0x18c280) {hg_actor_active(&p->env,a);return 0;}
    return p->outer->call(p->outer->user,pc,a,b,c,d);
}
static uint32_t actor_process_setup(void *user,uint32_t pc,uint32_t a,uint32_t b,
                                    uint32_t c,uint32_t d,uint32_t mode,uint32_t sequence) {
    HGProcessing *p=user;
    (void)pc; /* Sole defined six-argument seam: resident 0018a330. */
    hg_actor_setup(&p->env,a,b,c,d,mode,sequence);return 0;
}
void hg_area1_actor_fixed(HGResourceEnv *e,uint32_t actor) {
    HGProcessing p={e,{0},0};p.env=*e;p.env.user=&p;
    p.env.load=process_load;p.env.store=process_store;p.env.call=actor_process_call;
    hg_area1_actor(&p.env,actor,actor_process_setup);
}
void hg_actor_setup_fixed(HGResourceEnv *e,uint32_t actor,uint32_t model,uint32_t animation,
                          uint32_t attachment,uint32_t mode,uint32_t sequence) {
    HGProcessing p={e,{0},0};p.env=*e;p.env.user=&p;
    p.env.load=process_load;p.env.store=process_store;p.env.call=actor_process_call;
    hg_actor_setup(&p.env,actor,model,animation,attachment,mode,sequence);
}

void hg_player_initialize(HGResourceEnv *e,uint32_t a) { /* 00158a10 */
    w16(e,a+0x2a,0x3f);
    uint32_t model=call(e,0x138370,r32(e,0x3f4300+r16(e,a+0x2a)*4),0x010000d3,0,0);
    uint32_t animation=call(e,0x138370,r32(e,0x3f4300+r16(e,a+0x2a)*4),0x090000d5,0,0);
    uint32_t attachment=call(e,0x138370,r32(e,0x3f4300+r16(e,a+0x2a)*4),0x030000d3,0,0);
    call(e,0x139d20,a,model,animation,0);
    call(e,0x139fc0,a+0x90,5,0,0);
    call(e,0x1235c0,a+0x90,2,0,0);
    call(e,0x267300,a+0x250,attachment,0,0);
    call(e,0x139ff0,a+0x90,r32(e,0x3f4300+r16(e,a+0x2a)*4),0x06000000,0);
    w16(e,a+0x2b2,r32(e,0x3fa1d0));w8(e,a+0x50c,0);
    if(r8(e,0x46e12d)==2 && r8(e,0x3fa202)!=255) {
        w8(e,0x3fa206,2);w8(e,a+0x47b,2);w8(e,a+0x463,r8(e,0x3fa202));
        w8(e,a+0x462,255);w8(e,a+0x4a3,255);
    } else if(r8(e,0x46e12d)==1) {
        w8(e,a+0x462,r8(e,0x3fa200));w8(e,a+0x4a3,r8(e,0x3fa201));
        w8(e,a+0x47b,255);w8(e,a+0x463,255);
    } else {
        w8(e,a+0x462,r8(e,0x3fa200));w8(e,a+0x4a3,r8(e,0x3fa201));
        w8(e,a+0x47b,255);w8(e,a+0x463,255);
        w8(e,0x46e12d,0);w8(e,0x46e12e,0);
    }
    w32(e,a+0x450,0);w32(e,a+0x454,0);w8(e,a,1);w8(e,a+0x44b,0);
    w16(e,a+0x45a,0);w16(e,a+0x4d8,0);w16(e,a+0x4f0,0);
    w8(e,a+1,0);w8(e,a+0x46e,0);w8(e,a+0x46f,0);
    uint32_t position[4];
    for(uint32_t i=0;i<4;i++)position[i]=r32(e,a+0x30+i*4);
    for(uint32_t i=0;i<4;i++)w32(e,a+0x390+i*4,position[i]);
    w16(e,a+0x47c,0);w16(e,a+0x47e,0);w8(e,a+0x480,0);w8(e,a+0x481,0);w8(e,a+0x483,0);
    w8(e,a+0x482,call(e,0x1b7f00,a+0x30,0,0,0));
    call(e,0x196ad0,0x31,0,0,0);call(e,0x196ad0,0xaa,0,0,0);
    w32(e,a+0x4e4,call(e,0x196ad0,0xe8,0,0,0));
    w8(e,a+0x4a2,0);call(e,0x144340,a+0x50,a+0x30,a+0x40,0);
    call(e,0x13a240,a+0x90,a+0x50,0,0);call(e,0x267450,a,0,0,0);
    call(e,0x13a2a0,a+0x90,a+0x350,2,0);call(e,0x139fe0,a+0x90,1,0,0);
    w8(e,a+0x449,r8(e,0x46e12d));w8(e,a+0x448,0);w8(e,a+0x44a,0);
    w8(e,a+0x45f,r8(e,0x46e12e));call(e,0x159380,a,0,0,0);
}
void hg_actor_set_animation_flag(HGResourceEnv *e,uint32_t object,uint32_t value) { /* 00139fe0 */
    w8(e,object+0x0d,value);
}
void hg_player_unlink_effects(HGResourceEnv *e,uint32_t a,uint32_t retain) { /* 00158ce0 */
    uint32_t effect=r32(e,a+0x450);
    if(effect!=0) {w8(e,effect+0x18,3);w32(e,a+0x450,0);}
    effect=r32(e,a+0x454);
    if(effect!=0) {w8(e,effect+0x18,3);w8(e,effect+0x19,0);w32(e,a+0x454,0);}
    w8(e,a+0x49e,0);w16(e,a+0x4d0,0);w8(e,a+0x4d3,0);
    if(retain==0)w8(e,a+0x46d,0);
}
/* Host seam: raw f12 bits precede original integer a0/a1. Outgoing 13a060
 * carries raw f12=0, raw f13=blend, then integer a0=object. Its original a1
 * carries sequence, preserved across this leaf. */
uint32_t hg_player_select_animation(HGResourceEnv *e,uint32_t blend,uint32_t a,uint32_t sequence) { /* 0015e190 */
    uint32_t current=r16(e,a+0xb6);
    if((current&0x8000u)!=0)current|=0xffff0000u;
    if(current==(sequence&0xffffu))return 0;
    call(e,0x13a060,0,blend,a+0x90,sequence);return 1;
}

/* One lq/sq pair per 16-byte block: retain the original overlap behavior. */
void hg_actor_copy_node_matrix(HGResourceEnv *e,uint32_t object,uint32_t output,uint32_t index) { /* 0013a2a0 */
    uint32_t input=r32(e,r32(e,object+0x58)+(index<<2));
    for(uint32_t block=0;block<4;block++) {
        uint32_t lane[4];
        for(uint32_t i=0;i<4;i++)lane[i]=r32(e,input+block*16+i*4);
        for(uint32_t i=0;i<4;i++)w32(e,output+block*16+i*4,lane[i]);
    }
}

/* 00159570. Float arguments on the address bus are raw bits before integer
 * arguments. 00120830 returns raw f0 bits through the same uint32_t seam.
 * Input scratch is an explicit guest-frame address, as in loader adapters. */
void hg_player_active_update(HGResourceEnv *e,uint32_t a,uint32_t frame) {
    if((r32(e,0x3eaef0)&0x3fu)==0)w8(e,a+0x482,call(e,0x1b7f00,a+0x30,0,0,0));
    call(e,0x15d770,a,0,0,0);
    uint32_t event=r8(e,0x3f9e3a);
    if((event&0x80u)==0 && r8(e,a+0x2c3)!=0 && event!=0 && r8(e,a+0x448)!=3) {
        w8(e,a,2);w8(e,a+0x18,2);w8(e,a+0x19,0);
        if(r8(e,0x3f9e3a)!=11)w8(e,0x3eaed4,0);
        return;
    }
    if(r8(e,0x3eaee8)!=0 || r16(e,a+0x45a)!=0 || r8(e,a+0x49f)!=0) {
        call(e,0x119910,frame+0x30,0x3eaf00,0x3c,0);
        call(e,0x119ac8,0x3eaf00,0,0x3c,0);
    }
    switch(r8(e,a+0x448)) {
    case 0:call(e,0x1594f0,0,0,0,0);call(e,0x159ad0,a,0,0,0);break;
    case 1:
        call(e,0x1594f0,0,0,0,0);w32(e,0x95e57c,1);
        if(r8(e,0x3fa200)==6)call(e,0x32b3c0,a,0,0,0);
        else if(r8(e,0x3fa200)<3)call(e,0x175190,a,0,0,0);
        else call(e,0x176fb0,a,0,0,0);
        break;
    case 2:call(e,0x1594f0,0,0,0,0);w32(e,0x95e57c,1);call(e,0x1bcd00,a,0,0,0);break;
    case 3:
        call(e,0x1e5060,a,0,0,0);
        if(r8(e,a+0x514)!=0)w8(e,a+0x4a2,r8(e,a+0x514));
        break;
    case 4:call(e,0x15a0d0,a,0,0,0);break;
    default:break;
    }
    if(r8(e,0x3eaee8)!=0 || r16(e,a+0x45a)!=0 || r8(e,a+0x49f)!=0) {
        call(e,0x119910,0x3eaf00,frame+0x30,0x3c,0);w8(e,a+0x464,0);
        uint32_t flags=r8(e,a+0x49f);
        if((flags&1)!=0) {
            w32(e,0x70000ba4,0);w32(e,0x70000ba0,0x42800000);
            w32(e,0x70000bac,0x42000000);w32(e,0x70000ba8,0);
            call(e,0x17fad0,0x70000ba0,0,0,0);w8(e,0x3f9f38,0);
            w16(e,a+0x45a,2);w8(e,a+1,0);
            if((call(e,0x15de80,a,0,0,0)&0x80000000u)!=0) {
                w8(e,a+0x49f,r8(e,a+0x49f)&0xfeu);
                if((r8(e,0x3eaed4)&6)==0) {
                    w16(e,a+0x45a,1);w8(e,a,r8(e,a)|4);
                    w16(e,a+0x4d8,r16(e,0x3e0738+r32(e,0x46d908)*2));
                } else {
                    w8(e,a+0x49f,r8(e,a+0x49f)|4);
                    call(e,0x15c410,0x3d567750,r32(e,a+0x44),a,0);
                    call(e,0x15c540,0,a,0x70000b80,0);
                }
            }
        } else if((flags&8)!=0) {
            if(r8(e,a+0x4fe)==0)w8(e,a+0x49f,flags&0xf7u);
            else w8(e,a+0x4fe,r8(e,a+0x4fe)-1u);
        }
        if((r8(e,a+0x49f)&4)!=0) {
            w8(e,0x3f9f38,0);
            uint32_t first=call(e,0x12c030,0x3ecccccd,0x3f9940,0x3f9860,3);
            uint32_t second=call(e,0x12c030,0x3ecccccd,0x3f9950,0x3f9870,3);
            if(first==7 && second==7) {
                w8(e,a+0x49f,r8(e,a+0x49f)&0xfbu);
                w8(e,0x3eaed4,r8(e,0x3eaed4)&0xf9u);w8(e,a,r8(e,a)|4);
                w16(e,a+0x4d8,r16(e,0x3e0738+r32(e,0x46d908)*2));w16(e,a+0x45a,1);
            } else w16(e,a+0x45a,2);
        }
        if((r8(e,a+0x49f)&2)!=0) {
            if(r16(e,a+0x414)==0) {
                w8(e,a+0x49f,r8(e,a+0x49f)&0xfdu);
                if(r8(e,a)!=2)w8(e,a,r8(e,a)&0xfdu);
            } else w16(e,a+0x414,r16(e,a+0x414)-1u);
        }
        if(r16(e,a+0x45a)!=0)w16(e,a+0x45a,r16(e,a+0x45a)-1u);
    }
    if(r16(e,a+0x4d8)!=0) {
        w16(e,a+0x4d8,r16(e,a+0x4d8)-1u);
        if(r16(e,a+0x4d8)==0)w8(e,a,r8(e,a)&0xfbu);
    }
    if(r16(e,a+0x4f0)!=0) {
        w16(e,a+0x4f0,r16(e,a+0x4f0)-1u);
        if(r16(e,a+0x4f0)==0 && r8(e,a)!=2)w8(e,a,r8(e,a)&0xfdu);
    }
    if(r16(e,a+0x416)!=0)w16(e,a+0x416,r16(e,a+0x416)-1u);
    w32(e,0x3f9f78,0);
    if(r8(e,a+0x464)!=0 && r32(e,a+0x420)!=0 && (r32(e,a+0x420)&0x80000000u)==0)
        w32(e,0x3f9f78,r32(e,a+0x420));
    w32(e,a+0x4f8,call(e,0x120830,a+0x30,a+0x390,0,0));
}

/* Selected state-0 execution of 00175190. This includes its common entry
 * callbacks and the observed next-update mutation. Other states are deliberately
 * left to the caller, not silently modeled as a no-op. */
uint32_t hg_player_equipment_enter(HGResourceEnv *e,uint32_t a) {
    if(r8(e,a+0x19)!=0)return 0;
    call(e,0x188550,0x41af3333,a,0,0);
    call(e,0x188650,a,0,0,0);call(e,0x15bf30,a,0,0,0);
    /* These callees must preserve state 0 for this selected split. */
    call(e,0x17d930,(r32(e,0x3eaef0)&4)!=0?0x43700000u:0u,
         r8(e,0x3fa200),r8(e,0x3fa201),0);
    w8(e,a+0x48e,0);w8(e,a+0x464,0);
    if(call(e,0x15fe00,a,1,0,0)!=0) {
        w32(e,a+0x4cc,r32(e,a+0x18));w8(e,a+0x19,11);w8(e,a+0x1a,0);
    } else if((r8(e,a+0x462)==r8(e,0x3fa200) && r8(e,a+0x4a3)==r8(e,0x3fa201)) || r8(e,0x3eacec)==0) {
        w8(e,a+0x462,r8(e,0x3fa200));w8(e,a+0x4a3,r8(e,0x3fa201));
        call(e,0x15c160,a,0,0,0);call(e,0x174900,a,0,0,0);w8(e,0x46e12f,0);
        call(e,0x15e190,0,a,0x0600007f,0);call(e,0x15e190,0x41200000,a,0x06000075,0);
        w8(e,a+0x19,1);
    }
    return 1;
}

/* 00158e20, finite sound-volume domain [0,1]. This host entry uses the
 * bounded chop model for MUL.S / CVT.W.S. For nonnegative values in this
 * range, floor(chop_float(127*v)) equals floor(127*v), computed below
 * directly from the original single-precision bits. Exceptional FP modes and
 * values outside this domain have not been verified. */
static uint32_t player_sound_volume_127(uint32_t bits) {
    uint32_t exponent=(bits>>23)&255u;
    if(exponent==0)return 0;
    uint32_t product=((bits&0x7fffffu)|0x800000u)*127u;
    uint32_t shift=150u-exponent;
    return shift<32u?product>>shift:0u;
}
void hg_player_equipment_transition(HGResourceEnv *e,uint32_t a) {
    if(r8(e,a+0x50c)!=0 && r8(e,a+0x448)==r8(e,a+0x449)) {
        w8(e,a+0x50c,0);
        uint32_t old=r8(e,a+0x448);
        if(old==2) {
            if(r16(e,0x46da32+r8(e,a+0x50b)*2u)!=0) {
                w8(e,a+0x45f,1);w8(e,0x3fa202,r8(e,a+0x50b));
            }
        } else if(old==1) {
            w8(e,a+0x45f,0);w8(e,0x3fa200,r8(e,a+0x509));
            w8(e,0x3fa201,r8(e,a+0x50a));
        }
    }
    if(r8(e,a+0x45f)!=0 && r8(e,0x3fa202)!=255) {
        w8(e,a+0x45f,0);call(e,0x159140,a,0,0,0);return;
    }
    w8(e,a+0x45f,1);
    uint32_t kind=r8(e,0x3fa200),mask=0,message=0;
    if(kind>=3 && kind<6) {mask=0x80;message=0x26070026;}
    else {
        uint32_t sub=r8(e,0x3fa201);
        if(sub==1) {mask=0x400;message=0x260a0026;}
        else if(sub==2) {mask=0x40;message=0x26060026;}
    }
    if(mask!=0 && call(e,0x15b260,mask,0,0,0)!=0) {
        call(e,0x1676f0,message,0,0,0);call(e,0x15b6b0,mask,2,0,0);
        /* The original-address host sound seam has four variable integer
         * slots; this call's fifth R5900 argument t0 is fixed at 1. A driver
         * resolving 156ad0 for this entry must supply that explicit constant. */
        call(e,0x156ad0,0x36,0x1000,player_sound_volume_127(r32(e,0x3eaa78)),0x40);
        call(e,0x158d90,a,0,0,0);return;
    }
    w8(e,a+0x448,1);w8(e,0x46e12d,1);w8(e,0x46e12e,0);
    w8(e,a+0x44a,r8(e,a+0x448));call(e,0x158d30,a,0,0,0);
    kind=r8(e,0x3fa200);
    if(kind<3 || kind>=6 || r8(e,0x46e12f)==1)w8(e,0x46e12f,0);
    if(r8(e,0x3fa200)!=r8(e,a+0x462) || r8(e,0x3fa201)!=r8(e,a+0x4a3))
        call(e,0x130150,4,0,0,0);
}
/* 001594f0: conditional countdown; read back after its store as in the EE. */
void hg_player_condition_timer(HGResourceEnv *e) {
    if((r8(e,0x3eaee8)&1u)!=0)return;
    uint32_t count=r32(e,0x46d90c);
    if((count&0x80000000u)!=0 || r8(e,0x3f9fcc)!=0 || r8(e,0x3f9f3c)!=0 || count==0)return;
    w32(e,0x46d90c,count-1u);
    if(r32(e,0x46d90c)==0)call(e,0x1cc5b0,0,0x70000b80,0x70000b80,0);
}
/* 0015b260. Only the low word is relevant to these two AND tests. */
uint32_t hg_player_flag_status(HGResourceEnv *e,uint32_t mask) {
    if((r32(e,0x46d894)&mask)==0)return 0;
    return (r32(e,0x3f9f08)&mask)!=0?2u:1u;
}
/* 0015c160: allocate and bind the equipment actor chosen by player fields. */
uint32_t hg_player_spawn_equipment(HGResourceEnv *e,uint32_t a) {
    uint32_t object=call(e,0x1429b0,0x80,1,0x16b0f0,0);
    if(object==0)return 0;
    uint32_t kind=r8(e,a+0x462);
    if(kind==6) {w32(e,object+4,5);w32(e,object+0x1c,0x173f50);}
    else if(kind<3) {w32(e,object+4,0);w32(e,object+0x1c,0x16b0f0);}
    else {w32(e,object+4,1);w32(e,object+0x1c,0x173900);}
    w32(e,object+8,r8(e,a+0x462));w32(e,object+0x10,r8(e,a+0x4a3));
    call(e,0x120490,object,0x3f92d0,0,0);
    for(uint32_t off=0x30;off<=0x4c;off+=4)w32(e,object+off,off==0x3c || off==0x4c?0x3f800000u:0);
    w32(e,a+0x450,object);return 1;
}

void hg_player_ordinary_transition(HGResourceEnv *e,uint32_t a) { /* 00158d90 */
    w8(e,a+0x448,0);w8(e,0x46e12d,0);w8(e,0x46e12e,r8(e,a+0x45f));
    w8(e,a+0x44a,r8(e,a+0x448));call(e,0x158d30,a,0,0,0);
}

/* Host composition of the observed player operation, sharing the established
 * resource/node/list bus. Engine/animation/math dependencies are forwarded to
 * the owner's bus. Only state 0 of 175190 is resolved here. */
static uint32_t player_process_call(void *user,uint32_t pc,uint32_t a,uint32_t b,uint32_t c,uint32_t d) {
    HGProcessing *p=user;
    switch(pc) {
    case 0x158a10:hg_player_initialize(&p->env,a);return 0;
    case 0x139fe0:hg_actor_set_animation_flag(&p->env,a,b);return 0;
    case 0x158ce0:hg_player_unlink_effects(&p->env,a,b);return 0;
    case 0x15e190:return hg_player_select_animation(&p->env,a,b,c);
    case 0x13a2a0:hg_actor_copy_node_matrix(&p->env,a,b,c);return 0;
    case 0x159570:hg_player_active_update(&p->env,a,p->frame);return 0;
    case 0x159ad0:hg_player_ordinary_update(&p->env,a);return 0;
    case 0x159380:hg_player_apply_transition(&p->env,a);return 0;
    case 0x158d30:hg_player_reset_transition(&p->env,a);return 0;
    case 0x158d90:hg_player_ordinary_transition(&p->env,a);return 0;
    case 0x158e20:hg_player_equipment_transition(&p->env,a);return 0;
    case 0x1594f0:hg_player_condition_timer(&p->env);return 0;
    case 0x15b260:return hg_player_flag_status(&p->env,a);
    case 0x15c160:return hg_player_spawn_equipment(&p->env,a);
    case 0x175190:
        if(hg_player_equipment_enter(&p->env,a)!=0)return 0;
        break;
    /* M9 movement/turn chain; FP operands are raw bits, first. */
    case 0x15fe00:return hg_player_movement_gate(&p->env,a,b);
    case 0x15fe70:return hg_player_movement(&p->env,a,b);
    case 0x1608b0:hg_player_walk(&p->env,a);return 0;
    case 0x160510:hg_player_turn(&p->env,a,b);return 0;
    case 0x160420:return hg_player_rate_approach(&p->env,a,b,c,d);
    case 0x15fcd0:return hg_player_speed_class(&p->env,a,b);
    case 0x160c20:return hg_player_move_limit(&p->env,a);
    case 0x1204e0:return hg_angle_wrap(&p->env,a);
    case 0x1429b0:return hg_actor_allocate(&p->env,a,b,c);
    case 0x1428f0:hg_actor_construct(&p->env,a,b,c,d);return 0;
    case 0x142970:hg_actor_construct_short(&p->env,a,b,c,d);return 0;
    case 0x142810:hg_actor_register_list(&p->env,a,b,c);return 0;
    default:break;
    }
    return actor_process_call(user,pc,a,b,c,d);
}
void hg_player_initialize_fixed(HGResourceEnv *e,uint32_t actor) {
    HGProcessing p={e,{0},0};p.env=*e;p.env.user=&p;
    p.env.load=process_load;p.env.store=process_store;p.env.call=player_process_call;
    hg_player_initialize(&p.env,actor);
}
void hg_player_active_update_fixed(HGResourceEnv *e,uint32_t actor,uint32_t frame) {
    HGProcessing p={e,{0},frame};p.env=*e;p.env.user=&p;
    p.env.load=process_load;p.env.store=process_store;p.env.call=player_process_call;
    hg_player_active_update(&p.env,actor,frame);
}
void hg_player_equipment_transition_fixed(HGResourceEnv *e,uint32_t actor) {
    HGProcessing p={e,{0},0};p.env=*e;p.env.user=&p;
    p.env.load=process_load;p.env.store=process_store;p.env.call=player_process_call;
    hg_player_equipment_transition(&p.env,actor);
}

/* ===================== M9: connected turning and camera response =====================
 * Original Japanese evidence: analysis/player_turn_camera/functions.json.
 *
 * Integer-exact single-precision subset shared by EE COP1 and VU0 macro code
 * here. It mirrors tools/ee_subset.py, the arithmetic oracle measured against
 * PCSX2 v2.8.2 default-settings retail captures: denormal operands read as zero,
 * results round toward zero (chop), denormal results flush to +0 (a zero
 * product keeps the operand sign XOR, M12) and overflow
 * saturates to +/-FLT_MAX. EE add/sub first clear the smaller operand's bits
 * below one alignment guard bit; VU adds are exact-then-chop. Non-finite
 * operands and the unobserved branches marked below are outside the verified
 * domain: they call HG_DOMAIN_FAULT, a host-only fail-closed bus entry that is
 * not an original address. No FP status flags or timing are modeled. */
#define HG_DOMAIN_FAULT 0xfffffff0u
static void domain_fault(HGResourceEnv *e,uint32_t code) { call(e,HG_DOMAIN_FAULT,code,0,0,0); }
static int fp_field(uint32_t b) { return (int)((b>>23)&255u); }
static uint32_t fp_encode(uint32_t sign,uint64_t m,int e2) {
    /* chop m*2^e2 to single precision */
    if(m==0)return 0;
    int msb=63;
    while(((m>>msb)&1u)==0)msb--;
    if(msb>23) {m>>=msb-23;e2+=msb-23;}
    else if(msb<23) {m<<=23-msb;e2-=23-msb;}
    int field=e2+150;
    if(field>=255)return sign|0x7f7fffffu;
    if(field<=0)return 0;
    return sign|((uint32_t)field<<23)|((uint32_t)m&0x7fffffu);
}
static uint64_t fp_mantissa(uint32_t b) { return fp_field(b)?((b&0x7fffffu)|0x800000u):0; }
static uint32_t fp_add_bits(uint32_t l,uint32_t r,int subtract,int ee_guard,uint32_t *fault) {
    if(fp_field(l)==255 || fp_field(r)==255) {*fault=1;return 0;}
    if(ee_guard) {
        int d=fp_field(l)-fp_field(r),s=d<0?-d:d;
        if(s!=0) {
            uint32_t mask=s>=25?0x80000000u:(0xffffffffu<<(s-1));
            if(d>0)r&=mask; else l&=mask;
        }
    }
    if(subtract)r^=0x80000000u;
    uint64_t ml=fp_mantissa(l),mr=fp_mantissa(r);
    if(ml==0 && mr==0)return 0;
    if(mr==0)return fp_encode(l&0x80000000u,ml,fp_field(l)-150);
    if(ml==0)return fp_encode(r&0x80000000u,mr,fp_field(r)-150);
    int el=fp_field(l),er=fp_field(r);
    uint32_t sb,ss;uint64_t mb,ms;int eb,es;
    if(el>er || (el==er && ml>=mr)) {sb=l&0x80000000u;mb=ml;eb=el;ss=r&0x80000000u;ms=mr;es=er;}
    else {sb=r&0x80000000u;mb=mr;eb=er;ss=l&0x80000000u;ms=ml;es=el;}
    int d=eb-es;uint64_t big=mb<<30,small,full=ms<<30;uint64_t sticky;
    if(d>=54) {small=0;sticky=1;}
    else {small=full>>d;sticky=(small<<d)!=full;}
    uint64_t m=sb==ss?big+small:big-small-sticky;
    return fp_encode(sb,m,eb-180);
}
/* M12: a zero (or flushed) product keeps the operand sign XOR, as EE mul.s
 * does under PCSX2 (retail 0012c640 stores +0 * negative as 0x80000000). */
static uint32_t fp_mul_bits(uint32_t l,uint32_t r,uint32_t *fault) {
    uint32_t sign=(l^r)&0x80000000u,v;
    if(fp_field(l)==255 || fp_field(r)==255) {*fault=1;return 0;}
    if(fp_field(l)==0 || fp_field(r)==0)return sign;
    v=fp_encode(sign,fp_mantissa(l)*fp_mantissa(r),fp_field(l)+fp_field(r)-300);
    return v!=0?v:sign;
}
static int64_t fp_key(uint32_t b) {
    int64_t m=fp_field(b)?(int64_t)(b&0x7fffffffu):0;
    return (b&0x80000000u)?-m:m;
}
/* M11. Normal-range division core: q = floor((ml << 40) / mr) lies in
 * [2^39, 2^41); returns the 24-bit truncated mantissa, its exponent (value =
 * mant * 2^e2) and the discarded fraction state for rounding. */
static uint64_t fp_quotient(uint64_t ml,uint64_t mr,int *e2,uint64_t *low,uint64_t *half,int *exact) {
    uint64_t num=ml<<40,q=num/mr;
    int shift=(q>>40)!=0?17:16;
    *low=q&((1ull<<shift)-1u);*half=1ull<<(shift-1);*exact=(num%mr)==0;
    *e2=shift-40;
    return q>>shift;
}
/* EE DIV.S under the PCSX2 v2.8.2 default profile: round to nearest even
 * (unlike the chopped add/mul), as fp_signed_nearest in tools/ee_subset.py.
 * A zero divisor or non-finite operand is outside the verified domain. */
static uint32_t fp_div_nearest_bits(uint32_t l,uint32_t r,uint32_t *fault) {
    if(fp_field(l)==255 || fp_field(r)==255 || fp_field(r)==0) {*fault=1;return 0;}
    if(fp_field(l)==0)return 0;
    uint64_t low,half;int e2,exact;
    uint64_t mant=fp_quotient(fp_mantissa(l),fp_mantissa(r),&e2,&low,&half,&exact);
    int field=e2+fp_field(l)-fp_field(r)+150;
    uint32_t sign=(l^r)&0x80000000u;
    if(field<=0)return 0;                 /* the oracle flushes before rounding */
    if(field>=255)return sign|0x7f7fffffu;
    if(low>half || (low==half && (!exact || (mant&1u)!=0))) {
        if(++mant==(1ull<<24)) {mant>>=1;field++;}
    }
    if(field>=255)return sign|0x7f7fffffu;
    return sign|((uint32_t)field<<23)|((uint32_t)mant&0x7fffffu);
}
static uint64_t isqrt64(uint64_t n) {
    uint64_t root=0,bit=1ull<<62;
    while(bit>n)bit>>=2;
    while(bit!=0) {
        if(n>=root+bit) {n-=root+bit;root=(root>>1)+bit;}
        else root>>=1;
        bit>>=2;
    }
    return root;
}
/* VU0 macro VRSQRT Q, fs.fsf, ft.ftf under PCSX2 defaults (Chop): the square
 * root of ft.ftf is chopped to single precision, then fs.fsf / root is
 * chopped. Zero, negative or non-finite radicands are outside the domain. */
static uint32_t vu_rsqrt_bits(uint32_t num,uint32_t rad,uint32_t *fault) {
    if(fp_field(num)==255 || fp_field(rad)==255 || fp_field(rad)==0 || (rad&0x80000000u)!=0) {*fault=1;return 0;}
    uint64_t m=fp_mantissa(rad);int e=fp_field(rad)-150;
    if((e&1)!=0) {m<<=1;e--;}
    uint64_t s=isqrt64(m<<38);            /* sqrt(rad) = s * 2^((e-38)/2), floored */
    int bits=0;
    while((s>>bits)!=0)bits++;
    uint64_t root=s>>(bits-24);
    int root_e2=(e-38)/2+(bits-24);       /* root value = root * 2^root_e2, normal */
    if(fp_field(num)==0)return 0;
    uint64_t low,half;int e2,exact;
    uint64_t mant=fp_quotient(fp_mantissa(num),root,&e2,&low,&half,&exact);
    int field=e2+(fp_field(num)-150)-root_e2+150;
    uint32_t sign=num&0x80000000u;
    if(field<=0)return 0;
    if(field>=255)return sign|0x7f7fffffu;
    return sign|((uint32_t)field<<23)|((uint32_t)mant&0x7fffffu);
}
/* Exported for synthetic differential tests: op 0 add.s, 1 sub.s, 2 mul.s,
 * 3 c.lt.s, 4 c.le.s, 5 c.eq.s, 6 VU add, 7 VU sub, 8 div.s (nearest),
 * 9 VU rsqrt (a / sqrt(b), chop). */
uint32_t hg_ee_fp(uint32_t op,uint32_t a,uint32_t b,uint32_t *fault) {
    *fault=0;
    switch(op) {
    case 0:case 1:return fp_add_bits(a,b,op==1,1,fault);
    case 2:return fp_mul_bits(a,b,fault);
    case 3:case 4:case 5:
        if(fp_field(a)==255 || fp_field(b)==255) {*fault=1;return 0;}
        if(op==3)return fp_key(a)<fp_key(b);
        if(op==4)return fp_key(a)<=fp_key(b);
        return fp_key(a)==fp_key(b);
    case 6:case 7:return fp_add_bits(a,b,op==7,0,fault);
    case 8:return fp_div_nearest_bits(a,b,fault);
    case 9:return vu_rsqrt_bits(a,b,fault);
    default:*fault=1;return 0;
    }
}
static uint32_t fp_op(HGResourceEnv *e,uint32_t op,uint32_t a,uint32_t b) {
    uint32_t fault,v=hg_ee_fp(op,a,b,&fault);
    if(fault)domain_fault(e,op+1);
    return v;
}
static uint32_t fadd(HGResourceEnv *e,uint32_t a,uint32_t b) { return fp_op(e,0,a,b); }
static uint32_t fsub(HGResourceEnv *e,uint32_t a,uint32_t b) { return fp_op(e,1,a,b); }
static uint32_t fmul(HGResourceEnv *e,uint32_t a,uint32_t b) { return fp_op(e,2,a,b); }
static int flt(HGResourceEnv *e,uint32_t a,uint32_t b) { return fp_op(e,3,a,b)!=0; }
static int fle(HGResourceEnv *e,uint32_t a,uint32_t b) { return fp_op(e,4,a,b)!=0; }
static int feq(HGResourceEnv *e,uint32_t a,uint32_t b) { return fp_op(e,5,a,b)!=0; }
static uint32_t fneg(uint32_t a) { return a^0x80000000u; }
static uint32_t fabs_ee(HGResourceEnv *e,uint32_t a) { return flt(e,a,0)?fneg(a):a; }
static void copy_quad(HGResourceEnv *e,uint32_t dst,uint32_t src) {
    uint32_t v[4];
    for(int i=0;i<4;i++)v[i]=r32(e,src+i*4);
    for(int i=0;i<4;i++)w32(e,dst+i*4,v[i]);
}
/* vmulax/vmadday/vmaddaz/vmaddw.xyzw then sqc2: out = M rows x vector lanes. */
static void vu_transform(HGResourceEnv *e,uint32_t matrix,uint32_t vector,uint32_t out) {
    uint32_t m[16],v[4],acc[4];
    for(int i=0;i<16;i++)m[i]=r32(e,matrix+i*4);
    for(int i=0;i<4;i++)v[i]=r32(e,vector+i*4);
    for(int lane=0;lane<4;lane++) {
        acc[lane]=fmul(e,m[lane],v[0]);
        acc[lane]=fp_op(e,6,acc[lane],fmul(e,m[4+lane],v[1]));
        acc[lane]=fp_op(e,6,acc[lane],fmul(e,m[8+lane],v[2]));
        acc[lane]=fp_op(e,6,acc[lane],fmul(e,m[12+lane],v[3]));
    }
    for(int lane=0;lane<4;lane++)w32(e,out+lane*4,acc[lane]);
}

/* 001204e0: wrap an angle into (-pi, pi] by repeated EE subtract/add of 2*pi. */
uint32_t hg_angle_wrap(HGResourceEnv *e,uint32_t angle) {
    const uint32_t pi=0x40490fdbu,minus_pi=0xc0490fdbu,two_pi=0x40c90fdbu;
    uint32_t limit=1u<<20;
    if(!fle(e,angle,pi))
        do {angle=fsub(e,angle,two_pi);if(--limit==0) {domain_fault(e,0x10);break;}} while(!fle(e,angle,pi));
    if(fle(e,angle,minus_pi))
        do {angle=fadd(e,angle,two_pi);if(--limit==0) {domain_fault(e,0x10);break;}} while(fle(e,angle,minus_pi));
    return angle;
}
/* 0015fcd0: speed class 1..3; a1 bit 4 selects class 2 instead of 3. */
uint32_t hg_player_speed_class(HGResourceEnv *e,uint32_t a,uint32_t mode) {
    if(r8(e,a+0x4ad)!=0 || r8(e,a+0x4d4)==0)return 1;
    if((int16_t)r16(e,a+0x416)<0x15)return 1;
    return (mode&4)!=0?2:3;
}
/* 00160c20: selected actor state 2 with global 3f9ff2 = 0x68 or 0x8e. */
uint32_t hg_player_move_limit(HGResourceEnv *e,uint32_t a) {
    if(r8(e,a+0x2c3)!=2)return 0;
    uint32_t v=r16(e,0x3f9ff2);
    return v==0x68 || v==0x8e;
}
/* 00160420: move a rate toward limit by step; an opposite-signed rate is braked
 * by step*brake. Both delay-slot products are evaluated as in the original. */
uint32_t hg_player_rate_approach(HGResourceEnv *e,uint32_t step,uint32_t limit,uint32_t brake,uint32_t field) {
    uint32_t current,value,braked;
    if(flt(e,step,0)) {
        current=r32(e,field);
        if(!fle(e,current,0)) {w32(e,field,fadd(e,current,fmul(e,step,brake)));return 0;}
        braked=fmul(e,step,brake);
        if(flt(e,current,limit)) {
            value=fsub(e,current,braked);w32(e,field,value);
            if(flt(e,value,limit))return 0;
        } else {
            value=fadd(e,current,step);w32(e,field,value);
            if(!fle(e,value,limit))return 0;
        }
        w32(e,field,limit);return 1;
    }
    current=r32(e,field);
    if(flt(e,current,0)) {w32(e,field,fadd(e,current,fmul(e,step,brake)));return 0;}
    braked=fmul(e,step,brake);
    if(!fle(e,current,limit)) {
        value=fsub(e,current,braked);w32(e,field,value);
        if(!fle(e,value,limit))return 0;
    } else {
        value=fadd(e,current,step);w32(e,field,value);
        if(flt(e,value,limit))return 0;
    }
    w32(e,field,limit);return 1;
}
/* Shared stick quantization at 0.4/0.6/0.8 of the absolute axis. */
static uint32_t stick_level(HGResourceEnv *e,uint32_t magnitude) {
    if(flt(e,magnitude,0x3ecccccdu))return 0;
    if(flt(e,magnitude,0x3f19999au))return 1;
    if(flt(e,magnitude,0x3f4ccccdu))return 2;
    return 3;
}
/* Decay a rate toward zero by step, clamping a sign crossing to +0. */
static void decay_rate(HGResourceEnv *e,uint32_t field,uint32_t step) {
    uint32_t value=r32(e,field);
    if(flt(e,value,0)) {
        value=fadd(e,value,step);w32(e,field,value);
        if(!fle(e,value,0))w32(e,field,0);
    } else {
        value=fsub(e,value,step);w32(e,field,value);
        if(flt(e,value,0))w32(e,field,0);
    }
}
/* 00160510: turn. Left-stick X (3eaf0c) selects table row 39a3f0+level*16;
 * the rate at +440 approaches its limit (negated for a negative stick) or
 * decays, then heading +44 becomes wrap(+44 - rate). */
void hg_player_turn(HGResourceEnv *e,uint32_t a,uint32_t mode) {
    uint32_t speed=call(e,0x15fcd0,a,mode,0,0);
    uint32_t level=stick_level(e,fabs_ee(e,r32(e,0x3eaf0c)));
    if(speed==3 && level!=0)level=3;
    w8(e,a+0x504,level);
    uint32_t scale=r32(e,0x3e0870+speed*4),row=0x39a3f0+level*16;
    uint32_t brake=fmul(e,r32(e,0x39a3f4),scale);
    uint32_t step=fmul(e,r32(e,row+8),scale),limit=fmul(e,r32(e,row+12),scale);
    if(level!=0) {
        if(flt(e,r32(e,0x3eaf0c),0)) {step=fneg(step);limit=fneg(limit);}
        call(e,0x160420,step,limit,brake,a+0x440);
    } else decay_rate(e,a+0x440,step);
    w32(e,a+0x44,call(e,0x1204e0,fsub(e,r32(e,a+0x44),r32(e,a+0x440)),0,0,0));
}
/* 001608b0: forward speed from left-stick Y (3eaf10) at +438, then turn,
 * then position +30/+38 advances by speed * sin/cos(heading). */
void hg_player_walk(HGResourceEnv *e,uint32_t a) {
    uint32_t axis=fabs_ee(e,r32(e,0x3eaf10));
    uint32_t speed=call(e,0x15fcd0,a,0,0,0),level=0;
    if(r8(e,a+0x4ad)==0) {
        if(call(e,0x160c20,a,0,0,0)!=0) {
            if(!flt(e,axis,0x3ecccccdu)) {level=1;speed=1;}
        } else if(call(e,0x15b260,1,0,0,0)!=0) {
            if(!flt(e,axis,0x3ecccccdu)) {level=1;if(speed==3)speed=2;}
        } else {
            level=stick_level(e,axis);
            if(speed==3 && level!=0)level=3;
        }
    }
    w8(e,a+0x45c,level);
    uint32_t scale=r32(e,0x3e0860+speed*4),row=0x39a3b0+level*16;
    uint32_t brake=fmul(e,r32(e,0x39a3b4),scale),step;
    if(speed==3)step=fmul(e,0x40000000u,fmul(e,r32(e,row+8),scale));
    else step=fmul(e,r32(e,row+8),scale);
    uint32_t limit=fmul(e,r32(e,row+12),scale);
    if(level!=0) {
        if(flt(e,r32(e,0x3eaf10),0)) {step=fneg(step);limit=fneg(limit);}
        call(e,0x160420,step,limit,brake,a+0x438);
    } else decay_rate(e,a+0x438,step);
    uint32_t side=fabs_ee(e,r32(e,a+0x434)),forward=fabs_ee(e,r32(e,a+0x438));
    uint32_t larger=fle(e,side,forward)?forward:side;
    call(e,0x160510,a,feq(e,0,larger)?4:1,0,0);
    uint32_t heading=r32(e,a+0x44);
    uint32_t s=call(e,0x143560,heading,0,0,0);
    w32(e,a+0x30,fadd(e,r32(e,a+0x30),fmul(e,r32(e,a+0x438),s)));
    heading=r32(e,a+0x44);
    uint32_t c=call(e,0x143620,heading,0,0,0);
    w32(e,a+0x38,fadd(e,r32(e,a+0x38),fmul(e,r32(e,a+0x438),c)));
}
/* 0015fe70: movement/look dispatcher. Mode bit 1 allows walking, bit 4 turns
 * without walking and bit 8 (or held 3eaf00 bit 4) selects look mode. */
uint32_t hg_player_movement(HGResourceEnv *e,uint32_t a,uint32_t mode) {
    uint32_t busy=0;
    w8(e,a+0x465,0);w16(e,a+0x49a,0);w8(e,a+0x505,0);w8(e,a+0x504,0);w8(e,a+0x45d,0);
    w8(e,a+0x45c,0);call(e,0x160ff0,a,0,0,0);
    if(r8(e,a+0x49f)!=0 || r8(e,a+0x449)==3 || r8(e,0x3eaeb0)!=0)return 0;
    if(r8(e,0x3f9f3c)!=0 && call(e,0x15b260,2,0,0,0)!=0)mode=4;
    if(r8(e,0x3eaee8)!=0)busy=1;
    else {
        if(r8(e,a+0x461)==0 && (r16(e,0x3eaf00)&4)==0 && (r16(e,0x3eaf04)&8)!=0) {
            w8(e,a+0x461,1);w32(e,a+0x43c,0);
        }
        if(r8(e,a+0x461)!=0)call(e,0x15fd30,a,0,0,0);
        if(r8(e,a+0x4ad)==0 && ((r16(e,0x3eaf00)&4)!=0 || (mode&8)!=0)) {
            w8(e,a+0x465,1);
            if(r8(e,a+0x461)==0)call(e,0x1606e0,a,mode|8,0,0);
            call(e,0x160510,a,mode|8,0,0);busy=1;
        } else {
            if((mode&4)!=0) {call(e,0x160510,a,mode|4,0,0);busy=1;}
            if(r8(e,a+0x461)==0) {
                decay_rate(e,a+0x43c,fmul(e,r32(e,0x39a3f0),r32(e,0x3e0874)));
                w32(e,a+0x3c0,call(e,0x1204e0,fadd(e,r32(e,a+0x3c0),r32(e,a+0x43c)),0,0,0));
            }
        }
    }
    uint32_t pitch=r32(e,a+0x3c0);
    if(flt(e,pitch,0xbf9a25c1u)) {w32(e,a+0x3c0,0xbf9a25c1u);w32(e,a+0x43c,0);}
    else if(!fle(e,pitch,0x3f9a25c1u)) {w32(e,a+0x3c0,0x3f9a25c1u);w32(e,a+0x43c,0);}
    if((mode&1)!=0 && busy==0) {call(e,0x160c60,a,0,0,0);call(e,0x1608b0,a,0,0,0);}
    else {w32(e,a+0x434,0);w32(e,a+0x438,0);}
    uint32_t first=r8(e,a+0x504),second=r8(e,a+0x505);
    uint32_t level=first<second?second:first;
    first=r8(e,a+0x45c);second=r8(e,a+0x45d);
    if(first<second)first=second;
    if(level<first)level=first;
    if(level==0 && (int16_t)r16(e,a+0x416)>=0x15 && r8(e,a+0x4d4)!=0) {
        w8(e,a+0x4d4,r8(e,a+0x4d4)-1u);
        if(r8(e,a+0x4d4)!=0)level=1;
    }
    if(r8(e,a+0x4ad)!=0) {w16(e,a+0x416,0);w8(e,a+0x4d4,0);}
    else if(level!=0 && (r16(e,0x3eaf00)&(r16(e,0x3eaecc)|8))!=0) {
        if(r8(e,a+0x4d4)==0) {
            int16_t timer=(int16_t)r16(e,a+0x416);
            if(timer>=0x15)w8(e,a+0x4d4,5);
            else if(timer==0) {w16(e,a+0x416,0x8c);w8(e,a+0x4d4,5);}
        }
    } else {
        int16_t timer=(int16_t)r16(e,a+0x416);
        if(timer>=0x15) {if(timer<0x78)w16(e,a+0x416,0x14);}
        else if(timer==0)w8(e,a+0x4d4,0);
    }
    uint32_t side=fabs_ee(e,r32(e,a+0x434)),forward=fabs_ee(e,r32(e,a+0x438));
    uint32_t larger=fle(e,side,forward)?forward:side;
    if(!flt(e,larger,0x3d872b02u)) {
        if(r8(e,a+0x506)>=0x15)w8(e,a+0x46e,0);
        w8(e,a+0x506,0);call(e,0x15aea0,a,1,0,0);
    } else if(feq(e,0,larger)) {
        w8(e,a+0x506,r8(e,a+0x506)+1u);
        if(r8(e,a+0x506)>=0x80)w8(e,a+0x506,0x80);
    }
    w32(e,0x70000b80,r32(e,a+0x390));w32(e,0x70000b84,0);
    w32(e,0x70000b88,r32(e,a+0x398));w32(e,0x70000b8c,0x3f800000u);
    w32(e,0x70000b90,r32(e,a+0x30));w32(e,0x70000b94,0);
    w32(e,0x70000b98,r32(e,a+0x38));w32(e,0x70000b9c,0x3f800000u);
    uint32_t heading;
    if(!flt(e,call(e,0x120830,0x70000b80,0x70000b90,0,0),0x3a83126fu))
        heading=call(e,0x120580,r32(e,0x70000b90),r32(e,0x70000b98),0x70000b80,0);
    else heading=r32(e,a+0x44);
    uint32_t result=call(e,0x15ebc0,heading,a,0,0);
    call(e,0x15fc10,a,0,0,0);
    return result;
}
/* 0015fe00: movement gate; returns 1 while +4ad is set for a selected +2c3. */
uint32_t hg_player_movement_gate(HGResourceEnv *e,uint32_t a,uint32_t mode) {
    call(e,0x15fe70,a,mode,0,0);
    if(r8(e,a+0x4ad)==0 || r8(e,a+0x2c3)==0)return 0;
    if(r8(e,a+0x4ad)==2) {w8(e,a+0x4ad,0);return 0;}
    return 1;
}
/* 0012bd00, observed +47a==0 branch: head matrix = identity rotated by pitch
 * +3c0, heading (+44, or wrap(+44 + +4dc) when +49f bit 1), roll +48; eye
 * from +380 (or the camera's own +e0); target = matrix x (0,0,20,1). */
uint32_t hg_camera_head_matrix(HGResourceEnv *e,uint32_t player,uint32_t camera,uint32_t keep_eye) {
    if(r8(e,player+0x47a)!=0) {domain_fault(e,0x20);return 0x70000500;}
    uint32_t heading=(r8(e,player+0x49f)&1)!=0?
        call(e,0x1204e0,fadd(e,r32(e,player+0x44),r32(e,player+0x4dc)),0,0,0):r32(e,player+0x44);
    for(uint32_t i=0;i<64;i+=16)copy_quad(e,0x70000500+i,0x3db2b0+i);
    call(e,0x143c60,r32(e,player+0x3c0),0x70000500,0x70000500,0);
    call(e,0x143d30,heading,0x70000500,0x70000500,0);
    call(e,0x143b90,r32(e,player+0x48),0x70000500,0x70000500,0);
    copy_quad(e,0x70000530,keep_eye==0?player+0x380:camera+0xe0);
    copy_quad(e,camera+0xe0,0x70000530);
    w32(e,0x70000700,0);w32(e,0x70000708,0x41a00000u);w32(e,0x7000070c,0x3f800000u);w32(e,0x70000704,0);
    vu_transform(e,0x70000500,0x70000700,camera+0xf0);
    return 0x70000500;
}
/* 0012d130: follow the head matrix; without shake (12cce0 == 0) the camera's
 * eye/target pair is replaced by the new +e0/+f0 before the look-at 12c3d0. */
void hg_camera_follow(HGResourceEnv *e,uint32_t player,uint32_t camera) {
    uint32_t matrix=call(e,0x12bd00,player,camera,r8(e,camera+0x140)!=0,0);
    call(e,0x12c640,camera,matrix,0,0);
    if(call(e,0x12cce0,camera,0,0,0)==0) {copy_quad(e,camera,camera+0xe0);copy_quad(e,camera+0x10,camera+0xf0);}
    call(e,0x12c3d0,camera,0,0,0);
}
/* 0012c790, observed 3eaed4==0 branch: player node refresh, camera follow,
 * eye at player position + 16.5 y, view rotation from camera +d0/+d4. */
void hg_camera_update(HGResourceEnv *e) {
    const uint32_t player=0x3f92d0,camera=0x3f9860;
    call(e,0x15e1d0,player,0,0,0);
    call(e,0x13a240,player+0x90,player+0x50,0,0);
    call(e,0x13a2a0,player+0x90,player+0x2d0,6,0);
    call(e,0x13a2a0,player+0x90,player+0x310,0x11,0);
    call(e,0x13a2a0,player+0x90,player+0x350,2,0);
    call(e,0x267450,player,0,0,0);
    if(r8(e,0x3eaed4)!=0) {domain_fault(e,0x21);return;}
    call(e,0x12d130,player,camera,0,0);
    w32(e,0x70000040,r32(e,player+0x30));
    w32(e,0x70000044,fadd(e,0x41840000u,r32(e,player+0x34)));
    w32(e,0x70000048,r32(e,player+0x38));
    for(uint32_t i=0;i<64;i+=16)copy_quad(e,0x70000000+i,0x3db2b0+i);
    call(e,0x143c60,r32(e,camera+0xd0),0x70000000,0x70000000,0);
    call(e,0x143d30,r32(e,camera+0xd4),0x70000000,0x70000000,0);
    copy_quad(e,0x70000030,camera);
    /* M15 ownership (original-instruction runs from every native camera-entry
     * reproduce camera-return exactly): 0012c790 itself writes only
     * 70000700..70f (0012bd00/0012c640), 700007b0..7bf (0015e1d0) and
     * 70000ba0..bab (0012c3d0), all by direct jal. 70000710..72f and
     * 70000bac..baf at camera return are pre-dispatch window outputs (0017f2d0
     * -> 00181510, stores 00181874..00181908; 00198f60 at 00199198), and
     * 700007c0..7ff, 70000900..94f come from the coordinator tail 00143150
     * (hg_render_node_list). No store to them belongs here. */
    w8(e,camera+0x140,0);
}
static uint32_t turn_camera_process_call(void *user,uint32_t pc,uint32_t a,uint32_t b,uint32_t c,uint32_t d) {
    HGProcessing *p=user;
    switch(pc) {
    case 0x12d130:hg_camera_follow(&p->env,a,b);return 0;
    case 0x12bd00:return hg_camera_head_matrix(&p->env,a,b,c);
    default:break;
    }
    return player_process_call(user,pc,a,b,c,d);
}
void hg_camera_update_fixed(HGResourceEnv *e) {
    HGProcessing p={e,{0},0};p.env=*e;p.env.user=&p;
    p.env.load=process_load;p.env.store=process_store;p.env.call=turn_camera_process_call;
    hg_camera_update(&p.env);
}

/* M10: 00158560, observed state-1 active path only. Other states fail closed.
 * lq/sq copy all four lanes, including position.w. +428 clears in the branch
 * delay slot after 159570, before the common tail and history copy. */
void hg_player_dispatch(HGResourceEnv *e) {
    const uint32_t a=0x3f92d0;
    w32(e,0x95e57c,0);
    copy_quad(e,0x700000e0,a+0x30);
    if(r8(e,a+0x18)!=1 || (r8(e,0x3eaee8)!=0 && r8(e,0x3f9f38)!=0)) {
        domain_fault(e,0x30);return;
    }
    w8(e,0x3f9f38,0);w8(e,a+1,1);
    call(e,0x159570,a,0,0,0);
    w32(e,a+0x428,0);
    w32(e,a+0x40,call(e,0x1204e0,fsub(e,r32(e,a+0x3c0),0x3d567750u),0,0,0));
    uint32_t sum=r8(e,0x3f9e3f)+r8(e,a+0x44b);
    sum+=(uint32_t)(int32_t)(int8_t)r8(e,a+0x4ac);
    sum+=r8(e,a+0x4ad);
    sum+=(uint32_t)(int32_t)(int16_t)r16(e,a+0x45a);
    sum+=r8(e,a+0x49f);sum+=r8(e,0x3fa067);
    if(r8(e,a+0x464)==0)sum++;
    w8(e,0x3f9f6e,sum!=0);
    copy_quad(e,a+0x390,a+0x30);
}
void hg_player_dispatch_fixed(HGResourceEnv *e,uint32_t frame) {
    HGProcessing p={e,{0},frame};p.env=*e;p.env.user=&p;
    p.env.load=process_load;p.env.store=process_store;p.env.call=player_process_call;
    hg_player_dispatch(&p.env);
}
/* Host composition of the two consecutive calls at 12f8c4/12f8cc. This does
 * not reconstruct the surrounding gameplay coordinator or its other calls. */
void hg_player_walk_camera_fixed(HGResourceEnv *e,uint32_t frame) {
    HGProcessing p={e,{0},frame};p.env=*e;p.env.user=&p;
    p.env.load=process_load;p.env.store=process_store;p.env.call=turn_camera_process_call;
    hg_player_dispatch(&p.env);
    hg_camera_update(&p.env);
}

/* M11: 0015ebc0, the static movement constraint called by 0015fe70 after the
 * walk proposes +30/+38 (original evidence: analysis/player_constraint/
 * functions.json). Four probe tiers query 001446a0 from the proposed position:
 * head (+16.5 y) and middle (+8.25 y) segments 4 units out at -90..+90 degrees
 * around the movement heading; foot (+3 y) segments from 2 units behind to 4
 * ahead; then 1 (or 3 with +4ad) rear probes. A qualifying normal-wall hit
 * whose normalized probe direction faces the hit normal (dot <= -0.3, or -0.7
 * for foot/rear probes with +4ad) adds the query's push vector 3f39b0/3f39b8
 * to X/Z and restarts that tier's probes from the corrected position.
 *
 * The scene query, sin/cos (00143560/00143620), RNG 0011ad58 and sound request
 * 00156ad0 remain original-address bus calls. Hits flagged 0x1e00 (special
 * surfaces: 0015eaf0 re-query and 3f3a20 object updates) were not observed and
 * fail closed. HG_GUEST_FRAME is a host-only seam, like HG_DOMAIN_FAULT: it
 * tells an instruction-modeling bus that callees run beneath this function's
 * original 0x70-byte guest frame. 0x145740 later copies uninitialized guest
 * stack into the 3f3a00 hit record, so the original layout is observable. */
#define HG_GUEST_FRAME 0xfffffff1u
static uint32_t vadd(HGResourceEnv *e,uint32_t a,uint32_t b) { return fp_op(e,6,a,b); }
static uint32_t vsub(HGResourceEnv *e,uint32_t a,uint32_t b) { return fp_op(e,7,a,b); }
static uint32_t sra32(uint32_t v,unsigned n) { return (v>>n)|((v&0x80000000u)!=0?~(0xffffffffu>>n):0u); }
/* lq/sq the position quad to the probe origin 70000b80, then y += height. */
static void constraint_origin(HGResourceEnv *e,uint32_t a,uint32_t height) {
    copy_quad(e,0x70000b80,a+0x30);
    w32(e,0x70000b84,fadd(e,r32(e,0x70000b84),height));
}
/* wrap(heading + degrees * pi / 180): mul.s, then div.s (nearest), add.s. */
static uint32_t constraint_angle(HGResourceEnv *e,uint32_t heading,uint32_t degrees) {
    uint32_t radians=fp_op(e,8,fmul(e,0x40490fdbu,degrees),0x43340000u);
    return call(e,0x1204e0,fadd(e,heading,radians),0,0,0);
}
/* point.x += scale * sin(angle); point.z += scale * cos(angle). */
static void constraint_offset(HGResourceEnv *e,uint32_t point,uint32_t angle,uint32_t scale) {
    uint32_t s=call(e,0x143560,angle,0,0,0);
    w32(e,point,fadd(e,r32(e,point),fmul(e,scale,s)));
    uint32_t c=call(e,0x143620,angle,0,0,0);
    w32(e,point+8,fadd(e,r32(e,point+8),fmul(e,scale,c)));
}
/* 1 = normal wall, 0 = ignored hit/miss, 2 = unobserved special surface. */
static uint32_t constraint_hit(HGResourceEnv *e,uint32_t a,uint32_t from) {
    uint32_t hit=call(e,0x1446a0,a,from,0x70000b90,0x207);
    if(hit==0)return 0;
    if((hit&0x100)!=0 && (r16(e,0x3f39c4)&2u)==0)return 0;
    if((hit&0x1e00)!=0) {domain_fault(e,0x40);return 2;}
    return 1;
}
/* VU0: c00 = (b90 - b80).xyz with w = b90.w; Q = 1/sqrt(|c00.xyz|^2);
 * c00 = (c00.xyz * Q, 0 + w); dot = c00.xyz . normal 3f3a10; c.le.s. */
static int constraint_facing(HGResourceEnv *e,uint32_t a,int foot) {
    uint32_t v[4],n[3];
    for(uint32_t i=0;i<3;i++)w32(e,0x70000c00+i*4,vsub(e,r32(e,0x70000b90+i*4),r32(e,0x70000b80+i*4)));
    w32(e,0x70000c0c,r32(e,0x70000b9c));
    for(uint32_t i=0;i<4;i++)v[i]=r32(e,0x70000c00+i*4);
    uint32_t length2=vadd(e,vadd(e,fmul(e,v[0],v[0]),fmul(e,v[1],v[1])),fmul(e,v[2],v[2]));
    uint32_t q=fp_op(e,9,0x3f800000u,length2),w_lane=vadd(e,0,v[3]);
    for(uint32_t i=0;i<3;i++)w32(e,0x70000c00+i*4,fmul(e,v[i],q));
    w32(e,0x70000c0c,w_lane);
    for(uint32_t i=0;i<3;i++) {v[i]=r32(e,0x70000c00+i*4);n[i]=r32(e,0x3f3a10+i*4);}
    uint32_t dot=vadd(e,vadd(e,fmul(e,v[0],n[0]),fmul(e,v[1],n[1])),fmul(e,v[2],n[2]));
    uint32_t threshold=foot && r8(e,a+0x4ad)!=0?0xbf333333u:0xbe99999au;
    return fle(e,dot,threshold);
}
/* Apply the push to X/Z only, then restart the tier origin from it. */
static void constraint_push(HGResourceEnv *e,uint32_t a,uint32_t height) {
    w32(e,a+0x30,fadd(e,r32(e,a+0x30),r32(e,0x3f39b0)));
    w32(e,a+0x38,fadd(e,r32(e,a+0x38),r32(e,0x3f39b8)));
    constraint_origin(e,a,height);
}
static uint32_t constraint_leave(HGResourceEnv *e,uint32_t result) {
    call(e,HG_GUEST_FRAME,0x70,0,0,0);
    return result;
}
uint32_t hg_player_static_constraint(HGResourceEnv *e,uint32_t heading,uint32_t a) {
    const uint32_t head=0x41840000U,middle=0x41040000u,foot=0x40400000u; /* 16.5, 8.25, 3.0 */
    const uint32_t minus_90=0xc2b40000u,step_45=0x42340000u;
    uint32_t result=0,hit;
    call(e,HG_GUEST_FRAME,0x70,1,0,0);
    constraint_origin(e,a,head);
    for(uint32_t i=0,degrees=minus_90;i<5;i++,degrees=fadd(e,degrees,step_45)) {
        uint32_t side=i==2?0u:2u,angle=constraint_angle(e,heading,degrees);
        copy_quad(e,0x70000b90,0x70000b80);
        constraint_offset(e,0x70000b90,angle,0x40800000u);
        if((hit=constraint_hit(e,a,0x70000b80))==2)return constraint_leave(e,0);
        if(hit!=0 && constraint_facing(e,a,0)) {constraint_push(e,a,head);result=side|1u;}
    }
    constraint_origin(e,a,middle);
    for(uint32_t i=0,degrees=minus_90;i<5;i++,degrees=fadd(e,degrees,step_45)) {
        copy_quad(e,0x70000b90,0x70000b80);
        uint32_t angle=constraint_angle(e,heading,degrees);
        constraint_offset(e,0x70000b90,angle,0x40800000u);
        if((hit=constraint_hit(e,a,0x70000b80))==2)return constraint_leave(e,0);
        if(hit!=0 && constraint_facing(e,a,0)) {constraint_push(e,a,middle);result|=0x30u;}
    }
    constraint_origin(e,a,foot);
    for(uint32_t i=0,degrees=minus_90;i<5;i++,degrees=fadd(e,degrees,step_45)) {
        copy_quad(e,0x70000b90,0x70000b80);
        uint32_t angle=constraint_angle(e,heading,degrees);
        constraint_offset(e,0x70000b90,angle,0x40800000u);
        copy_quad(e,0x70000c10,0x70000b80);
        constraint_offset(e,0x70000c10,angle,0xc0000000u);
        if((hit=constraint_hit(e,a,0x70000c10))==2)return constraint_leave(e,0);
        if(hit!=0 && constraint_facing(e,a,1)) {constraint_push(e,a,foot);result|=0x100u;}
    }
    constraint_origin(e,a,foot);
    uint32_t rear=r8(e,a+0x4ad)!=0?3u:1u;
    for(uint32_t i=0;i<rear;i++) {
        uint32_t degrees=i==0?0u:(i==1?step_45:0xc2340000u);
        copy_quad(e,0x70000b90,0x70000b80);
        copy_quad(e,0x70000c10,0x70000b80);
        uint32_t angle=constraint_angle(e,heading,degrees);
        constraint_offset(e,0x70000b90,angle,0xc0000000u);
        if(r8(e,a+0x4ad)!=0)constraint_offset(e,0x70000c10,angle,0x40000000u);
        if((hit=constraint_hit(e,a,0x70000c10))==2)return constraint_leave(e,0);
        if(hit!=0 && constraint_facing(e,a,1)) {constraint_push(e,a,foot);result|=0x100u;}
    }
    /* Bump: pushed while moving almost straight backward relative to the
     * facing heading, or by foot/rear probes only; +508 rate-limits it. */
    uint32_t turn=call(e,0x1204e0,fsub(e,r32(e,a+0x44),heading),0,0,0);
    if(flt(e,turn,0))turn=fneg(turn);
    int bump=(!flt(e,turn,0x403de44eu) && result!=0 && (result&0x22u)==0) || result==0x100u;
    if(bump && r8(e,a+0x508)==0 && (r16(e,a+0x49a)&0x100u)==0) {
        w8(e,a+0x508,0x50);
        if(r8(e,a+0x49c)!=0) {
            uint32_t random=call(e,0x11ad58,0,0,0,0),volume=r32(e,0x3eaa78);
            /* M8's volume conversion is verified for finite [0,1] only. */
            if((volume&0x80000000u)!=0 || volume>0x3f800000u) {domain_fault(e,0x41);return constraint_leave(e,0);}
            uint32_t offset=sra32(sra32(random,16)<<2,15)<<1;
            /* The bus has four argument slots; this call's fifth R5900
             * argument t0 is the constant 1 (delay slot 0015fbb8). */
            call(e,0x156ad0,r16(e,0x3e0850u+offset),0x1000,player_sound_volume_127(volume),0x40);
        }
    }
    if(r8(e,a+0x508)!=0)w8(e,a+0x508,r8(e,a+0x508)-1u);
    w16(e,a+0x49a,result);
    return constraint_leave(e,result);
}
/* M11 composition: M10's dispatcher/history and M9's camera, with 0015ebc0
 * resolved to C. The M10 composition above keeps its modeled constraint. */
static uint32_t constraint_process_call(void *user,uint32_t pc,uint32_t a,uint32_t b,uint32_t c,uint32_t d) {
    HGProcessing *p=user;
    if(pc==0x15ebc0)return hg_player_static_constraint(&p->env,a,b);
    return turn_camera_process_call(user,pc,a,b,c,d);
}
void hg_player_constraint_camera_fixed(HGResourceEnv *e,uint32_t frame) {
    HGProcessing p={e,{0},frame};p.env=*e;p.env.user=&p;
    HGResourceEnv *bus=&p.env;
    bus->load=process_load;bus->store=process_store;bus->call=constraint_process_call;
    hg_player_dispatch(bus);   /* walk, C 0015ebc0 response, history */
    hg_camera_update(bus);     /* same-frame camera on the corrected position */
}
/* Host entry for synthetic/original-instruction differentials of 0015ebc0. */
uint32_t hg_player_static_constraint_fixed(HGResourceEnv *e,uint32_t heading,uint32_t a) {
    HGProcessing p={e,{0},0};p.env=*e;p.env.user=&p;
    p.env.load=process_load;p.env.store=process_store;p.env.call=constraint_process_call;
    return hg_player_static_constraint(&p.env,heading,a);
}

/* M12: 0015fc10 grounded follow-up and the observed floor-hit path of its
 * floor routine 0015e420 (original evidence: analysis/player_grounded/
 * functions.json). 0015fe70 calls 0015fc10 directly after 0015ebc0, so the
 * gravity step starts from the constraint-corrected position.
 *
 * 0015e420 probes the floor through the original-address bus call 00145630
 * (f12 = 6.0, segment from y+4 down to y-2; 00144820/00145740/00267ed0 stay
 * modeled). Only ordinary floor hits are reconstructed. These paths fail
 * closed: no floor (airborne/ledge, 0x50), hazard types 6/7 (0x51), special
 * surfaces 0x1e00 (0x52), type-5 secondary probe (0x53) and the +47e == 3
 * effect-actor spawn (0x54). Both functions announce their original guest
 * frames (0x20 and 0x30) so the modeled floor query runs at its original
 * stack depth, as the M11 HG_GUEST_FRAME contract requires. */
uint32_t hg_player_floor(HGResourceEnv *e,uint32_t a) {
    call(e,HG_GUEST_FRAME,0x30,1,0,0);
    w8(e,a+0x483,r8(e,a+0x483)&0xfdu);
    w16(e,0x3f9ff2,0);
    copy_quad(e,0x70000b80,a+0x30);                      /* lq/sq, w included */
    w32(e,0x70000b84,fsub(e,r32(e,0x70000b84),0x40000000u)); /* y - 2.0 */
    uint32_t hit=call(e,0x145630,0x40c00000u,a,0x70000b80,0x2c0);
    uint32_t fault=0;
    if(hit==0)fault=0x50;
    else {
        uint32_t type=r16(e,0x3f39c6);
        if(type==7 || type==6)fault=0x51;
        else {
            if(r8(e,a)==2)w8(e,a,1);
            if((hit&0x1e00u)!=0)fault=0x52;
            else if((hit&0x100u)==0) {w32(e,a+0x4f4,0);w8(e,a+0x2c3,2);}
            else {
                w8(e,a+0x2c3,1);
                w16(e,a+0x47c,r16(e,a+0x47c)&0x8000u);
                w16(e,a+0x47c,r16(e,a+0x47c)|((1u<<(type&31u))&0xffffu)); /* sllv */
                w16(e,a+0x47e,r16(e,0x3f39c8));
                if(r16(e,a+0x47e)==0) {
                    w8(e,a+0x480,r16(e,0x3f39cc)&0xffu);
                    if(r8(e,a+0x480)==1 && (r32(e,0x46d88c)&1u)!=0)w8(e,a+0x483,r8(e,a+0x483)|2u);
                    w8(e,a+0x481,(r16(e,0x3f39cc)>>8)&0xffu);
                } else {w8(e,a+0x480,0);w8(e,a+0x481,0);}
                if(r16(e,0x3f39c6)==5)fault=0x53;
                else if(r16(e,a+0x47e)==3 && r8(e,0x3fa4d7)==0 && r8(e,0x3fa1b6)==0)fault=0x54;
            }
        }
    }
    if(fault!=0) {domain_fault(e,fault);call(e,HG_GUEST_FRAME,0x30,0,0,0);return 0;}
    copy_quad(e,a+0x30,0x3f39a0);                         /* snap to the floor hit */
    call(e,HG_GUEST_FRAME,0x30,0,0,0);
    return 1;
}
/* 0015fc10: vertical speed +430 -= 0.08 (0.03 while +4ad), floored at -2.0;
 * y += speed; the speed clears when 0015e420 reports a floor. */
void hg_player_grounded(HGResourceEnv *e,uint32_t a) {
    call(e,HG_GUEST_FRAME,0x20,1,0,0);
    uint32_t gravity=r8(e,a+0x4ad)==0?0x3da3d70au:0x3cf5c28fu;
    uint32_t speed=fsub(e,r32(e,a+0x430),gravity);
    w32(e,a+0x430,speed);
    if(flt(e,speed,0xc0000000u))w32(e,a+0x430,0xc0000000u);
    w32(e,a+0x34,fadd(e,r32(e,a+0x34),r32(e,a+0x430)));
    if(call(e,0x15e420,a,0,0,0)!=0)w32(e,a+0x430,0);
    call(e,HG_GUEST_FRAME,0x20,0,0,0);
}
/* M12 composition: M11's C constraint, then C grounded/floor, on the
 * unchanged M10 dispatcher/history and M9 camera. M9-M11 entry points keep
 * their modeled 0015fc10.
 *
 * HG_CALL_RETURN is a host-only observation seam, like HG_GUEST_FRAME: it
 * reports (callee entry, actual return value) of the C floor routine to the
 * outer bus so verification compares the value 0015fc10 receives with the
 * native v0. It has no guest effect. */
#define HG_CALL_RETURN 0xfffffff2u
static uint32_t grounded_process_call(void *user,uint32_t pc,uint32_t a,uint32_t b,uint32_t c,uint32_t d) {
    HGProcessing *p=user;
    if(pc==0x15fc10) {hg_player_grounded(&p->env,a);return 0;}
    if(pc==0x15e420) {
        uint32_t result=hg_player_floor(&p->env,a);
        p->outer->call(p->outer->user,HG_CALL_RETURN,pc,result,0,0);
        return result;
    }
    return constraint_process_call(user,pc,a,b,c,d);
}
void hg_player_grounded_camera_fixed(HGResourceEnv *e,uint32_t frame) {
    HGProcessing p={e,{0},frame};p.env=*e;p.env.user=&p;
    HGResourceEnv *bus=&p.env;
    bus->load=process_load;bus->store=process_store;bus->call=grounded_process_call;
    hg_player_dispatch(bus);   /* walk, C 0015ebc0, C 0015fc10/0015e420, history */
    hg_camera_update(bus);     /* same-frame camera on the floor-corrected position */
}
/* Host entry for synthetic/original-instruction differentials of 0015fc10. */
void hg_player_grounded_fixed(HGResourceEnv *e,uint32_t a) {
    HGProcessing p={e,{0},0};p.env=*e;p.env.user=&p;
    p.env.load=process_load;p.env.store=process_store;p.env.call=grounded_process_call;
    hg_player_grounded(&p.env,a);
}


/* M13: the observed per-frame engine advance that the original executes
 * between the end of one reconstructed frame and the next dispatcher entry.
 *
 * 0031bb80 (hg_frame_edge_sync): four per-frame edge counters are copied to
 * their previous slots and cleared (3fa040 -> 3fa050; 3f9e34/3f9e38/3f9e3c
 * +0x210 -> +0x220). Called by the coordinator 0012f850 every frame.
 *
 * hg_counter_advance: the observed 001379f0-region advance, guarded by the
 * byte 0x2f78(gp) = 0x3eaee8 (0 in all observed corridor frames): guard-clear
 * increments 46d930/46e110; either guard state continues to the threshold.
 *
 * hg_frame_advance: the observed prefix of the per-frame coordinator
 * 0012f850 (12f850..12f8a4): 3fa190 += 1 with the original's negative-wrap
 * reset (bgez guard), the edge sync, halfword 3f9ff8 = 0, and the guarded 46e118 += 1.
 * M14 connects 00198f60 in C and 0017f6d0/0017f2d0 in the instruction model.
 * Later coordinator calls remain HG_EXTERNAL_CALL seams. The observed frames run 0012f850
 * (not its sibling 0012f950): the selector byte 3f9f3e is 0.
 *
 * hg_player_frame_continuous: one continuous frame = coordinator prefix,
 * M10 dispatcher (walk/turn, C 0015ebc0, C 0015fc10/0015e420, history), the
 * M9 same-frame camera, the declared external coordinator tail, the
 * 001379f0 engine counter advance and the CPU-side render handoff, all on
 * one carried bus. Pad input and GS field timing are external inputs. */
#define HG_EXTERNAL_CALL 0xfffffff3u
static const uint32_t frame_guard=0x3eaee8u; /* byte 0x2f78(gp), gp = 0x3e7f70 */

/* 00198f60: bounded pre-dispatch sprite/packet service. The observed array
 * at 0039e960 contains twelve records and a negative sentinel. +0c is the
 * carried animation phase; +08 is its wrap limit, +10 its signed step.
 *
 * Packet stores follow the original widths exactly. A header writes only
 * bytes 0-1 (sh), 3 (sb) and 4-7 (sw); byte 2 and bytes 8-15 are never
 * written. A 0x40-byte quad packet then issues `sq $zero,0x10` (16 bytes),
 * `sw +1c` and four 64-bit `sd` stores at +20/+28/+30/+38 (0x1991c0..d4,
 * 0x1992a0..b4, 0x1993d0..e4). No store reaches +40, the next header. */
#define HG_CALL_T_REGS 0xfffffff5u /* host seam: t0,t1,t2 of the next call */
static uint32_t sprite_fp_int(int32_t value) {
    return value?fp_encode(0,(uint32_t)value,0):0;
}
static void sprite_sd(HGResourceEnv *e,uint32_t dst,uint64_t value) {
    w32(e,dst,(uint32_t)value);w32(e,dst+4,(uint32_t)(value>>32));
}
static void sprite_packet(HGResourceEnv *e,uint32_t ctx,uint32_t kind,uint32_t data) {
    uint32_t at=r32(e,ctx+0x550);
    w8(e,at+3,kind);w32(e,at+4,data);w16(e,at,3);
    w32(e,ctx+0x550,at+0x10);
}
static void sprite_quad_packet(HGResourceEnv *e,uint32_t ctx,uint64_t xy,uint64_t last) {
    uint32_t at=r32(e,ctx+0x550);
    w8(e,at+3,0x10);w32(e,at+4,0);w16(e,at,3);
    w32(e,ctx+0x550,at+0x40);
    for(uint32_t i=0;i<16;i+=4)w32(e,at+0x10+i,0);   /* sq $zero,0x10 */
    w32(e,at+0x1c,0x50000002);
    sprite_sd(e,at+0x20,0x1000000000008001ull);
    sprite_sd(e,at+0x28,14);
    sprite_sd(e,at+0x30,xy);
    sprite_sd(e,at+0x38,last);
}
void hg_pre_dispatch_service(HGResourceEnv *e) {
    uint32_t ctx=r32(e,0x3e7f70+0x3098),s=0x39e960;
    uint32_t packet=r32(e,ctx+0x550);
    call(e,0xfffffff6u,0x198f60,1,0,0);
    /* 00198f74/7c: f12 is 0x477fff00, a0 is the packet cursor. */
    call(e,0x155350,0x477fff00,packet,0,0);
    w32(e,0x70000b80,0);w32(e,0x70000b88,0x44000000);
    w32(e,0x70000b8c,0x42800000);w32(e,0x70000b84,0);
    call(e,0x153020,5,0x1d0,2,0x70000b80);
    ctx=r32(e,0x3e7f70+0x3098);
    call(e,0x152c50,5,0x1d0,2,(uint32_t)(int32_t)(int16_t)r16(e,ctx+0x5c0));
    for(unsigned record=0;record<=12;record++,s+=0x20) {
        int32_t index=(int8_t)r8(e,s);
        if(index<0) {
            if(record!=12) {domain_fault(e,0x61);return;}
            break;
        }
        if(record==12 || index>3 || r8(e,s+1)!=0 ||
           (r8(e,s+2)!=0 && r8(e,s+2)!=3 && r8(e,s+2)!=9)) {
            domain_fault(e,0x62);return;
        }
        int32_t rem=index%8,quot=index/8;
        uint32_t fr=sprite_fp_int(rem),fq=sprite_fp_int(quot);
        uint32_t f6=fmul(e,fr,0x42800000),f5=fmul(e,fq,0x42800000);
        uint32_t f4=fadd(e,0x44d80000,f6);
        uint32_t f2=fadd(e,0x44f20000,f5);
        uint32_t f0=fadd(e,f4,0x42800000);
        w32(e,0x70000b80,f4);w32(e,0x70000b88,f0);
        w32(e,0x70000b84,fsub(e,f2,r32(e,s+0x14)));
        uint32_t f1=fadd(e,f2,0x42800000);
        w32(e,0x70000b8c,fadd(e,r32(e,s+0x14),f1));
        w32(e,0x70000b90,0);
        w32(e,0x70000b94,r32(e,s+0xc));
        w32(e,0x70000b98,r32(e,s+8));
        w32(e,0x70000b9c,fadd(e,r32(e,s+8),r32(e,s+0xc)));
        uint32_t color=r32(e,s+4);
        w32(e,0x70000ba0,color);w32(e,0x70000ba4,color);
        w32(e,0x70000ba8,color);w32(e,0x70000bac,0x42fe0000);
        uint64_t xy=((uint64_t)(uint32_t)(quot*64+64)<<48) |
                    ((uint64_t)(uint32_t)(quot*64)<<32) |
                    ((uint64_t)(uint32_t)(rem*64+64)<<16) |
                    (uint32_t)(rem*64);
        ctx=r32(e,0x3e7f70+0x3098);
        sprite_quad_packet(e,ctx,xy,0x40);
        sprite_packet(e,ctx,0x30,0x381850+48u*(uint32_t)(int8_t)r8(e,s+3));
        call(e,0x14a0a0,5,1,(uint32_t)(int32_t)(int8_t)r8(e,s+2),0);
        ctx=r32(e,0x3e7f70+0x3098);
        sprite_quad_packet(e,ctx,r8(e,s+2)==0?1:0,0x4a);
        /* 0014f060 reads non-ABI t0/t1/t2 as well as a0..a3: t0 = 70000ba0
         * (0x199278), t2 = s0+18 (0x19927c), t1 = ctx+5b0 with ctx reloaded
         * after the packet stores (0x1992b8, delay slot 0x1992c0). */
        ctx=r32(e,0x3e7f70+0x3098);
        call(e,HG_CALL_T_REGS,0x70000ba0,ctx+0x5b0,s+0x18,0);
        call(e,0x14f060,5,1,0x70000b80,0x70000b90);
        uint32_t step=r32(e,s+0x10);
        if(r8(e,s+1)!=0)step=fmul(e,step,r32(e,0x3f9f30));
        uint32_t phase=fadd(e,r32(e,s+0xc),step);
        if(flt(e,phase,0))phase=fadd(e,phase,r32(e,s+8)); /* add.s f0,f1,f0 */
        w32(e,s+0xc,phase);
        if(!fle(e,phase,r32(e,s+8)))w32(e,s+0xc,fsub(e,phase,r32(e,s+8)));
    }
    ctx=r32(e,0x3e7f70+0x3098);
    sprite_packet(e,ctx,0x30,0x381910);
    sprite_quad_packet(e,ctx,0,0x4a);
    /* 0x1993e8..0x199410: a2 is the final 0x30 packet's address (cursor
     * before its increment) and a3 the context. a2/a3 are original
     * arguments even though 00152e90 overwrites them before reading. */
    ctx=r32(e,0x3e7f70+0x3098);
    packet=r32(e,ctx+0x550);
    sprite_packet(e,ctx,0x30,0x381880);
    call(e,0x152e90,5,0x381880,packet,ctx);
    call(e,0x14a0a0,5,0,0,0);
    ctx=r32(e,0x3e7f70+0x3098);
    packet=r32(e,ctx+0x550);
    w8(e,packet+3,0x60);w32(e,packet+4,0);w16(e,packet,0);
    w32(e,ctx+0x550,packet+0x10);
    call(e,0xfffffff6u,0x198f60,0,0,0);
}

/* M15: coordinator tail service 00143150 (0012f900) and the two observed
 * render nodes that produce the camera-relative screen-space scratch:
 * 0019c8a0 writes 70000900..94f (anchor sprite and its local-to-world row)
 * and 002f7830 writes 70000780..7ff (billboard/quad corner rows) and
 * 70000b80..bef. Both nodes run after the camera chain on its outputs: the
 * view matrix 70000000..3f (0012c790), the camera position/forward 003f9860/
 * 003f9890 and the camera pitch/yaw 003f9930/003f9934.
 *
 * VU lane products are exact-then-chop with a zero or underflowed product
 * stored as +0 (the instruction model's VU lanes); COP1 mul.s keeps the
 * sign of a zero product (fmul). Unobserved node states and variants fail
 * closed (domain fault 0x70..0x72). */
#define HG_CALL_WIDE_A3 0xfffffff4u /* host seam: full 64-bit a3 of the next call */
#define HG_SERVICE_MARK 0xfffffff6u
static uint32_t vmul(HGResourceEnv *e,uint32_t a,uint32_t b) {
    uint32_t fault=0,v=fp_mul_bits(a,b,&fault);
    if(fault)domain_fault(e,3);
    return (v&0x7fffffffu)==0?0:v;
}
static uint32_t fdiv(HGResourceEnv *e,uint32_t a,uint32_t b) { return fp_op(e,8,a,b); }
/* lqc2 vf,src; v<op>.<mask> vfd,vf,ft.x; sqc2 vfd,dst: unmasked lanes keep src. */
static void vq_scale(HGResourceEnv *e,uint32_t dst,uint32_t src,uint32_t s,unsigned mask) {
    uint32_t v[4];
    for(int i=0;i<4;i++)v[i]=r32(e,src+i*4);
    for(int i=0;i<4;i++)if(mask&(8u>>i))v[i]=vmul(e,v[i],s);
    for(int i=0;i<4;i++)w32(e,dst+i*4,v[i]);
}
static void vq_mul(HGResourceEnv *e,uint32_t dst,uint32_t a,uint32_t b) {
    uint32_t v[4];
    for(int i=0;i<4;i++)v[i]=vmul(e,r32(e,a+i*4),r32(e,b+i*4));
    for(int i=0;i<4;i++)w32(e,dst+i*4,v[i]);
}
static void vq_add(HGResourceEnv *e,uint32_t dst,uint32_t a,uint32_t b,unsigned mask) {
    uint32_t v[4];
    for(int i=0;i<4;i++) {
        v[i]=r32(e,a+i*4);
        if(mask&(8u>>i))v[i]=vadd(e,v[i],r32(e,b+i*4));
    }
    for(int i=0;i<4;i++)w32(e,dst+i*4,v[i]);
}
/* vmul.xyz, vaddy.x, vaddz.x, qmfc2: ((a.x*b.x)+(a.y*b.y))+(a.z*b.z). */
static uint32_t vdot3(HGResourceEnv *e,uint32_t a,uint32_t b) {
    uint32_t p[3];
    for(int i=0;i<3;i++)p[i]=vmul(e,r32(e,a+i*4),r32(e,b+i*4));
    return vadd(e,vadd(e,p[0],p[1]),p[2]);
}
static void identity_to(HGResourceEnv *e,uint32_t dst) {
    for(uint32_t i=0;i<64;i+=16)copy_quad(e,dst+i,0x3db2b0+i);
}
static void close_packet_stream(HGResourceEnv *e) {
    uint32_t slot=r32(e,0x3e7f70+0x3098)+0x550;
    w8(e,r32(e,slot)+3,0x60);w32(e,r32(e,slot)+4,0);w16(e,r32(e,slot),0);
    w32(e,slot,r32(e,slot)+0x10);
}
/* 0019c8a0 observed state 1 (node+18 == 1); sub-state node+19 0..2. */
static void anchor_draw(HGResourceEnv *e,uint32_t node) {
    const uint32_t s3=node+0x50;
    w32(e,0x70000b80,0);w32(e,0x70000b84,0xbfc00000u);   /* (0,-1.5,1.2,1) */
    w32(e,0x70000b88,0x3f99999au);w32(e,0x70000b8c,0x3f800000u);
    vu_transform(e,0x70000000,0x70000b80,node+0x30);    /* camera-chain view matrix */
    for(uint32_t l=0;l<3;l++) {                         /* drift along camera forward */
        uint32_t k=fmul(e,fmul(e,0x3cf5c28fu,r32(e,0x3f9890+4*l)),r32(e,s3+0x1c));
        w32(e,node+0x30+4*l,fadd(e,r32(e,node+0x30+4*l),k));
    }
    call(e,0x155350,0,r32(e,r32(e,0x3e7f70+0x3098)+0x550),0,0);
    w32(e,0x70000908,0);w32(e,0x70000904,0x3f800000u);
    identity_to(e,0x70000910);
    copy_quad(e,0x70000940,node+0x30);                  /* local-to-world row 3 */
    call(e,0x14a0a0,5,2,3,0);
    w32(e,0x70000900,r32(e,s3+0x10));
    w32(e,0x7000090c,r32(e,s3+0x40));
    call(e,0x1503f0,r32(e,s3+0x18),5,0x70000900,0x3a0490);
    w32(e,0x7000090c,r32(e,s3+0x44));
    call(e,0x1503f0,r32(e,s3+0x18),5,0x70000900,0x3a0500);
    call(e,0x14a0a0,5,0,0,0);
    close_packet_stream(e);
}
void hg_camera_anchor_node(HGResourceEnv *e,uint32_t node) {
    const uint32_t s3=node+0x50;
    call(e,HG_GUEST_FRAME,0x80,1,0,0);
    /* 0019c8d8..ec copy two gp constants to guest stack locals only. */
    if(r8(e,node+0x18)!=1) {domain_fault(e,0x70);call(e,HG_GUEST_FRAME,0x80,0,0,0);return;}
    uint32_t sub=r8(e,node+0x19);
    if(sub==0) {                                        /* 0019c988: new texture pair */
        w32(e,s3+0x10,0);w32(e,s3+0x1c,0);
        uint32_t r1=call(e,0x11ad58,0,0,0,0);
        w32(e,s3+0x40,sra32(sra32(r1,16)<<7,15));
        uint32_t r2=call(e,0x11ad58,0,0,0,0);
        w32(e,s3+0x44,sra32(sra32(r2,16)<<7,15));
        w8(e,node+0x19,r8(e,node+0x19)+1u);
        sub=1;                                          /* falls into 0019c9c8 */
    }
    if(sub==1) {                                        /* 0019c9c8: wait for release */
        if(r8(e,0x3f976c)!=0)w32(e,s3+0x10,0);
        else {
            int advance=1;
            if(r8(e,0x3f976d)<2) {
                uint32_t v=fadd(e,r32(e,s3+0x10),0x3c83126fu);
                w32(e,s3+0x10,v);
                advance=!flt(e,v,0x40800000u);
            }
            if(advance) {w8(e,node+0x19,r8(e,node+0x19)+1u);w32(e,s3+0x10,0);w32(e,s3+0x1c,0);}
        }
    } else if(sub==2) {                                 /* 0019ca38: active */
        uint32_t sum=r8(e,0x3e7f70+0x2f78)+r8(e,0x3fa067);
        sum+=r8(e,0x3e7f70+0x2f74);sum+=r8(e,0x3f976f);
        if(sum!=0 || r8(e,0x3f92d1)==0)w8(e,node+0x19,0);
        else {
            w32(e,s3+0x10,fadd(e,r32(e,s3+0x10),0x3c83126fu));
            w32(e,s3+0x1c,fadd(e,r32(e,s3+0x1c),0x3f4ccccdu));
            if(!fle(e,r32(e,s3+0x10),0x3fb33333u))w8(e,node+0x19,0);
            anchor_draw(e,node);
        }
    }
    if((r8(e,0x3f9753)&15u)!=0) {                       /* 0019ccc8 */
        if(r32(e,s3+0xb8)==0)w32(e,s3+0xb8,call(e,0x196ad0,0x2bf,0,0,0));
    } else {
        uint32_t other=r32(e,s3+0xb8);
        if(other!=0) {w8(e,other+0x18,2);w32(e,s3+0xb8,0);}
    }
    call(e,HG_GUEST_FRAME,0x80,0,0,0);
}
/* 002f7830 helpers: wrap one lane into [-64,64] (stored only when wrapped),
 * a rotated quad at the particle (70000780 matrix, 700007c0 corners from
 * 003c9c50) and its emission through 0014d4f0 with the 64-bit GIF tag in a3. */
static void particle_wrap(HGResourceEnv *e,uint32_t at) {
    for(uint32_t l=0;l<12;l+=4) {
        uint32_t v=r32(e,at+l);
        if(!fle(e,v,0x42800000u))w32(e,at+l,fsub(e,v,0x43000000u));
        else if(flt(e,v,0xc2800000u))w32(e,at+l,fadd(e,v,0x43000000u));
    }
}
static void particle_quad(HGResourceEnv *e,uint32_t pitch,uint32_t yaw,int with_yaw) {
    uint32_t row[12],t[4],p[4];
    identity_to(e,0x70000780);
    call(e,0x143c60,pitch,0x70000780,0x70000780,0);
    if(with_yaw)call(e,0x143d30,yaw,0x70000780,0x70000780,0);
    for(int i=0;i<4;i++) {p[i]=r32(e,0x70000b90+i*4);t[i]=r32(e,0x700007b0+i*4);}
    for(int i=0;i<12;i++)row[i]=r32(e,0x70000780+i*4);
    for(int i=0;i<3;i++)t[i]=vadd(e,t[i],p[i]);         /* vadd.xyz row3 += position */
    for(int i=0;i<12;i++)w32(e,0x70000780+i*4,row[i]);
    for(int i=0;i<4;i++)w32(e,0x700007b0+i*4,t[i]);
    for(uint32_t k=0;k<4;k++)vu_transform(e,0x70000780,0x3c9c50+16*k,0x700007c0+16*k);
}
static void particle_emit(HGResourceEnv *e) {
    call(e,HG_CALL_WIDE_A3,0xdd413200u,0x20064405u,0,0);
    call(e,0x14d4f0,5,0x700007c0,0x70000be0,0xdd413200u);
}
void hg_camera_particle_field(HGResourceEnv *e,uint32_t node) {
    const uint32_t s3=node+0x50,dirs=node+0x90,speeds=node+0xd4,layers=node+0x60;
    call(e,HG_GUEST_FRAME,0xc0,1,0,0);
    uint32_t variant=r32(e,node+8);
    if(r8(e,node+0x18)!=1 || variant!=0) {domain_fault(e,0x72);call(e,HG_GUEST_FRAME,0xc0,0,0,0);return;}
    for(uint32_t i=0;i<3;i++) {                         /* 002f7c28: layer directions */
        identity_to(e,0x700007c0);
        call(e,0x143c60,r32(e,node+0x40),0x700007c0,0x700007c0,0);
        call(e,0x143d30,r32(e,node+0x44),0x700007c0,0x700007c0,0);
        call(e,0x143b90,r32(e,node+0x48),0x700007c0,0x700007c0,0);
        w32(e,0x70000bc0,0);w32(e,0x70000bc8,0x3f800000u);
        w32(e,0x70000bcc,0x3f800000u);w32(e,0x70000bc4,0);
        vu_transform(e,0x700007c0,0x70000bc0,dirs+16*i);
    }
    for(int l=0;l<4;l++)                                /* 002f80c8: camera delta */
        w32(e,0x70000b80+l*4,vsub(e,r32(e,s3+l*4),r32(e,0x3f9860+l*4)));
    for(uint32_t i=0;i<3;i++) {                         /* 002f8168: scroll layers */
        vq_scale(e,0x70000bb0,dirs+16*i,r32(e,speeds+4*i),15);
        vq_add(e,0x70000b90,0x70000b80,0x70000bb0,15);
        vq_add(e,layers+16*i,0x70000b90,layers+16*i,15);
        particle_wrap(e,layers+16*i);
        w32(e,layers+16*i+12,0);
    }
    uint32_t ctx=r32(e,0x3e7f70+0x3098);
    call(e,0x155350,0,r32(e,ctx+0x550),0,0);
    uint32_t resource=call(e,0x138370,r32(e,0x3f457c),0x09000000u,0,0);
    call(e,0x152aa0,5,resource,0,0);
    uint32_t slot=r32(e,0x3e7f70+0x3098)+0x550;
    w8(e,r32(e,slot)+3,0x30);w32(e,r32(e,slot)+4,0x3e7f70-0x7890);
    w16(e,r32(e,slot),3);w32(e,slot,r32(e,slot)+0x10);
    call(e,0x14a0a0,5,1,3,0);
    vq_scale(e,0x70000bb0,0x3e57c0+16*variant,r32(e,node+0xe0),15);
    for(uint32_t group=0;group<4;group++) {
        copy_quad(e,0x70000bd0,0x3c9c90+16*group);
        for(uint32_t k=0;k<32;k++) {
            if(k>=16)vq_scale(e,0x70000ba0,0x3c9ad0+16*(k-16),0xc2800000u,14);
            else vq_scale(e,0x70000ba0,0x3c9ad0+16*k,0x42800000u,15);
            vq_mul(e,0x70000ba0,0x70000bd0,0x70000ba0);
            vq_add(e,0x70000b90,layers+16*(k%3),0x70000ba0,15);
            particle_wrap(e,0x70000b90);
            if(flt(e,vdot3(e,0x3f9890,0x70000b90),0x3e23d70au))continue;   /* behind camera */
            uint32_t dist=call(e,0x143530,vdot3(e,0x70000b90,0x70000b90),0,0,0);
            if(fle(e,dist,0x42080000u) || !flt(e,dist,0x42800000u))continue; /* 34 < d < 64 */
            uint32_t t=fdiv(e,fsub(e,dist,0x42080000u),0x41f00000u);
            uint32_t fade=call(e,0x143560,fmul(e,0x40490fdbu,t),0,0,0);
            if(!fle(e,fade,0x3f800000u))fade=0x3f800000u;
            else if(flt(e,fade,0))fade=0;
            vq_scale(e,0x70000ba0,0x70000bb0,fade,15);
            vq_add(e,0x70000b90,0x70000b90,0x3f9860,15);  /* camera-relative to world */
            w32(e,0x70000b9c,0x3f800000u);
            uint32_t pitch=r32(e,0x3f9930);
            uint32_t tilt=fdiv(e,flt(e,pitch,0)?fneg(pitch):pitch,0x3fa0d97cu);
            particle_quad(e,pitch,r32(e,0x3f9934),1);     /* camera-facing billboard */
            uint32_t facing=fsub(e,0x3f800000u,tilt);
            if(flt(e,facing,0))facing=0;
            vq_scale(e,0x70000be0,0x70000ba0,facing,15);
            particle_emit(e);
            particle_quad(e,0x3fc90fdbu,0,0);             /* horizontal quad */
            vq_scale(e,0x70000be0,0x70000ba0,tilt,15);
            particle_emit(e);
        }
    }
    call(e,0x14a0a0,5,0,0,0);
    close_packet_stream(e);
    copy_quad(e,s3,0x3f9860);                           /* camera position for next delta */
    call(e,HG_GUEST_FRAME,0xc0,0,0,0);
}
/* M15: coordinator service 0012bc70 (0012f8f0), the player model draw. It runs
 * after camera return and before 00143150; its light setup 00129020 and ground
 * shadow 00129e40 leave 70000700..71f (shadow colour, normalized model-space
 * light direction), 70000500..53f (shadow matrix) and 70000c80..d1f (light
 * matrices). Observed path only: model type 3 (+50 & ff), light mode 2.0
 * (+1ac), shadow mode 3 (+50 & 30000000), no point lights. Other paths fail
 * closed (0x74..0x79). 00125d60, 00128de0, 00126180, 00128e80, 00151c40 and
 * every leaf execute original instructions through the bus. HG_GUEST_FRAME
 * (bytes, 1) returns the guest sp of the entered frame: 00151c40 and 0014d4f0
 * receive pointers to the original's stack parameter blocks. */
static void model_light_setup(HGResourceEnv *e,uint32_t model,uint32_t lights,uint32_t attenuation) {
    uint32_t sp=call(e,HG_GUEST_FRAME,0x90,1,0,0);    /* 00129020 */
    const uint32_t light=r32(e,model+0x10);
    copy_quad(e,sp+0x60,model+0x40);
    uint32_t type=r32(e,model+0x50)&0xffu;
    if(type==0x40||type==0x20||type==0x0e||type==0x0d||type==0x02 ||
       !feq(e,r32(e,model+0x1ac),0x40000000u)) {domain_fault(e,0x77);call(e,HG_GUEST_FRAME,0x90,0,0,0);return;}
    w32(e,sp+0x70,model);w32(e,sp+0x7c,0x70000d00);w32(e,sp+0x74,lights);w32(e,sp+0x78,attenuation);
    w32(e,sp+0x80,r32(e,light+0x20));w32(e,sp+0x84,2);w32(e,sp+0x88,2);
    uint32_t count=call(e,0x151c40,sp+0x70,0,0,0);
    for(uint32_t l=0;l<12;l+=4)w32(e,0x70000700+l,r32(e,model+0x190+l));
    w32(e,0x7000070c,0);
    vu_transform(e,r32(e,0x3e7f70+0x2a40)+0x50,0x70000700,0x70000700);  /* to model space */
    /* 00129348: 70000710.xyz = v * rsqrt(v.v), .w = 0 + v.w */
    uint32_t v[4],q;
    for(int i=0;i<4;i++)v[i]=r32(e,0x70000700+4*i);
    q=fp_op(e,9,0x3f800000u,vdot3(e,0x70000700,0x70000700));
    uint32_t w=vadd(e,0,v[3]);
    for(int i=0;i<3;i++)w32(e,0x70000710+4*i,vmul(e,v[i],q));
    w32(e,0x7000071c,w);
    w32(e,lights+0x08,r32(e,0x70000710));w32(e,lights+0x18,r32(e,0x70000714));
    w32(e,lights+0x28,r32(e,0x70000718));w32(e,lights+0x38,0);
    uint32_t dist=call(e,0x143530,vdot3(e,0x70000700,0x70000700),0,0,0);
    uint32_t range=fadd(e,r32(e,model+0x1a8),r32(e,light+0x20));
    if(!flt(e,dist,range) || (r32(e,model+0x50)&0x00200000u)==0 || count!=0) {
        domain_fault(e,0x78);call(e,HG_GUEST_FRAME,0x90,0,0,0);return;
    }
    uint32_t fade=fsub(e,0x3f800000u,fdiv(e,dist,range));
    for(uint32_t l=0;l<12;l+=4)
        w32(e,attenuation+0x20+l,fmul(e,fade,fmul(e,r32(e,model+0x19c+l),r32(e,model+0x40+l))));
    w32(e,attenuation+0x2c,0);
    copy_quad(e,attenuation+0x30,r32(e,0x3e7f70+0x3080)+0x10);
    w32(e,attenuation+0x3c,fadd(e,0x4b000000u,fmul(e,0x43000000u,r32(e,model+0x4c))));
    w8(e,model+0x1c,0);
    call(e,HG_GUEST_FRAME,0x90,0,0,0);
}
static void model_shadow_quad(HGResourceEnv *e,uint32_t model,uint32_t stream) {
    uint32_t sp=call(e,HG_GUEST_FRAME,0x70,1,0,0);    /* 00129e40 */
    identity_to(e,0x70000500);
    w32(e,0x70000514,0x40866666u);w32(e,0x70000500,0x40866666u);   /* scale 4.2 */
    call(e,0x143c60,0xbfc90fdbu,0x70000500,0x70000500,0);         /* -pi/2 about x */
    uint32_t bone=r32(e,r32(e,model+0x58)+4u*r8(e,model+0x0d));
    copy_quad(e,0x70000530,bone+0x30);
    for(uint32_t k=0;k<4;k++)vu_transform(e,0x70000500,0x37bab0+16*k,sp+0x30+16*k);
    w32(e,0x70000700,0x40000000u);w32(e,0x70000704,0x40000000u);
    w32(e,0x70000708,0x40000000u);w32(e,0x7000070c,0x42f80000u);   /* (2,2,2,124) */
    call(e,0x14a0a0,stream,2,2,0);
    call(e,HG_CALL_WIDE_A3,0x99423880u,0x20072a25u,0,0);
    call(e,0x14d4f0,stream,sp+0x30,0x70000700,0x99423880u);
    call(e,0x14a0a0,stream,0,0,0);
    call(e,HG_GUEST_FRAME,0x70,0,0,0);
}
static void model_draw_mesh(HGResourceEnv *e) {          /* 00125f30 */
    call(e,HG_GUEST_FRAME,0x10,1,0,0);
    const uint32_t gp=0x3e7f70;
    uint32_t model=r32(e,gp+0x2a30),light=r32(e,model+0x50)&0x00f00000u;
    if(light==0x00200000u||light==0x00100000u)model_light_setup(e,model,0x70000c80,0x70000cc0);
    else if(light==0x00400000u) {domain_fault(e,0x76);call(e,HG_GUEST_FRAME,0x10,0,0,0);return;}
    model=r32(e,gp+0x2a30);
    uint32_t shadow=r32(e,model+0x50)&0x30000000u;
    if(shadow==0x30000000u)model_shadow_quad(e,model,r32(e,gp+0x2a50));
    else if(shadow==0x10000000u) {domain_fault(e,0x76);call(e,HG_GUEST_FRAME,0x10,0,0,0);return;}
    model=r32(e,gp+0x2a30);
    uint32_t data=r32(e,model+0x14);
    if(data!=0) {
        if((r32(e,model+0x50)&0x01000000u)!=0) {domain_fault(e,0x76);call(e,HG_GUEST_FRAME,0x10,0,0,0);return;}
        call(e,0x152aa0,r32(e,gp+0x2a50),data,0,0);
        uint32_t slot=r32(e,gp+0x3098)+4u*r32(e,gp+0x2a50)+0x53c;
        w8(e,r32(e,slot)+3,0x30);w32(e,r32(e,slot)+4,gp-0x7890);
        w16(e,r32(e,slot),3);w32(e,slot,r32(e,slot)+0x10);
        w32(e,gp+0x2a14,0);
    }
    call(e,HG_GUEST_FRAME,0x10,0,0,0);
}
static void model_draw_type(HGResourceEnv *e,uint32_t object) {   /* 00124010 */
    call(e,HG_GUEST_FRAME,0x140,1,0,0);
    uint32_t word=r32(e,object+0x50);
    if((word&0xffu)!=3) {domain_fault(e,0x75);call(e,HG_GUEST_FRAME,0x140,0,0,0);return;}
    call(e,0x128de0,0x3f800000u,word,0,0);
    model_draw_mesh(e);
    call(e,0x126180,0,0,0,0);
    call(e,0x128e80,0,0,0,0);
    if(r32(e,0x3e7f70+0x2a24)!=0)domain_fault(e,0x75);   /* 00125d10 */
    call(e,HG_GUEST_FRAME,0x140,0,0,0);
}
static uint32_t model_draw(HGResourceEnv *e,uint32_t owner,uint32_t mode) {   /* 00123ee0 */
    call(e,HG_GUEST_FRAME,0x30,1,0,0);
    const uint32_t object=owner+0x90;
    uint32_t drawn=0;
    if((r32(e,owner+0xe0)&0xc0000000u)!=0) {
        if(mode==1)domain_fault(e,0x74);                    /* 0018c9c0 visibility test */
        else if(feq(e,0,r32(e,object+0x4c)))drawn=1;
        else {
            uint32_t kind=r32(e,object+0x50)&0x0f000000u;
            uint32_t list=r32(e,0x3e7f70+0x3098)+0x310;
            if(kind==0x04000000u)call(e,0x125d60,1,list,owner,0);
            else if(kind==0x02000000u)call(e,0x125d60,5,list,owner,0);
            else if(kind==0x01000000u)call(e,0x125d60,2,list,owner,0);
            model_draw_type(e,object);
            drawn=1;
        }
    }
    call(e,HG_GUEST_FRAME,0x30,0,0,0);
    return drawn;
}
/* 0012bc70: draw the player model (00123ee0 with a1 = 0) after updating the
 * player +e0 draw bits: cleared while any of gp+2f78, 003f9e3a, 003f9f3d is
 * set, otherwise 0x30000000 set. Skipped while 003f92d1 is zero. */
void hg_player_model_draw(HGResourceEnv *e) {
    const uint32_t player=0x3f92d0;
    call(e,HG_SERVICE_MARK,0x12bc70,1,0,0);
    call(e,HG_GUEST_FRAME,0x10,1,0,0);
    if(r8(e,player+1)!=0) {
        if(r8(e,0x3e7f70+0x2f78)!=0 || r8(e,0x3f9e3a)!=0 || r8(e,0x3f9f3d)!=0)
            w32(e,player+0xe0,r32(e,player+0xe0)&0xcfffffffu);
        else w32(e,player+0xe0,r32(e,player+0xe0)|0x30000000u);
        model_draw(e,player,0);
    }
    call(e,HG_GUEST_FRAME,0x10,0,0,0);
    call(e,HG_SERVICE_MARK,0x12bc70,0,0,0);
}
/* 00143150 is the M8 list walker hg_actor_update_type2 (list 00539458, next
 * +24 read in the jalr delay slot before the +1c callback, counter gp+2f68 =
 * 003eaed8). M15 adds only the host seams for the continuous composition:
 * service marks around it and its original 0x20 guest frame, so its callbacks
 * run at the original stack depth. Observed callbacks 0019c8a0 and 002f7830
 * are C (routed by the bus); 001a6dc0 and 002a20b0 execute original
 * instructions through the bus. */
void hg_render_node_list(HGResourceEnv *e) {
    call(e,HG_SERVICE_MARK,0x143150,1,0,0);
    call(e,HG_GUEST_FRAME,0x20,1,0,0);
    hg_actor_update_type2(e);
    call(e,HG_GUEST_FRAME,0x20,0,0,0);
    call(e,HG_SERVICE_MARK,0x143150,0,0,0);
}
static uint32_t render_node_process_call(void *user,uint32_t pc,uint32_t a,uint32_t b,uint32_t c,uint32_t d) {
    HGProcessing *p=user;
    if(pc==0x19c8a0) {hg_camera_anchor_node(&p->env,a);return 0;}
    if(pc==0x2f7830) {hg_camera_particle_field(&p->env,a);return 0;}
    return p->outer->call(p->outer->user,pc,a,b,c,d);
}
/* Synthetic-differential entry: 00143150 with only its two C callbacks routed
 * to C; every other call (including 00138370) goes to the caller's bus. */
void hg_render_node_list_fixed(HGResourceEnv *e) {
    HGProcessing p={e,{0},0};p.env=*e;p.env.user=&p;
    p.env.load=process_load;p.env.store=process_store;p.env.call=render_node_process_call;
    hg_render_node_list(&p.env);
}

void hg_frame_edge_sync(HGResourceEnv *e) {
    w32(e,0x3fa050,r32(e,0x3fa040));
    w32(e,0x3fa040,0);
    w32(e,0x3f9e34+0x220,r32(e,0x3f9e34+0x210));
    w32(e,0x3f9e34+0x210,0);
    w32(e,0x3f9e38+0x220,r32(e,0x3f9e38+0x210));
    w32(e,0x3f9e38+0x210,0);
    w32(e,0x3f9e3c+0x220,r32(e,0x3f9e3c+0x210));
    w32(e,0x3f9e3c+0x210,0);
}
void hg_counter_advance(HGResourceEnv *e) {
    if(r8(e,frame_guard)==0) {
        w32(e,0x46d930,r32(e,0x46d930)+1u);
        w32(e,0x46e110,r32(e,0x46e110)+1u);
        if(r32(e,0x46e110)==0xffffffffu)w32(e,0x46e110,0xffffffffu);
    }
    /* 137a40..137aa4 mega-counter path: reachable only after 108000 frames
     * (46d930 >= 0x1a5e0); never observed in the retail captures. Reconstructed
     * for the listing. Both guard states reach this threshold check. */
    if((uint32_t)r32(e,0x46d930)<108000u)return;
    w32(e,0x46d908,r32(e,0x46d908)+1u);
    int32_t m=(int32_t)r32(e,0x46d908);
    if(m<0)w32(e,0x46d908,0);      /* bgez guard: negative wrap resets */
    else if(m>=3)w32(e,0x46d908,3); /* clamped at 3 */
    w32(e,0x46d930,0);
}
void hg_frame_advance(HGResourceEnv *e) {
    call(e,HG_GUEST_FRAME,0x50,1,0,0); /* coordinator-entry seam (0012f850) */
    w32(e,0x3fa190,r32(e,0x3fa190)+1u);
    if((int32_t)r32(e,0x3fa190)<0)w32(e,0x3fa190,0); /* bgez guard: negative wrap resets */
    call(e,0x31bb80,0,0,0,0);
    w16(e,0x3f9ff8,0);
    if(r8(e,frame_guard)==0)w32(e,0x46e118,r32(e,0x46e118)+1u);
    call(e,HG_GUEST_FRAME,0x50,0,0,0); /* coordinator-counter seam (0012f8a8) */
}
static uint32_t frame_process_call(void *user,uint32_t pc,uint32_t a,uint32_t b,uint32_t c,uint32_t d) {
    HGProcessing *p=user;
    if(pc==0x31bb80) {hg_frame_edge_sync(&p->env);return 0;}
    if(pc==0x12f850) {hg_frame_advance(&p->env);return 0;}
    if(pc==0x198f60) {hg_pre_dispatch_service(&p->env);return 0;}
    /* M15: 00143150 render-node list and its observed C callbacks. */
    if(pc==0x143150) {hg_render_node_list(&p->env);return 0;}
    if(pc==0x12bc70) {hg_player_model_draw(&p->env);return 0;}
    if(pc==0x19c8a0) {hg_camera_anchor_node(&p->env,a);return 0;}
    if(pc==0x2f7830) {hg_camera_particle_field(&p->env,a);return 0;}
    /* Coordinator 0012f850 engine-service calls (authenticated jal targets
 * 0012f8a8..0012f92c plus the counter routine 00137890). The first three
     * and 00143150/0012bc70 (M15) are connected above; the other calls are declared
     * external. Matrix helpers 00143c60/00143d30/
     * 00143b90 are NOT coordinator calls: they are the camera-chain rotations
     * reached from 0012bd00/0012c790 (hg_camera_head_matrix/hg_camera_update)
     * and must execute their original instructions, exactly as in the M12
     * grounded path (they fall through to the owner bus, which interprets
     * them with FP_SEAMS operand placement). Externalizing them left the head
     * matrix identity and diverged the camera struct from the first frame. */
    if(pc==0x13b6e0 ||
       pc==0x1430a0 || pc==0x143100 || pc==0x18c520 ||
       pc==0x1431a0 || pc==0x25c170 || pc==0x182480 ||
       pc==0x13ce80 || pc==0x137890) {
        p->outer->call(p->outer->user,HG_EXTERNAL_CALL,pc,0,0,0);
        return 0;
    }
    return grounded_process_call(user,pc,a,b,c,d);
}
static void frame_prepare_packets(HGResourceEnv *e) {
    uint32_t ctx=r32(e,0x3e7f70+0x3098);
    uint32_t bank=r32(e,ctx+0x598);
    uint32_t field=call(e,HG_RENDER_FIELD,r32(e,ctx+0x59c),0,0,0);
    if(bank>1 || field>1) {domain_fault(e,0x63);return;}
    w32(e,ctx+0x59c,field);  /* 00149940: store GS CSR bit 13 */
    w32(e,ctx+0x598,1u-bank); /* 00149870..80: select next render bank */
    /* 0011d93c calls this at the start of the next render iteration.
     * Original instructions rebuild packet prefixes/cursors from the bank's
     * table and reset the sort queue through 0014906c -> 00155330. */
    call(e,0x148970,0,0,0,0);
}

void hg_player_frame_continuous(HGResourceEnv *e,uint32_t frame) {
    HGProcessing p={e,{0},frame};p.env=*e;p.env.user=&p;
    HGResourceEnv *bus=&p.env;
    bus->load=process_load;bus->store=process_store;bus->call=frame_process_call;
    hg_frame_advance(bus);     /* 0012f850 prefix: 3fa190, edge sync, 3f9ff8, 46e118 */
    call(bus,0x198f60,0,0,0,0);
    call(bus,0x17f6d0,0x3f9e30,0,0,0); /* 0012f8b8 delay-slot a0 */
    call(bus,0x17f2d0,0,0,0,0);         /* 0012f8c0 delay-slot a0 */
    call(bus,0xfffffff7u,0,0,0,0);      /* before 00158560 executes */
    hg_player_dispatch(bus);   /* walk, C 0015ebc0, C 0015fc10/0015e420, history */
    hg_camera_update(bus);     /* same-frame camera on the floor-corrected position */
    /* Coordinator tail (0012f850 after the camera return): external services
     * around the C player model draw 0012bc70 and render-node list 00143150, then the engine routine 00137890
     * whose 001379f0 body advances 46d930 and 46e110 (hg_counter_advance). */
    call(bus,HG_EXTERNAL_CALL,0x13b6e0,0,0,0);
    call(bus,HG_EXTERNAL_CALL,0x1430a0,0,0,0);
    call(bus,HG_EXTERNAL_CALL,0x143100,0,0,0);
    call(bus,0x12bc70,0,0,0,0);                /* 0012f8f0: M15 C player model draw */
    call(bus,HG_EXTERNAL_CALL,0x18c520,0,0,0);
    call(bus,0x143150,0,0,0,0);                /* 0012f900: M15 C render-node list */
    call(bus,HG_EXTERNAL_CALL,0x1431a0,0,0,0);
    call(bus,HG_EXTERNAL_CALL,0x25c170,0,0,0);
    call(bus,HG_EXTERNAL_CALL,0x182480,0,0,0);
    call(bus,HG_EXTERNAL_CALL,0x13ce80,0,0,0);
    hg_counter_advance(bus);   /* 00137890's 001379f0 body: 46d930/46e110 */
    frame_prepare_packets(bus);
}
