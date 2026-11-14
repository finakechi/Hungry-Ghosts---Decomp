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
 * 001386e0 (geometry/material processing) and 00152ae0 (upload) stay explicit
 * external operations; neither is exercised by the selected bootstrap member. */
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
 * this entry wires it to M4 and M3 using the SAME bus and disc environment.
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
    if(pc==0x137ed0)return hg_resource_fixup(&p->env,a,b,c,p->frame);
    if(pc==0x1389d0)return hg_resource_map_address(&p->env,a,p->frame-0x50);
    return p->outer->call(p->outer->user,pc,a,b,c,d);
}
void hg_resource_worker_fixed(HGResourceEnv *e,uint32_t frame) {
    HGProcessing p={e,{0},frame};p.env=*e;p.env.user=&p;
    p.env.load=process_load;p.env.store=process_store;p.env.call=process_call;
    hg_resource_worker(&p.env);
}
