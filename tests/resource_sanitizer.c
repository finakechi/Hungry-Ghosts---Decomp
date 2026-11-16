/* Standalone synthetic integration under ASan/UBSan; contains no retail data. */
#include "resource_loader.h"
#include <assert.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
typedef struct {
    unsigned char *ram;
    unsigned char disk[0x84800],area[4096],scratch[0x4000];
    unsigned ticks,reads,rejects;
    unsigned player_trace[128],player_calls,player_timer_finishes,player_animation_selects;
} Test;
static uint32_t load(void *u,uint32_t a,uint32_t n) {
    Test *t=u;unsigned char *bytes=t->ram;uint32_t limit=0x2000000;
    if(a>=0x70000000){a-=0x70000000;bytes=t->scratch;limit=sizeof t->scratch;}
    assert(a<limit && n<=4 && a+n<=limit);
    uint32_t v=0;for(uint32_t i=0;i<n;i++)v|=(uint32_t)bytes[a+i]<<(8*i);return v;
}
static void store(void *u,uint32_t a,uint32_t n,uint32_t v) {
    Test *t=u;unsigned char *bytes=t->ram;uint32_t limit=0x2000000;
    if(a>=0x70000000){a-=0x70000000;bytes=t->scratch;limit=sizeof t->scratch;}
    assert(a<limit && n<=4 && a+n<=limit);
    for(uint32_t i=0;i<n;i++)bytes[a+i]=(unsigned char)(v>>(8*i));
}
static uint32_t read32(void *u,uint32_t a) { return load(u,a,4); }
static int32_t sync_read(void *u,int32_t arg) { (void)u;assert(arg==1);return 0; }
static int32_t ready(void *u,int32_t arg) { (void)u;assert(arg==0 || arg==1);return 2; }
static int32_t error(void *u) { (void)u;return 0; }
static int32_t read_disc(void *u,uint32_t lba,uint32_t count,uint32_t dst,const uint8_t *mode) {
    Test *t=u;assert(mode[0]==0 && mode[1]==1 && mode[2]==0);
    if(t->rejects){t->rejects--;return 0;}
    assert(dst+count*2048u<0x2000000);
    if(lba==2000) {
        assert(count==2);memcpy(t->ram+dst,t->area,count*2048u);
    }else {
        assert(lba>=1000 && (lba-1000+count)*2048u<=sizeof t->disk);
        memcpy(t->ram+dst,t->disk+(lba-1000)*2048,count*2048u);
    }
    t->reads++;return 1;
}
static uint32_t external(void *u,uint32_t pc,uint32_t a,uint32_t b,uint32_t c,uint32_t d) {
    Test *t=u;(void)d;
    switch(pc) {
    case 0x119ac8:assert(a+c<0x2000000);memset(t->ram+a,(int)b,c);return a;
    case 0x103d60:assert(a==0x3f8e00);return 17;
    case 0x103d80:assert(a==17 && b==0);return 0;
    case 0x103da0:return 0;
    case 0x130020:case 0x12fe90:assert(a==0x3f8e00);return 0;
    case 0x1434e0:assert(++t->ticks<100);return 0;
    case 0x137ed0:return a+32;
    case 0x156a50:return (a+load(u,a+0x10,4)+0x40u)&0xffffffc0u;
    case 0x1389d0:return 0;
    case 0x1041e0:assert(a==2);return 0;
    case 0x1235c0:assert(a==0x5f0090 && b==5);return 0;
    case 0x123660:assert(a==0x5f0090 && b<=1);return 0;
    case 0x1330f0:store(u,a+0x18,1,b);return 0;
    case 0x143e60:return 0; /* explicit synthetic quaternion boundary */
    case 0x18b6c0:case 0x18c120:
        assert(a==0x5f0000 && b==0x5f02d0);return pc==0x18b6c0;
    case 0x15e0c0:assert(a==0x5f0000);return 0;
    case 0x13a050:assert(a==0x3f800000 && b==0x5f0090);return 0;
    case 0x15fe00:case 0x188650:case 0x15bf30:case 0x159380:
    case 0x158e20:case 0x158ce0:assert(a==0x5f0000);return 0;
    case 0x161110:assert(a==0x5f0000 && b==0);return 0;
    case 0x15e190:assert(a==0 && b==0x5f0000 && c==0x6000000);return 0;
    case 0x144340:assert(a==0x5f0050 && b==0x5f0030 && c==0x5f0040);return 0;
    case 0x139c90:
        assert(a==0x5f0000 && b==0x70000b90);
        assert(load(u,b,4)==0x41c00000 && load(u,b+12,4)==0x3f800000);return 0;
    case 0x18c280:
        assert(a==1 && load(u,0x3eb058,4)==0x5f0000 && load(u,0x3eb05c,4)==0x5f02d0);return 0;
    case 0x142e90:assert(a==0x5f0000);return 0;
    case 0x100100:
        assert(a==0x500000 || a==0x500400);
        store(u,a+0x24,4,0);return 0;
    default:assert(!"Unexpected external operation");return 0;
    }
}
/* Synthetic player engine/math boundaries on the same RAM/scratch bus. The
 * actual C resolver owns resource lookup, nodes, state transitions and spawned
 * actor/list effects. These leaves calculate no result from retail captures. */
