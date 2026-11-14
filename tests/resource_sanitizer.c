/* Standalone synthetic integration under ASan/UBSan; contains no retail data. */
#include "resource_loader.h"
#include <assert.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
typedef struct { unsigned char *ram; unsigned char disk[6144]; unsigned ticks,reads,rejects; } Test;
static uint32_t load(void *u,uint32_t a,uint32_t n) {
    Test *t=u;assert(a<0x2000000 && n<=4 && a+n<=0x2000000);
    uint32_t v=0;for(uint32_t i=0;i<n;i++)v|=(uint32_t)t->ram[a+i]<<(8*i);return v;
}
static void store(void *u,uint32_t a,uint32_t n,uint32_t v) {
    Test *t=u;assert(a<0x2000000 && n<=4 && a+n<=0x2000000);
    for(uint32_t i=0;i<n;i++)t->ram[a+i]=(unsigned char)(v>>(8*i));
}
static uint32_t read32(void *u,uint32_t a) { return load(u,a,4); }
static int32_t sync_read(void *u,int32_t arg) { (void)u;assert(arg==1);return 0; }
static int32_t ready(void *u,int32_t arg) { (void)u;assert(arg==0 || arg==1);return 2; }
static int32_t error(void *u) { (void)u;return 0; }
static int32_t read_disc(void *u,uint32_t lba,uint32_t count,uint32_t dst,const uint8_t *mode) {
    Test *t=u;assert(mode[0]==0 && mode[1]==1 && mode[2]==0);
    if(t->rejects){t->rejects--;return 0;}
    assert(lba>=1000 && lba+count<=1003 && dst+count*2048u<0x2000000);
    memcpy(t->ram+dst,t->disk+(lba-1000)*2048,count*2048u);t->reads++;return 1;
}
static uint32_t external(void *u,uint32_t pc,uint32_t a,uint32_t b,uint32_t c,uint32_t d) {
    Test *t=u;(void)d;
    switch(pc) {
    case 0x119ac8:assert(a+c<0x2000000);memset(t->ram+a,(int)b,c);return a;
    case 0x103d60:assert(a==0x3f8e00);return 17;
    case 0x103d80:assert(a==17 && b==0);return 0;
    case 0x103da0:return 0;
    case 0x1434e0:assert(++t->ticks<100);return 0;
    case 0x137ed0:return a+32;
    case 0x1389d0:return 0;
    case 0x1041e0:assert(a==2);return 0;
    default:assert(!"Unexpected external operation");return 0;
    }
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
    free(t.ram);puts("PASS: synthetic request/worker/PACK/CDVD and AREA finalization under sanitizers");return 0;
}