static uint32_t player_external(void *u,uint32_t pc,uint32_t a,uint32_t b,uint32_t c,uint32_t d) {
    Test *t=u;const uint32_t actor=0x3f92d0;
    assert(t->player_calls<sizeof t->player_trace/sizeof t->player_trace[0]);
    t->player_trace[t->player_calls++]=pc;
    switch(pc) {
    case 0x119ac8:case 0x1330f0:return external(u,pc,a,b,c,d);
    case 0x119910:
        assert(a+c<0x2000000 && b+c<0x2000000);memmove(t->ram+a,t->ram+b,c);return a;
    case 0x139ff0:
        assert(a==actor+0x90 && b==0x600000 && c==0x06000000);
        store(u,a+0x26,2,c);return 0;
    case 0x13a060:
        assert(a==0 && (b==0 || b==0x41200000) && c==actor+0x90);
        store(u,c+0x26,2,d);t->player_animation_selects++;return 0;
    case 0x1b7f00:assert(a==actor+0x30);return 3;
    case 0x196ad0:assert((a==0x31 || a==0xaa || a==0xe8) && b==0);return 0;
    case 0x144340:
        assert(a==actor+0x50 && b==actor+0x30 && c==actor+0x40);
        for(unsigned i=0;i<16;i++)store(u,a+i*4,4,i%5==0?0x3f800000u:0);
        memcpy(t->ram+a+0x30,t->ram+b,16);return 0;
    case 0x13a240:
        assert(a==actor+0x90 && b==actor+0x50 && c==0);
        for(unsigned i=0;i<load(u,a+0x54,4);i++) {
            uint32_t node=load(u,load(u,a+0x58,4)+i*4,4);
            assert(node+64<0x2000000);memcpy(t->ram+node,t->ram+b,64);
        }
        return 0;
    case 0x267450:assert(a==actor);return 0;
    case 0x13a050:assert(a==0x3f800000 && b==actor+0x90);return 0;
    case 0x15fe00:assert(a==actor && b<=1);return 0;
    case 0x15d770:case 0x188650:case 0x15bf30:assert(a==actor);return 0;
    case 0x161110:case 0x174900:assert(a==actor && b==0);return 0;
    case 0x120830:assert(a==actor+0x30 && b==actor+0x390);return 0x3f800001;
    case 0x188550:assert(a==0x41af3333 && b==actor);return 0;
    case 0x17d930:assert(a==0 && b==0 && c==1);return 0;
    case 0x120490:assert(a==0x720000 && b==actor);return 0;
    case 0x1cc5b0:
        assert(a==0 && b==0x70000b80 && c==0x70000b80);t->player_timer_finishes++;return 0;
    default:assert(!"Unexpected player engine boundary");return 0;
    }
}
static void player_composition(Test *t,HGResourceEnv *original) {
    const uint32_t actor=0x3f92d0,block=0x600000,model=0x601000,frame=0x1ff0790;
    HGResourceEnv e=*original;e.call=player_external;
    for(unsigned edge=0;edge<=128;edge+=128) {
        memset(t->ram,0,0x2000000);memset(t->scratch,0,sizeof t->scratch);
        t->player_calls=0;t->player_timer_finishes=0;t->player_animation_selects=0;
        store(t,0x3f43fc,4,block);store(t,block,4,0x354b4c46);store(t,block+4,4,214);
        store(t,block+16+211*4,4,0x01001000);store(t,block+16+213*4,4,0x09002000);
        store(t,model+8,4,3);store(t,model+0x34,4,0x3000);
        store(t,0x4d8140,4,0x70000c);store(t,0x4d8144,4,3);
        for(unsigned i=0;i<3;i++)store(t,0x700000+i*4,4,0x710200-i*0x100);
        store(t,0x37b9a0+8,4,0x10203040);
        store(t,actor+0x30,4,0x3f800000);store(t,actor+0x34,4,0x40000000);
        store(t,actor+0x38,4,0x40400000);store(t,actor+0x3c,4,0x3f800000);
        store(t,0x3fa201,1,1);store(t,0x3fa202,1,255);
        store(t,0x3eaec8,2,32);store(t,0x3eaed0,2,128);
        store(t,0x539430,4,0x720000);store(t,0x53943c,4,1);
        store(t,0x46d90c,4,2);
        hg_player_initialize_fixed(&e,actor);
        assert(load(t,actor+0xa0,4)==model && load(t,actor+0xa4,4)==0x602000);
        assert(load(t,actor+0xe4,4)==3 && load(t,0x4d8144,4)==0);
        assert(load(t,actor+0x350+0x30,4)==0x3f800000);
        assert(load(t,actor+0x449,1)==255 && load(t,actor+0x44a,1)==0);
        assert(load(t,actor+0xe0,4)==0x10203040 && load(t,actor+0x9d,1)==1);
        hg_player_active_update_fixed(&e,actor,frame);
        hg_player_active_update_fixed(&e,actor,frame);
        assert(load(t,actor+0x19,1)==1 && load(t,actor+0x1a,1)==1);
        assert(load(t,0x46d90c,4)==0 && t->player_timer_finishes==1);
        store(t,0x3eaf04,2,edge);hg_player_active_update_fixed(&e,actor,frame);
        assert(load(t,actor+0x448,1)==(edge!=0) && load(t,actor+0x449,1)==255);
        assert(load(t,actor+0x19,1)==(edge?0u:1u));
        assert(load(t,actor+0x450,4)==0 && load(t,0x53943c,4)==1);
        store(t,0x3eaf04,2,0);hg_player_active_update_fixed(&e,actor,frame);
        assert(load(t,actor+0x19,1)==1 && load(t,actor+0x4f8,4)==0x3f800001);
        if(edge) {
            assert(load(t,actor+0x450,4)==0x720000 && load(t,0x539430,4)==0);
            assert(load(t,0x53943c,4)==0 && load(t,0x539450,4)==0x720000);
            assert(load(t,0x72001c,4)==0x16b0f0 && load(t,0x720002,1)==1);
            assert(load(t,0x720000,1)==2 && load(t,0x720010,4)==1);
            assert(load(t,0x72003c,4)==0x3f800000 && load(t,actor+0xb6,2)==0x75);
            assert(t->player_animation_selects==2);
            unsigned calls=t->player_calls;
            assert(hg_player_equipment_enter(&e,actor)==0 && t->player_calls==calls);
        } else {
            assert(load(t,actor+0x450,4)==0 && load(t,0x539430,4)==0x720000);
            assert(load(t,0x53943c,4)==1 && t->player_animation_selects==0);
        }
    }
    store(t,0x46d894,4,0x400);assert(hg_player_flag_status(&e,0x80)==0);
    assert(hg_player_flag_status(&e,0x400)==1);
    store(t,0x3f9f08,4,0x400);assert(hg_player_flag_status(&e,0x400)==2);
}
static uint32_t actor_setup(void *u,uint32_t pc,uint32_t a,uint32_t b,
                            uint32_t c,uint32_t d,uint32_t e,uint32_t f) {
    (void)u;assert(pc==0x18a330 && a==0x5f0000);
    assert(b==0x1000000 && c==0x9000001 && d==0 && e==0 && f==0);return 0;
}
static void disk_word(Test *t,uint32_t a,uint32_t v) {
    for(unsigned i=0;i<4;i++)t->disk[a+i]=(unsigned char)(v>>(i*8));
}
int main(void) {
    Test t={0};t.ram=calloc(1,0x2000000);assert(t.ram);t.rejects=2;
    HGReadState read_state={0};HGReadEnv disc={&t,read32,sync_read,ready,read_disc,error,0,0,0,0};
    HGResourceEnv e={&t,load,store,external,&read_state,&disc};
    hg_resource_init(&e);store(&t,0x3ead20,4,1000);store(&t,0x3ead24,4,sizeof t.disk);
    disk_word(&t,4,2);disk_word(&t,8,2);disk_word(&t,12,2048);disk_word(&t,16,2048);
    disk_word(&t,20,4096);disk_word(&t,32,1);
    disk_word(&t,64,2048);disk_word(&t,68,2048);disk_word(&t,72,4);
    disk_word(&t,80,4096);disk_word(&t,84,2048);disk_word(&t,88,7);
    memcpy(t.disk+2048,"TEST",4);memcpy(t.disk+4096,"DATA",4);
    hg_resource_request_raw(&e,0,0);hg_resource_worker(&e);
    assert(t.reads==2 && load(&t,0x3f8e30,2)==0 && load(&t,0x3eacec,1)==0);
    assert(load(&t,0x3f4310,4)==0xb00000 && load(&t,0x3f431c,4)==0xb00800);
    assert(load(&t,0x3f4d3c,4)==0xb01000 && memcmp(t.ram+0xb00000,"TEST",4)==0);
    store(&t,0xa87814,4,32);memset(t.ram+0xa88000,0xff,32);hg_area_finalize(&e,0xa87800,2048);
    for(unsigned i=0;i<32;i++)assert(t.ram[0xa88000+i]==0);
    store(&t,0xb00000,4,0x60000000);store(&t,0xb00008,4,0x100);store(&t,0xb0000c,4,0x121);
    hg_resource_rebase(&e,0xb00000,0x3200);assert(load(&t,0xb00008,4)==0x3200);
    /* Combined path with a real synthetic FLK5 texture and alternate ID. */
    disk_word(&t,72,0x96);disk_word(&t,2048,0x354b4c46);
    disk_word(&t,2052,1);disk_word(&t,2056,2048);disk_word(&t,2060,0);
    disk_word(&t,2064,0x09000020);disk_word(&t,2080,0x60000000);
    disk_word(&t,2088,0x100);disk_word(&t,2092,0x121);
    hg_resource_request_raw(&e,0,0);hg_resource_worker_fixed(&e,0x1ff05c0);
    assert(load(&t,0xb00028,4)==0x1e00 && load(&t,0xb0002c,4)==0x1e21);
    assert(load(&t,0xb00008,4)==0x1d00 && load(&t,0x3f4558,4)==0xb00000);
    store(&t,0x1ff05bf,1,0xfe);
    assert(hg_resource_map_address(&e,0xffffffff,0x1ff05c0)==0xfffffffe);
    /* Kind 1 / directory 0x102 is the general loader's AREA mode. Keep the
     * existing M1 bus and M4 texture processing connected through the worker. */
    memcpy(t.disk+0x81000,t.disk,2048);memcpy(t.disk+0x81800,t.disk+2048,4096);
    disk_word(&t,0x8100c,0x81800);disk_word(&t,0x81010,0x81800);
    disk_word(&t,0x81040,0x81800);disk_word(&t,0x81050,0x82000);
    memcpy(t.area,"MWo3",4);t.area[0x14]=37;
    store(&t,0x3dff04,4,0xa87800);store(&t,0x3fa1b0,1,1);store(&t,0x3fa1b1,1,2);
    store(&t,0x3e01b4,4,3);store(&t,0x3f3a58,4,2000);store(&t,0x3f3a5c,4,2049);
    store(&t,0x3f4d50,4,0xc00000);store(&t,0x3f4d3c,4,0x12345678);
    store(&t,0x3f4d38,4,0x87654321);store(&t,0x3f4d3c+4,4,0x11223344);
    memset(t.ram+0xa88001,0xff,37);memcpy(t.ram+0xc02000,"RETAINED",8);
    unsigned before=t.reads;
    hg_resource_request_raw(&e,1,0x102);hg_resource_worker_fixed(&e,0x1ff05c0);
    assert(t.reads==before+3 && load(&t,0x3f8e30,2)==0 && load(&t,0x3eacec,1)==0);
    assert(memcmp(t.ram+0xa87800,"MWo3",4)==0);
    for(unsigned i=0;i<37;i++)assert(t.ram[0xa88001+i]==0);
    assert(load(&t,0xc00028,4)==0x1e00 && load(&t,0x3f4558,4)==0xc00000);
    assert(load(&t,0x3f4d3c,4)==0x12345678 && load(&t,0x3f4d40,4)==0x11223344);
    assert(load(&t,0x3f4d50,4)==0xc00000 && load(&t,0x3f4d54,4)==0xc01000);
    assert(load(&t,0x3f4d5c,4)==0xc51000 && load(&t,0x3f4d38,4)==0);
    assert(memcmp(t.ram+0xc02000,"RETAINED",8)==0);
    hg_resource_request_raw(&e,1,0x102);hg_resource_worker_fixed(&e,0x1ff05c0);
    assert(load(&t,0x3f4558,4)==0xc00000 && load(&t,0x3f4d50,4)==0xc00000);
    /* Kind 2 loads AREA and a first PACK, then the same worker changes to kind
     * 3 and appends another PACK. Downstream 130020/12fe90 stay explicit. */
    memcpy(t.disk+0x2000,t.disk+2048,4096);memcpy(t.disk+0x3000,t.disk+0x2000,4096);
    memcpy(t.disk+0x1000,t.disk,2048);memcpy(t.disk+0x1800,t.disk,2048);
    disk_word(&t,0x100c,0x2000);disk_word(&t,0x1010,0x2000);
    disk_word(&t,0x1040,0x2000);disk_word(&t,0x1050,0x2800);
    disk_word(&t,0x180c,0x3000);disk_word(&t,0x1810,0x3000);
    disk_word(&t,0x1840,0x3000);disk_word(&t,0x1850,0x3800);disk_word(&t,0x1848,0x97);
    store(&t,0x3fa1b0,1,0);store(&t,0x3fa1b1,1,0);store(&t,0x3fa1b2,1,0);
    store(&t,0x3e01b0,4,5);store(&t,0x3f4d4c,4,0xc00000);
    before=t.reads;t.ticks=0;
    hg_resource_request_raw(&e,2,2);hg_resource_worker_fixed(&e,0x1ff05c0);
    assert(t.reads==before+5 && load(&t,0x3f8e30,2)==0 && load(&t,0x3eacec,1)==0);
    assert(load(&t,0x3f8e32,2)==3 && load(&t,0x3f8e44,4)==3 && load(&t,0x3eace0,1)==1);
    assert(load(&t,0x3f4558,4)==0xc00000 && load(&t,0x3f455c,4)==0xc01000);
    assert(load(&t,0xc00028,4)==0x1e00 && load(&t,0xc01028,4)==0x3200);
    assert(load(&t,0x3f4d50,4)==0xc01000 && load(&t,0x3f4d54,4)==0xc02000);
    assert(load(&t,0x3f4d5c,4)==0xc52000);
    /* M6: thirteen synthetic model+texture members through the same kind-2
     * AREA/worker path. No geometry/upload external mock is accepted above. */
    memset(t.disk+0x1000,0,0x7800);
    disk_word(&t,0x1000,2);disk_word(&t,0x1004,13);disk_word(&t,0x1008,13);
    disk_word(&t,0x100c,0x2000);disk_word(&t,0x1010,0x2000);
    disk_word(&t,0x1014,13*2048);disk_word(&t,0x1020,13);disk_word(&t,0x1800,3);
    for(unsigned i=0;i<13;i++) {
        uint32_t a=0x2000+i*2048,descriptor=0x1040+i*16;
        disk_word(&t,descriptor,a);disk_word(&t,descriptor+4,2048);disk_word(&t,descriptor+8,0x200+i);
        disk_word(&t,a,0x354b4c46);disk_word(&t,a+4,2);disk_word(&t,a+8,2048);
        disk_word(&t,a+16,0x01000020);disk_word(&t,a+20,0x09000400);
        disk_word(&t,a+0x20,1);disk_word(&t,a+0x30,0x30000);
        disk_word(&t,a+0x34,0xc0400000);disk_word(&t,a+0x44,0x40000000);
        disk_word(&t,a+0x3c,0xbf800000);disk_word(&t,a+0x4c,0x40800000);
        disk_word(&t,a+0x60,0x60000000);disk_word(&t,a+0x64,0x100);
        disk_word(&t,a+0x68,0x4018);disk_word(&t,a+0x6c,1);
        disk_word(&t,a+0xac,0x13000000);
        disk_word(&t,a+0x400,0x60000000);disk_word(&t,a+0x408,0x100);
        disk_word(&t,a+0x40c,0x120);disk_word(&t,a+0x434,0x18);
        disk_word(&t,a+0x450,64);disk_word(&t,a+0x454,4);
    }
    t.ticks=0;hg_resource_request_raw(&e,2,2);hg_resource_worker_fixed(&e,0x1ff05c0);
    for(unsigned i=0;i<13;i++) {
        uint32_t a=0xc00000+i*2048;
        assert(load(&t,a+0x40,4)==0x40a00000 && load(&t,a+8,4)==0x3100);
        assert(load(&t,a+0x64,4)==0x3100+0x100 && load(&t,a+0x68,4)==(0x3100+0x200)*32);
        assert(load(&t,a+0x408,4)==0x3200 && load(&t,0x3f4b00+i*4,4)==a);
    }
    assert(load(&t,0x3f8e30,2)==0 && load(&t,0x3eacec,1)==0);
    /* Invoke the reconstructed AREA1 actor callback with synthetic actor state.
     * Resident actor/matrix operations stay explicit mocked boundaries. */
    memset(t.ram+0x5f0000,0xa5,0x400);store(&t,0x5f0018,1,0);
    store(&t,0x5f02d0,4,0x80000001);
    hg_area1_actor(&e,0x5f0000,actor_setup);
    assert(load(&t,0x5f0000,1)==0xa5 && load(&t,0x5f0018,1)==1);
    assert(load(&t,0x5f02d0,4)==0x24 && load(&t,0x5f002a,2)==0x233);
    assert(load(&t,0x5f0004,4)==8 && load(&t,0x5f02bc,4)==0x5f02d0);
    assert(load(&t,0x5f00dc,4)==0x3f800000 && load(&t,0x5f00a8,4)==0x5f0300);
    assert(load(&t,0x5f02d4,4)==0x3f800000 && load(&t,0x5f02f0,4)==0x3f800000);
    assert(load(&t,0x5f0300,4)==0 && load(&t,0x5f0308,4)==0xbecccccd && load(&t,0x5f030c,4)==0);
    hg_area1_actor(&e,0x5f0000,actor_setup);
    assert(load(&t,0x3eb058,4)==0x5f0000 && load(&t,0x3eb05c,4)==0x5f02d0);
    store(&t,0x5f0018,1,2);hg_area1_actor(&e,0x5f0000,actor_setup);
    assert(load(&t,0x5f0018,1)==3);hg_area1_actor(&e,0x5f0000,actor_setup);
    assert(memcmp(t.ram+0xa87800,t.area,2049)==0);
    /* M7: kind-3 continuation through retained and context directories. The
     * invented special member supplies the explicit successful audio endpoint;
     * later raw members test publication and arena reuse without audio/IOP. */
    memset(t.ram,0,0x2000000);memset(t.disk,0,sizeof t.disk);
    memset(t.scratch,0,sizeof t.scratch);t.ticks=0;t.reads=0;t.rejects=0;
    read_state=(HGReadState){0};disc.active=0;
    store(&t,0x3ead20,4,1000);store(&t,0x3ead24,4,sizeof t.disk);
    store(&t,0x700000c4,4,2);store(&t,0x3fa1b0,1,1);
    store(&t,0x3e01c0,4,0x85);
    store(&t,0x3f4d44,4,0xc00000);store(&t,0x3f4d48,4,0xc91000);
    store(&t,0x3f4d50,4,0xd00000);store(&t,0x3f4d54,4,0xd00000);
    disk_word(&t,3*2048,3);
    for(unsigned i=0;i<3;i++) {
        uint32_t id=i==0?0x6b:(i==1?0x4c:0x85),h=id*2048,p=0x81000+i*2048;
        disk_word(&t,h,id);disk_word(&t,h+4,1);disk_word(&t,h+12,p);
        disk_word(&t,h+16,p);disk_word(&t,h+20,2048);
        disk_word(&t,h+8,1);disk_word(&t,h+64,p);
        disk_word(&t,h+68,2048);disk_word(&t,h+72,i==1?0x74:0x95);
        disk_word(&t,p,0x12345678+i);
        if(i==0) {
            disk_word(&t,h+8,0);disk_word(&t,h+16,p+2048);
            disk_word(&t,h+20,0);disk_word(&t,h+24,1);
            disk_word(&t,h+72,0x51);disk_word(&t,p+0x10,0x780);
        }
    }
    hg_resource_request_raw(&e,3,3);hg_resource_worker_fixed(&e,0x1ff05c0);
    assert(load(&t,0x3f4444,4)==0xc917c0 && load(&t,0x3f4d48,4)==0xc91800);
    assert(load(&t,0x3f4300+0x74*4,4)==0xc91800);
    assert(load(&t,0x3f4300+0x95*4,4)==0xc00000);
    assert(load(&t,0xc91800,4)==0x12345679 && load(&t,0xc00000,4)==0x1234567a);
    assert(load(&t,0x3f9fef,1)==0x4c && load(&t,0x46e10c,1)==1);
    assert(load(&t,0x3f8e30,2)==0 && load(&t,0x3eacec,1)==0);
    unsigned reads=t.reads;
    hg_resource_request_raw(&e,8,0);hg_resource_worker_fixed(&e,0x1ff05c0);
    assert(t.reads==reads && load(&t,0xc91800,4)==0x12345679);
    /* M8 pool construction into the ordinary list, then callbacks which sever
     * their own links. Both must run because NEXT is prefetched in a delay slot. */
    memset(t.ram+0x539430,0,56);
    store(&t,0x539430,4,0x500000);store(&t,0x53943c,4,2);
    store(&t,0x500024,4,0x500400);
    assert(hg_actor_allocate(&e,0,7,0x100100)==0x500000);
    assert(hg_actor_allocate(&e,0,8,0x100100)==0x500400);
    assert(hg_actor_allocate(&e,0,8,0x100100)==0);
    assert(load(&t,0x500424,4)==0 && load(&t,0x500420,4)==0x500000);
    hg_actor_update_main(&e);assert(load(&t,0x3eaed8,2)==2);
    hg_actor_update_priority(&e);assert(load(&t,0x3eaed8,2)==0);
    hg_actor_update_type2(&e);hg_actor_update_type2_priority(&e);
    store(&t,0x539438,4,0x500800);store(&t,0x539444,4,1);
    store(&t,0x500829,1,2);store(&t,0x500890,4,0x12345678);
    assert(hg_actor_allocate(&e,0x82,9,0x100100)==0x500800);
    assert(load(&t,0x539460,4)==0x500800 && load(&t,0x500890,4)==0x12345678);
    /* Connected real setup/lookup/node allocation, then the active dispatcher.
     * Quaternion, transform and render consumers remain declared boundaries. */
    memset(t.ram+0x5f0000,0,0x400);
    store(&t,0x3f4300+0x233*4,4,0x600000);
    store(&t,0x600000,4,0x354b4c46);store(&t,0x600004,4,2);
    store(&t,0x600010,4,0x01001000);store(&t,0x600014,4,0x09002000);
    store(&t,0x601008,4,2);store(&t,0x60100c,4,0x40);
    store(&t,0x4d8140,4,0x700008);store(&t,0x4d8144,4,2);
    store(&t,0x700000,4,0x710100);store(&t,0x700004,4,0x710000);
    store(&t,0x37b9a0+20,4,0x12345678);
    hg_area1_actor_fixed(&e,0x5f0000);
    assert(load(&t,0x5f0018,1)==1 && load(&t,0x5f00a0,4)==0x601000);
    assert(load(&t,0x5f00f0,4)==0x710000 && load(&t,0x5f00f4,4)==0x710100);
    assert(load(&t,0x4d8144,4)==0 && load(&t,0x5f0274,4)==0);
    hg_area1_actor_fixed(&e,0x5f0000);
    store(&t,0x3fa067,1,0);store(&t,0x3f9fee,1,16);
    store(&t,0x3eaf1c,4,0xbf400000);
    assert(hg_player_input_transitions(&e,0x5f0000)==0);
    assert(load(&t,0x5f04a4,1)==1 && load(&t,0x5f04a8,2)==40);
    assert(hg_player_input_transitions(&e,0x5f0000)==0);
    assert(load(&t,0x5f04a4,1)==1 && load(&t,0x5f04a8,2)==39);
    store(&t,0x3eaf1c,4,0);store(&t,0x3eaf04,2,0x20);
    assert(hg_player_input_transitions(&e,0x5f0000)==1);
    store(&t,0x5f0019,1,1);store(&t,0x5f001a,1,1);store(&t,0x5f0449,1,255);
    store(&t,0x3eaee8,1,0);store(&t,0x3f9e3f,1,0);store(&t,0x5f04ad,1,0);
    store(&t,0x3eaec8,2,32);store(&t,0x3eaed0,2,128);store(&t,0x3eaf04,2,0);
    hg_player_ordinary_update(&e,0x5f0000);assert(load(&t,0x5f0449,1)==255);
    store(&t,0x3eaf04,2,128);hg_player_ordinary_update(&e,0x5f0000);
    assert(load(&t,0x5f0449,1)==1);
    store(&t,0x3f9f38,4,0xaabbccdd);hg_player_apply_transition(&e,0x5f0000);
    assert(load(&t,0x3f9f38,4)==0xaabbcc00);
    hg_player_reset_transition(&e,0x5f0000);
    assert(load(&t,0x5f0449,1)==255 && load(&t,0x5f0019,1)==0);
    player_composition(&t,&e);
    free(t.ram);puts("PASS: synthetic M1-M7 workers and connected M8 player composition under sanitizers");return 0;
}
