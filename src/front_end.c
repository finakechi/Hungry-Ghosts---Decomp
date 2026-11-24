/* M18 front-end services: fade, controller, title menu, memory-card scan and
 * pause menu of the original Japanese SCPS-15046. Original evidence:
 * analysis/front_end/functions.json (original bytes) and
 * tools/verify_front_end_differential.py (original instructions versus C).
 * Analyst reconstruction; no matching compiler is asserted. Field names are
 * inferred; addresses, widths and store order are the original's. */
#include "front_end.h"

static uint32_t r(HGResourceEnv *e,uint32_t a,uint32_t n) { return e->load(e->user,a,n); }
static void w(HGResourceEnv *e,uint32_t a,uint32_t n,uint32_t v) { e->store(e->user,a,n,v); }
static uint32_t call(HGResourceEnv *e,uint32_t pc,uint32_t a,uint32_t b,uint32_t c,uint32_t d) {
    return e->call(e->user,pc,a,b,c,d);
}
static uint32_t r32(HGResourceEnv *e,uint32_t a) { return r(e,a,4); }
static uint32_t r16(HGResourceEnv *e,uint32_t a) { return r(e,a,2); }
static uint32_t r8(HGResourceEnv *e,uint32_t a) { return r(e,a,1); }
static void w32(HGResourceEnv *e,uint32_t a,uint32_t v) { w(e,a,4,v); }
static void w16(HGResourceEnv *e,uint32_t a,uint32_t v) { w(e,a,2,v); }
static void w8(HGResourceEnv *e,uint32_t a,uint32_t v) { w(e,a,1,v); }
static uint32_t s16(uint32_t v) { return (v&0x8000u)?v|0xffff0000u:v&0xffffu; }
static uint32_t s8(uint32_t v) { return (v&0x80u)?v|0xffffff00u:v&0xffu; }

#define GP 0x3e7f70u
#define G(off) (GP+(uint32_t)(off))
#define MODE_PTR 0x700000c0u
#define FADE G(0x2f40)
#define LOADER_BUSY G(0x2d7c)
#define VOLUME G(0x2b08)
#define RENDER_CTX G(0x3098)
#define SAVE_FLAG 0x70000410u        /* title: save data available */
#define PAD 0x95e340u
#define PAD_EDGE 0x95e568u           /* PAD+0x228 */
#define PAD_HELD 0x95e564u           /* PAD+0x224 */
#define TITLE_ALPHA 0x3f9f60u
#define CARD 0x966500u               /* memory-card scan state (cleared by 00147f10) */
#define HG_DOMAIN_FAULT 0xfffffff0u
#define HG_GUEST_FRAME 0xfffffff1u
#define HG_CALL_T_REGS 0xfffffff5u
#define HG_CALL_FP_REGS 0xfffffffbu
#define HG_CALL_T3_REG 0xfffffffcu   /* host seam (M18): t3 of the next call */
#define F_128 0x43000000u
#define F_ONE 0x3f800000u
#define F_HALF 0x3f000000u
#define F_002 0x3ca3d70au

static uint32_t blk(HGResourceEnv *e) { return r32(e,MODE_PTR); }
static void inc8(HGResourceEnv *e,uint32_t off) {
    uint32_t b=blk(e);
    w8(e,b+off,r8(e,b+off)+1u);
}
static void domain_fault(HGResourceEnv *e,uint32_t code) { call(e,HG_DOMAIN_FAULT,code,0,0,0); }
static uint32_t fp(HGResourceEnv *e,uint32_t op,uint32_t a,uint32_t b,uint32_t code) {
    uint32_t fault,v=hg_ee_fp(op,a,b,&fault);
    if(fault)domain_fault(e,code);
    return v;
}
/* cvt.s.w for |v| < 2^24 (exact); larger magnitudes fail closed. */
static uint32_t cvt_sw(HGResourceEnv *e,uint32_t v,uint32_t code) {
    uint32_t sign=v&0x80000000u,m=sign?0u-v:v;
    if(m==0)return 0;
    if(m>=0x1000000u) {domain_fault(e,code);return 0;}
    uint32_t ex=150;
    while(m<0x800000u) {m<<=1;ex--;}
    return sign|(ex<<23)|(m&0x7fffffu);
}
/* mul.s by 127.0 then cvt.w.s, as at every 00156ad0 volume argument. */
static uint32_t volume_127(HGResourceEnv *e) {
    uint32_t bits=fp(e,2,0x42fe0000u,r32(e,VOLUME),0x90);
    uint32_t field=(bits>>23)&255u;
    if(field==0)return 0;
    if(field>=158u) {domain_fault(e,0x91);return 0;}
    uint32_t m=(bits&0x7fffffu)|0x800000u,v;
    if(field>=150u)v=m<<(field-150u);
    else v=(150u-field)>=32u?0u:m>>(150u-field);
    return (bits&0x80000000u)?0u-v:v;
}
static void sound(HGResourceEnv *e,uint32_t id) {
    uint32_t a2=volume_127(e);
    call(e,HG_CALL_T_REGS,1,0,0,0);
    call(e,0x156ad0,id,0x1000,a2,0x40);
}

/* ---- fade family --------------------------------------------------- */

/* 00138fa0 */
void hg_fade_reset(HGResourceEnv *e) {
    w8(e,FADE,0);w8(e,FADE+1,0);w8(e,FADE+3,0);w8(e,FADE+2,0);
    w16(e,FADE+4,0);w32(e,FADE+8,0);w32(e,FADE+0x10,0);w32(e,FADE+0xc,0);
}
/* 00138fd0: guard (a1) keeps the fade while [00a6bf95] is set. */
void hg_fade_clear(HGResourceEnv *e,uint32_t blend,uint32_t guard) {
    if(guard && r8(e,0xa6bf95))return;
    w8(e,FADE+2,blend);w8(e,FADE,0);w8(e,FADE+1,0);w8(e,FADE+3,0);w16(e,FADE+4,0);
}
/* 00139010: immediate black, kind 1. */
void hg_fade_black(HGResourceEnv *e,uint32_t blend,uint32_t layer) {
    w8(e,FADE+1,layer);w8(e,FADE,2);w8(e,FADE+2,blend);w8(e,FADE+3,1);w16(e,FADE+4,0xff);
}
/* 00139040: fade out; mode 1/2/3 select kinds 4/5/7, otherwise kind 3. */
void hg_fade_out(HGResourceEnv *e,uint32_t step,uint32_t blend,uint32_t mode,uint32_t layer) {
    w16(e,FADE+6,step);w8(e,FADE+1,layer);w8(e,FADE,3);
    if(mode==1) {w32(e,0x700000d4,1);w8(e,FADE+3,4);}
    else if(mode==2)w8(e,FADE+3,5);
    else if(mode==3)w8(e,FADE+3,7);
    else w8(e,FADE+3,3);
    w8(e,FADE+2,blend);
}
/* 001390b0: fade in, kind 2; guard (a3) as 00138fd0. */
void hg_fade_in(HGResourceEnv *e,uint32_t step,uint32_t blend,uint32_t layer,uint32_t guard) {
    if(guard && r8(e,0xa6bf95))return;
    w8(e,FADE,1);w8(e,FADE+1,layer);w16(e,FADE+6,step);w8(e,FADE+3,2);w8(e,FADE+2,blend);
}
/* (float)level * 64.0 / 128.0, stored at gp+2f4c. */
static void music_target(HGResourceEnv *e,uint32_t level) {
    uint32_t f=cvt_sw(e,level,0x92);
    f=fp(e,2,0x42800000u,f,0x93);
    w32(e,FADE+0xc,fp(e,8,f,F_128,0x94));
}
/* 001390f0 */
void hg_music_fade_2(HGResourceEnv *e,uint32_t level) {
    if(r32(e,FADE+8)&8u)return;
    w32(e,FADE+8,2);music_target(e,level);
}
/* 00139140 */
void hg_music_fade_1(HGResourceEnv *e,uint32_t level) {
    if(!r32(e,FADE+8))return;
    w32(e,FADE+8,1);music_target(e,level);
}
/* 00139190 */
void hg_music_fade_6(HGResourceEnv *e,uint32_t level) {
    if(r32(e,FADE+8)&8u) {w32(e,FADE+8,0xc);return;}
    w32(e,FADE+8,6);music_target(e,level);
}
/* 001391f0 */
void hg_music_fade_5(HGResourceEnv *e,uint32_t level) {
    if(!r32(e,FADE+8))return;
    w32(e,FADE+8,5);music_target(e,level);
}

/* The screen quad of 00139240: 14a0a0(layer,1,blend?3:4,0), the colour/
 * level block at sp+base, 14f370(layer,1,&c0,&c1,&c2,&c3,ctx+5b4,&level),
 * 14a0a0(layer,0,0,0). */
static void fade_draw(HGResourceEnv *e,uint32_t sp,uint32_t base,uint32_t layer) {
    uint32_t mode=s8(r8(e,FADE+2))?3u:4u;
    uint32_t level=s16(r16(e,FADE+4));
    call(e,0x14a0a0,layer,1,mode,0);
    uint32_t b=sp+base,f=cvt_sw(e,level,0x95),ctx=r32(e,RENDER_CTX);
    w32(e,b+0x1c,F_128);
    w32(e,b+0x10,f);w32(e,b+0x14,f);w32(e,b+0x18,f);
    w32(e,b,r32(e,ctx+0x3c0));
    w32(e,b+4,r32(e,ctx+0x3c4));
    w32(e,b+8,r32(e,ctx+0x3c8));
    w32(e,b+0xc,r32(e,ctx+0x3cc));
    call(e,HG_CALL_T_REGS,b+8,b+0xc,r32(e,RENDER_CTX)+0x5b4,0);
    call(e,HG_CALL_T3_REG,b+0x10,0,0,0);
    call(e,0x14f370,layer,1,b,b+4);
    call(e,0x14a0a0,layer,0,0,0);
}
static int fade_rise(HGResourceEnv *e) {          /* +4 += +6, 1 when it reached 0xff */
    w8(e,FADE,3);
    w16(e,FADE+4,s16(r16(e,FADE+4))+s16(r16(e,FADE+6)));
    if((int32_t)s16(r16(e,FADE+4))<0xff)return 0;
    w16(e,FADE+4,0xff);
    return 1;
}
/* 00139240: per-frame fade service (main-loop tail). */
void hg_fade_service(HGResourceEnv *e) {
    uint32_t sp=call(e,HG_GUEST_FRAME,0x90,1,0,0);
    uint32_t kind=s8(r8(e,FADE+3)),phase;
    switch(kind) {
    case 0:
        phase=r8(e,FADE);
        if(!phase)break;
        if(phase==2)call(e,0x139010,0,0,0,0);
        else if(phase==3)call(e,0x139040,4,0,0,0);
        else {w8(e,FADE,0);w16(e,FADE+4,0);}
        break;
    case 1:
        phase=r8(e,FADE);
        if(!phase)call(e,0x138fd0,0,0,0,0);
        else if(phase==1)call(e,0x1390b0,4,0,0,0);
        else {w8(e,FADE,2);w16(e,FADE+4,0xff);}
        break;
    case 2:
        if(r8(e,FADE)==2) {w8(e,FADE+3,3);break;}
        w8(e,FADE,1);
        w16(e,FADE+4,s16(r16(e,FADE+4))-s16(r16(e,FADE+6)));
        if((int32_t)s16(r16(e,FADE+4))>=0)break;
        w16(e,FADE+4,0);w8(e,FADE+3,0);w8(e,FADE,0);
        break;
    case 3:
        if(!r8(e,FADE)) {w8(e,FADE+3,2);break;}
        if(fade_rise(e)) {w8(e,FADE+3,1);w8(e,FADE,2);}
        break;
    case 4:
        if(fade_rise(e)) {w8(e,FADE+3,1);w8(e,FADE,2);w32(e,0x700000d4,0);}
        break;
    case 5: case 7:
        if(fade_rise(e)) {w8(e,FADE+3,6);w8(e,FADE,2);}
        /* kinds 5 and 7 then draw on layer 3 in their own block */
        /* falls through */
    case 6:
        fade_draw(e,sp,0x70,3);
        call(e,HG_GUEST_FRAME,0x90,0,0,0);
        return;
    default:
        break;
    }
    if(kind) {
        if(r8(e,FADE+1))fade_draw(e,sp,0x50,3);
        else fade_draw(e,sp,0x30,4);
    }
    call(e,HG_GUEST_FRAME,0x90,0,0,0);
}

/* ---- controller ---------------------------------------------------- */

/* 0013d910 */
void hg_pad_clear_buttons(HGResourceEnv *e,uint32_t p) {
    w16(e,p+0x224,0);w16(e,p+0x226,0);w16(e,p+0x228,0);w16(e,p+0x22a,0);w16(e,p+0x22c,0);
    w16(e,p+0x22e,1);w16(e,p+0x230,0);w16(e,p+0x232,0);w16(e,p+0x234,0);w16(e,p+0x236,0);
    w16(e,p+0x238,0);w16(e,p+0x23a,1);w32(e,p+0x23c,0);
    for(uint32_t o=0x200;o<=0x220;o+=4)w32(e,p+o,0);
}
/* 0013dad0 */
void hg_pad_clear_port(HGResourceEnv *e,uint32_t p) {
    w8(e,p+0x1a1,0);w32(e,p+0x1a4,0);w8(e,p+0x194,0);w8(e,p+0x195,0);
}
/* 0013e500: stick byte to a d-pad bit (axis 0: 0x8000 / 0x2000, axis 1: 0x1000 / 0x4000). */
uint32_t hg_pad_stick_dpad(HGResourceEnv *e,uint32_t value,uint32_t axis) {
    (void)e;
    uint32_t low=axis?0x1000u:0x8000u,high=axis?0x4000u:0x2000u,b=value&0xffu;
    if(b<0x10u)return low;
    return b>=0xe1u?high:0;
}
/* 0013e550: digital pressure from the 003db240 masks. */
void hg_pad_digital_pressure(HGResourceEnv *e,uint32_t p) {
    for(uint32_t i=0;i<16;i++)
        w32(e,p+0x1c0+i*4,(r16(e,p+0x224)&r32(e,0x3db240+i*4))?F_ONE:0);
}
/* 0013e8f0: pressure byte to [0,1] between low (f12) and high (f13). */
uint32_t hg_pad_pressure(HGResourceEnv *e,uint32_t value,uint32_t low,uint32_t high) {
    uint32_t f=fp(e,2,0x3b808081u,cvt_sw(e,value,0x96),0x97);
    if(fp(e,3,f,low,0x98))return 0;
    uint32_t num=fp(e,1,f,low,0x99),den=fp(e,1,high,low,0x9a);
    uint32_t v=fp(e,2,num,fp(e,8,F_ONE,den,0x9b),0x9c);
    return fp(e,4,v,F_ONE,0x9d)?v:F_ONE;
}
static void repeat_group(HGResourceEnv *e,uint32_t p,uint32_t held,uint32_t prev,uint32_t edge,
                         uint32_t rep,uint32_t timer,uint32_t rate) {
    uint32_t a=r16(e,p+held)&0xf00fu,b=r16(e,p+prev)&0xf00fu;
    if(a!=b || !a) {
        w16(e,p+timer,0);w16(e,p+rate,1);w16(e,p+rep,r16(e,p+edge));
        return;
    }
    w16(e,p+timer,s16(r16(e,p+timer))+s16(r16(e,p+rate)));
    if((int32_t)s16(r16(e,p+timer))<0x21) {w16(e,p+rep,r16(e,p+edge)&0x0ff0u);return;}
    w16(e,p+rep,r16(e,p+held)&0xf00fu);
    w16(e,p+rep,r16(e,p+rep)|(r16(e,p+edge)&0x0ff0u));
    w16(e,p+timer,s16(r16(e,p+timer))-0x20u);
    w16(e,p+rate,s16(r16(e,p+rate))+1u);
    if((int32_t)s16(r16(e,p+rate))>=8)w16(e,p+rate,7);
}
/* 0013e090: read one port (libpad 0010db90 into the guest frame at sp+60)
 * and derive held, edge and repeat buttons. analog selects the stick path. */
uint32_t hg_pad_read(HGResourceEnv *e,uint32_t p,uint32_t analog) {
    uint32_t sp=call(e,HG_GUEST_FRAME,0x80,1,0,0),buf=sp+0x60,result=0;
    if(!call(e,0x10db90,r32(e,p+0x100),r32(e,p+0x104),buf,0))goto done;
    if(r8(e,buf))goto done;
    w16(e,p+0x226,r16(e,p+0x224));
    uint32_t held=~((r8(e,buf+2)<<8)|r8(e,buf+3));
    w16(e,p+0x224,held);
    w16(e,p+0x232,r16(e,p+0x230));
    w16(e,p+0x230,held);
    if(analog==1) {
        if(r8(e,p+0x10b)) {
            for(uint32_t i=0;i<16;i++) {
                uint32_t lo=r32(e,p+0x10c+i*8),hi=r32(e,p+0x110+i*8);
                call(e,HG_CALL_FP_REGS,lo,hi,0,0);
                w32(e,p+0x1c0+i*4,call(e,0x13e8f0,r8(e,buf+4+i),0,0,0));
            }
        } else call(e,0x13e550,p,0,0,0);
        call(e,HG_CALL_FP_REGS,cvt_sw(e,r8(e,buf+6),0x9e),cvt_sw(e,r8(e,buf+7),0x9e),0,0);
        call(e,0x13e5b0,p,0,0,0);
        call(e,HG_CALL_FP_REGS,cvt_sw(e,r8(e,buf+4),0x9e),cvt_sw(e,r8(e,buf+5),0x9e),0,0);
        call(e,0x13e5b0,p,1,0,0);
        if(!r32(e,p+0x100)) {
            if(!fp(e,3,r32(e,p+0x208),F_HALF,0x9f)) {
                w16(e,p+0x224,r16(e,p+0x224)&0xfffu);
                uint32_t v=call(e,0x13e500,r8(e,buf+6),0,0,0);
                w16(e,p+0x224,r16(e,p+0x224)|v);
                v=call(e,0x13e500,r8(e,buf+7),1,0,0);
                w16(e,p+0x224,r16(e,p+0x224)|v);
            }
            if(!r32(e,p+0x23c))w16(e,p+0x230,0);
        }
    } else {
        call(e,0x13e550,p,0,0,0);
        static const uint16_t z[9]={0x21c,0x218,0x214,0x210,0x20c,0x208,0x204,0x200,0x220};
        for(uint32_t i=0;i<9;i++)w32(e,p+z[i],0);
    }
    w16(e,p+0x234,~r16(e,p+0x232)&r16(e,p+0x230));
    w16(e,p+0x228,~r16(e,p+0x226)&r16(e,p+0x224));
    repeat_group(e,p,0x224,0x226,0x228,0x22a,0x22c,0x22e);
    repeat_group(e,p,0x230,0x232,0x234,0x236,0x238,0x23a);
    w8(e,p+0x1a3,r8(e,buf+1)>>4);
    result=1;
done:
    call(e,HG_GUEST_FRAME,0x80,0,0,0);
    return result;
}
static void port_reset(HGResourceEnv *e,uint32_t p) {
    w8(e,p+0x10a,0);
    call(e,0x13dad0,p,0,0,0);
    w8(e,p+0x10b,0);
    w8(e,p+0x1a0,0);
}
/* 0013dc30: libpad port state machine (11 states, table 003db210). */
void hg_pad_port(HGResourceEnv *e,uint32_t p) {
    uint32_t port=r32(e,p+0x100),slot=r32(e,p+0x104),ok=0,v,x;
    uint32_t st=call(e,0x10dcf8,port,slot,0,0);
    if(!st)port_reset(e,p);
    switch(r8(e,p+0x10a)) {
    case 0:
        if(st!=6 && st!=2)break;
        x=call(e,0x10e0d8,port,slot,1,0);
        if(!x)break;
        v=call(e,0x10e0d8,port,slot,2,0);
        if((int32_t)v>0)x=v;
        w16(e,p+0x108,x);
        v=r16(e,p+0x108);
        if(v==7)w8(e,p+0x10a,5);
        else if(v==4)w8(e,p+0x10a,2);
        else {w8(e,p+0x10a,1);w8(e,p+0x1a0,0);w8(e,p+0x10b,0);}
        break;
    case 1:
        if(st==7) {port_reset(e,p);break;}
        if(st!=2 && st!=6)break;
        if(call(e,0x13e090,p,st==6,0,0))ok=1;
        if(r8(e,p+0x1a3)==r16(e,p+0x108))break;
        port_reset(e,p);ok=0;
        break;
    case 2:
        if(st!=6 && st!=2)break;
        if(!call(e,0x10e0d8,port,slot,4,0xffffffffu)) {w8(e,p+0x10a,1);w8(e,p+0x1a0,0);w8(e,p+0x10b,0);}
        else w8(e,p+0x10a,3);
        break;
    case 3:
        if(st!=6)break;
        if(call(e,0x10e210,port,slot,1,3)==1)w8(e,p+0x10a,4);
        break;
    case 4:
        if(call(e,0x10de10,port,slot,0,0)==1)w8(e,p+0x10a,3);
        if(!call(e,0x10de10,port,slot,0,0)) {w8(e,p+0x10a,0);w8(e,p+0x10b,1);}
        break;
    case 5:
        if(!r8(e,p+0x10b)) {w8(e,p+0x10a,3);break;}
        if(st!=6)break;
        w8(e,p+0x10a,call(e,0x10de98,port,slot,0xffffffffu,0)?6:1);
        break;
    case 6:
        if(st!=6)break;
        if(call(e,0x10e388,port,slot,p+0x19a,0)==1)w8(e,p+0x10a,7);
        break;
    case 7:
        if(call(e,0x10de10,port,slot,0,0)==1)w8(e,p+0x10a,6);
        if(!call(e,0x10de10,port,slot,0,0))w8(e,p+0x10a,8);
        break;
    case 8:
        if(st!=6)break;
        if(call(e,0x10e5c8,port,slot,0,0)!=1) {w8(e,p+0x10a,1);w8(e,p+0x1a0,1);}
        else w8(e,p+0x10a,9);
        break;
    case 9:
        if(st!=6)break;
        if(call(e,0x10e628,port,slot,0,0)==1)w8(e,p+0x10a,10);
        break;
    case 10:
        if(call(e,0x10de10,port,slot,0,0)==1)w8(e,p+0x10a,9);
        if(!call(e,0x10de10,port,slot,0,0)) {w8(e,p+0x10a,1);w8(e,p+0x1a0,1);}
        break;
    default:
        break;
    }
    if(!ok)call(e,0x13d910,p,0,0,0);
}
/* 0013d970: per-frame controller service (main-loop head). */
void hg_pad_service(HGResourceEnv *e) {
    call(e,0x13dc30,PAD,0,0,0);
    static const uint16_t h[6][2]={{0x224,0x2f90},{0x226,0x2f92},{0x228,0x2f94},{0x22a,0x2f96},
                                   {0x234,0x2f98},{0x236,0x2f9a}};
    for(uint32_t i=0;i<6;i++)w16(e,G(h[i][1]),r16(e,PAD+h[i][0]));
    static const uint16_t f[11][2]={{0x200,0x2f9c},{0x204,0x2fa0},{0x208,0x2fa4},{0x20c,0x2fa8},
                                    {0x210,0x2fac},{0x214,0x2fb0},{0x218,0x2fb4},{0x21c,0x2fb8},
                                    {0x220,0x2fbc},{0x1fc,0x2fc0},{0x1f8,0x2fc4}};
    for(uint32_t i=0;i<11;i++)w32(e,G(f[i][1]),r32(e,PAD+f[i][0]));
    uint32_t y=r32(e,PAD+0x204);
    w32(e,G(0x2fc8),r8(e,0x3fa1b8)?y^0x80000000u:y);
}

/* ---- new-game state -------------------------------------------------- */

/* 001418b0: install the button configuration (4 u16 at src) at gp+2f60/5c/58/54. */
void hg_controls_apply(HGResourceEnv *e,uint32_t src) {
    w16(e,G(0x2f60),r16(e,src));w16(e,G(0x2f5c),r16(e,src+2));
    w16(e,G(0x2f58),r16(e,src+4));w16(e,G(0x2f54),r16(e,src+6));
}
/* 00141810: copy the option settings gp+2f20.. into the game state and
 * continue (j, not a call) into 001418b0 with 003fa1bc. */
void hg_options_to_game(HGResourceEnv *e) {
    uint32_t c0=r16(e,G(0x2f20)),c1=r16(e,G(0x2f22)),c2=r16(e,G(0x2f24)),c3=r16(e,G(0x2f26));
    w16(e,0x3fa1bc,c0);w16(e,0x3fa1be,c1);w16(e,0x3fa1c0,c2);w16(e,0x3fa1c2,c3);
    uint32_t b29=r8(e,G(0x2f29)),b2c=r8(e,G(0x2f2c)),h30=r16(e,G(0x2f30)),h32=r16(e,G(0x2f32));
    uint32_t b2a=r8(e,G(0x2f2a)),b2d=r8(e,G(0x2f2d));
    w8(e,0x3fa1b9,b29);w8(e,0x3fa1cc,b2c);w16(e,0x3fa208,h30);w16(e,0x3fa20a,h32);
    uint32_t b2e=r8(e,G(0x2f2e));
    w8(e,0x3fa1cf,b2a);
    uint32_t b2f=r8(e,G(0x2f2f));
    w8(e,0x3fa1cd,b2d);
    uint32_t b36=r8(e,G(0x2f36));
    w8(e,0x3fa1ce,b2e);w8(e,0x3fa205,b2f);w8(e,0x3fa1b8,b36);
    hg_controls_apply(e,0x3fa1bc);
}
/* 00141980: new-game state. Clears 003fa180..+73fc0 (00119ac8), area triple
 * (1,0,0), empty name (0011af00 with the empty format 003db280), default
 * flags and counters, four 0011f0c0 setups, then the option copy 00141810. */
void hg_new_game_state(HGResourceEnv *e) {
    call(e,0x119ac8,0x3fa180,0,0x73fc0,0);
    w8(e,0x3fa1b0,1);
    call(e,0x11af00,0x3fa194,0x3db280,0,0);
    w8(e,0x3fa200,0);w8(e,0x3fa201,0);w8(e,0x46e0f1,1);w8(e,0x3fa202,0xff);
    w8(e,0x46e12d,0);w8(e,0x3fa1c8,0x1e);w32(e,0x46d90c,0xffffffffu);
    w8(e,0x46e12e,0);w8(e,0x46e12f,0);
    static const uint32_t ff[]={0x3fa203,0x3fa204,0x46d91a,0x46d890,0x46d891,0x46d892,0x3fa206,
                                0x46d924,0x46d925,0x46d919,0x46d926,0x46d927};
    for(uint32_t i=0;i<sizeof ff/sizeof ff[0];i++)w8(e,ff[i],0xff);
    w32(e,0x3fa1d0,0xa0);w32(e,0x3fa1d4,0xa0);w32(e,0x3fa1d8,0x32);w32(e,0x3fa1dc,0x32);
    w32(e,0x3fa1ac,1);
    call(e,0x11f0c0,0,0,1,2);
    call(e,0x11f0c0,1,0,1,2);
    call(e,0x11f0c0,0,3,1,2);
    call(e,0x11f0c0,1,3,1,2);
    call(e,0x141810,0,0,0,0);
}

/* ---- title ---------------------------------------------------------- */

/* 00147f10: clear the 0x490-byte menu/card block and the render quad. */
void hg_menu_clear(HGResourceEnv *e) {
    call(e,0x119ac8,CARD,0,0x490,0);
    for(uint32_t o=0x3f0;o<0x400;o+=4)w32(e,r32(e,RENDER_CTX)+o,0);
}
/* 002cf020: title memory-card scan. 0 busy, 1 save data found, 2 none.
 * sceMc wrappers 001783c0 (sync), 001782a0 (get info), 00178270 (open) and
 * 0011af00 (sprintf) are bus calls (IOP boundary). */
uint32_t hg_title_card_scan(HGResourceEnv *e) {
    uint32_t sp=call(e,HG_GUEST_FRAME,0x120,1,0,0),s0=0,v;
    uint32_t st=s16(r16(e,CARD));
    w16(e,CARD+0x14,1);
    if(st==0) {
        if(call(e,0x1783c0,1,0,0,0)!=0xffffd8f0u)w16(e,CARD,s16(r16(e,CARD))+1u);
    } else if(st==1) {
        uint32_t sub=s16(r16(e,CARD+2));
        if(sub==0) {
            call(e,HG_CALL_T_REGS,0,0,0,0);
            v=call(e,0x1782a0,0,0,CARD+0x18,0);
            if(v==0)w16(e,CARD+2,s16(r16(e,CARD+2))+1u);
            else if(v!=3)s0=2;
        } else if(sub==1) {
            v=call(e,0x1783c0,1,0,0,0);
            if(v==0xffffffffu || v==0) {
                if(r32(e,CARD+0x18)!=2)s0=2;
                else {w16(e,CARD+2,0);w16(e,CARD,s16(r16(e,CARD))+1u);}
            } else if(v!=0xffffd8f0u)s0=2;
        }
    } else if(st==2) {
        uint32_t sub=s16(r16(e,CARD+2));
        if(sub==0) {w16(e,CARD+2,1);w16(e,CARD+4,0);w16(e,CARD+6,0);sub=1;}
        if(sub==1) {
            uint32_t x=s16(r16(e,CARD+4));
            if(x==0) {
                call(e,HG_CALL_T_REGS,s16(r16(e,CARD+6)),0,0,0);
                call(e,0x11af00,sp+0x20,0x3de8d8,0x3de8c0,0x3de8c8);
                call(e,HG_CALL_T_REGS,1,0x9664c0,0,0);
                v=call(e,0x178270,0,0,sp+0x20,0);
                if(v==0)w16(e,CARD+4,s16(r16(e,CARD+4))+1u);
                else if(v!=3)s0=2;
            } else if(x==1) {
                v=call(e,0x1783c0,1,0,0,0);
                if(v==0xffffd8eeu)s0=2;
                else if(v==0xfffffffcu || v==0) {
                    w16(e,CARD+6,s16(r16(e,CARD+6))+1u);
                    if((int32_t)s16(r16(e,CARD+6))<3)w16(e,CARD+4,0);
                    else s0=2;
                } else if(v==1)s0=1;
                else if(v!=0xffffd8f0u)s0=2;
            }
        }
    }
    call(e,HG_GUEST_FRAME,0x120,0,0,0);
    return s0;
}
/* 00a87de0 (overlay A0_B09): title-screen memory-card poll, state = mode +b.
 * 0: clear (00147f10) and scan; 1: scan until 002cf020 reports, then set the
 * save flag 70000410 (1 only for "save data found") and a 15-frame wait at
 * mode +1e; 2: count the wait down (unless hold) and rescan. Returns 1 in
 * state 2: the title may then complete its transitions. */
uint32_t hg_title_card_poll(HGResourceEnv *e,uint32_t hold) {
    uint32_t b=blk(e),st=r8(e,b+0xb);
    if(st==2) {
        if(hold)return 1;
        uint32_t t=(r16(e,b+0x1e)-1u)&0xffffu;
        w16(e,b+0x1e,t);
        if(!t)w8(e,blk(e)+0xb,0);
        return 1;
    }
    if(st==0) {w8(e,b+0xb,1);call(e,0x147f10,0,0,0,0);}
    else if(st!=1)return 0;
    uint32_t v=call(e,0x2cf020,0,0,0,0);
    if(!v)return 0;
    w32(e,SAVE_FLAG,v==1?1u:0u);
    w16(e,blk(e)+0x1e,0xf);
    b=blk(e);
    w8(e,b+0xb,r8(e,b+0xb)+1u);
    return 0;
}
/* 00a87880 (overlay A0_B09): title presentation and menu, minor = mode +9.
 * Returns 1 when a choice (mode +11) is confirmed, 3 on the attract timeout.
 * 00a87de0 is the C card poll below; the overlay draw/scene helpers 00a87ed0
 * and 00a88660 are bus calls. */
uint32_t hg_title_menu(HGResourceEnv *e) {
    uint32_t b=blk(e),minor=r8(e,b+9),s0;
    switch(minor) {
    case 0:
        call(e,0x139010,0,0,0,0);
        call(e,0x1413a0,0,0,0,0);
        call(e,0x1404f0,0,0,0,0);
        inc8(e,9);
        w16(e,blk(e)+0x1a,1);
        call(e,0xa88660,r8(e,blk(e)+0x12)?1u:0u,0,0,0);
        return 0;
    case 1:
        call(e,0x141470,0,0,0,0);
        b=blk(e);
        if(r16(e,b+0x1a))return 0;
        w32(e,TITLE_ALPHA,0);w32(e,0x3f9f34,0);w32(e,0x3f9f7c,0);
        w16(e,b+0x1c,0x4b0);
        inc8(e,9);
        w8(e,blk(e)+0x13,0);
        call(e,0x156550,1,0,0,0);
        call(e,HG_CALL_FP_REGS,F_ONE,F_002,0,0);
        call(e,0x1564a0,1,0x416,0,0);
        return 0;
    case 2: {
        call(e,0xa87de0,0,0,0,0);
        call(e,0x141470,0,0,0,0);
        uint32_t last=(r32(e,G(0x2ef4))&1u)?3u:2u;
        if(fp(e,5,F_128,r32(e,TITLE_ALPHA),0xa0)) {
            uint32_t pad=r16(e,PAD_EDGE),p;
            if(pad&0x1000u) {
                p=blk(e)+0x11;
                uint32_t v=r8(e,p);
                if(!v)w8(e,p,0);
                else {w8(e,p,v-1u);sound(e,4);}
                if(!r32(e,SAVE_FLAG) && r8(e,blk(e)+0x11)==1)w8(e,blk(e)+0x11,0);
            } else if(pad&0x4000u) {
                p=blk(e)+0x11;
                uint32_t v=r8(e,p);
                if(v==last)w8(e,p,last);
                else {w8(e,p,v+1u);sound(e,4);}
                if(!r32(e,SAVE_FLAG) && r8(e,blk(e)+0x11)==1)w8(e,blk(e)+0x11,2);
            }
        }
        if(!r32(e,SAVE_FLAG) && r8(e,blk(e)+0x11)==1)w8(e,blk(e)+0x11,0);
        if(!r16(e,PAD_HELD)) {
            b=blk(e);
            uint32_t t=r16(e,b+0x1c);
            w16(e,b+0x1c,t-1u);
            if(!t) {w8(e,blk(e)+9,4);call(e,0x139040,8,0,0,0);}
        } else {
            b=blk(e);
            w8(e,b+0x13,1);
            w16(e,blk(e)+0x1c,0x4b0);
            if(fp(e,5,F_128,r32(e,TITLE_ALPHA),0xa1) && (r16(e,PAD_EDGE)&0x820u)) {
                call(e,0x156550,1,0,0,0);
                call(e,HG_CALL_FP_REGS,F_ONE,F_002,0,0);
                call(e,0x1564a0,1,0x243,0,0);
                sound(e,5);
                b=blk(e);
                w32(e,TITLE_ALPHA,F_128);w32(e,0x3f9f34,F_128);w32(e,0x3f9f7c,F_128);
                if(!r8(e,b+0x11)) {
                    w8(e,b+9,3);
                    w16(e,blk(e)+0x1a,1);
                    call(e,0x196ad0,0x168,0,0,0);
                } else {
                    w8(e,b+9,3);
                    call(e,0x139040,8,0,0,0);
                }
            }
        }
        call(e,0xa87ed0,0,0,0,0);
        return 0;
    }
    case 3:
        s0=call(e,0xa87de0,1,0,0,0);
        call(e,0x141470,0,0,0,0);
        call(e,0xa87ed0,1,0,0,0);
        if(r8(e,FADE)==2 && s0)return 1;
        if(!r32(e,SAVE_FLAG))return 0;
        if(r16(e,PAD_EDGE)&0x820u)call(e,0x139040,8,0,0,0);
        return 0;
    case 4:
        s0=call(e,0xa87de0,1,0,0,0);
        call(e,0x141470,0,0,0,0);
        call(e,0xa87ed0,1,0,0,0);
        return (r8(e,FADE)==2 && s0)?3:0;
    default:
        return 0;
    }
}

/* ---- pause menu ------------------------------------------------------ */

/* 00161cf0: Up/Down cursor at the u16 cell, bounded by 0 and limit. */
void hg_menu_cursor(HGResourceEnv *e,uint32_t cell,uint32_t limit) {
    uint32_t pad=r16(e,G(0x2f94));
    if(pad&0x1000u) {
        if(!r16(e,cell))return;
        sound(e,0x50);
        w16(e,cell,r16(e,cell)-1u);
    } else if(pad&0x4000u) {
        if((int32_t)r16(e,cell)>=(int32_t)(limit&0xffffu))return;
        sound(e,0x50);
        w16(e,cell,r16(e,cell)+1u);
    }
}
static void pause_fade(HGResourceEnv *e,uint32_t next) {   /* +c 2, +14 next, fade out */
    w8(e,blk(e)+0xc,2);
    w8(e,blk(e)+0x14,next);
    call(e,0x139040,8,0,0,0);
}
static void pause_music(HGResourceEnv *e) {
    call(e,0x156d50,F_002,0,2,0);
}
/* 00162750: pause menu, state = mode +c (table 003db630). Returns 1 to
 * resume, 3 to quit, 2 from the data-load screen, 0 otherwise. States not
 * on the reconstructed resume/quit paths fail closed (domain fault 0xa8+). */
uint32_t hg_pause_menu(HGResourceEnv *e) {
    uint32_t b=blk(e),st=r8(e,b+0xc),pad;
    if(st>=0x12)return 0;
    switch(st) {
    case 0:
        call(e,0x166770,0,0,0,0);
        inc8(e,0xc);
        w8(e,blk(e)+0xd,0);w8(e,blk(e)+0xe,0);w16(e,blk(e)+0x20,0);
        w8(e,blk(e)+0x14,0);w8(e,blk(e)+0x17,0);
        /* falls through */
    case 1: {
        uint32_t rep=r16(e,G(0x2f96)),p,v;
        if(rep&0x1000u) {
            p=blk(e)+0x20;v=r16(e,p);
            w16(e,p,v?v-1u:3u);
            sound(e,0x50);
        } else if(rep&0x4000u) {
            p=blk(e)+0x20;v=r16(e,p);
            w16(e,p,(int32_t)v<3?v+1u:0u);
            sound(e,0x50);
        }
        call(e,0x163350,r16(e,blk(e)+0x20),0,0,0);
        pad=r16(e,G(0x2f94));
        if(pad&0x20u) {
            switch(r16(e,blk(e)+0x20)) {
            case 0:
                pause_fade(e,0x11);
                sound(e,0x52);
                return 0;
            case 1:
                pause_music(e);
                w8(e,blk(e)+0xc,2);w8(e,blk(e)+0x14,3);w16(e,blk(e)+0x22,0);
                call(e,0x139040,8,0,0,0);
                sound(e,0x51);
                return 0;
            case 2:
                pause_music(e);
                call(e,0x147f10,0,0,0,0);
                w8(e,blk(e)+0xc,2);w8(e,blk(e)+0x14,5);
                call(e,0x130150,1,0x69,0,0);
                call(e,0x139040,8,0,0,0);
                sound(e,0x56);
                return 0;
            case 3:
                pause_music(e);
                w8(e,blk(e)+0xc,2);w8(e,blk(e)+0x14,4);w16(e,blk(e)+0x22,1);
                call(e,0x139040,8,0,0,0);
                sound(e,0x52);
                return 0;
            default:
                return 0;
            }
        }
        if(pad&0x840u) {
            sound(e,0x52);
            pause_fade(e,0x11);
        }
        return 0;
    }
    case 2:
        call(e,0x163350,r16(e,b+0x20),0,0,0);
        if(r8(e,FADE)!=2 || r8(e,LOADER_BUSY))return 0;
        b=blk(e);
        w8(e,b+0xc,r8(e,b+0x14));
        call(e,0x1390b0,8,0,0,0);
        return 0;
    case 4:
        call(e,0x161cf0,b+0x22,1,0,0);
        call(e,0x1635b0,r16(e,blk(e)+0x22),0,0,0);
        pad=r16(e,G(0x2f94));
        if(pad&0x20u) {
            w32(e,blk(e)+0x28,0x1635b0);
            call(e,0x139040,8,0,0,0);
            b=blk(e);
            if(!r16(e,b+0x22)) {w8(e,b+0xc,7);sound(e,0x52);}
            else {w8(e,b+0xc,6);sound(e,0x51);}
        } else if(pad&0x40u) {
            sound(e,0x46);
            w32(e,blk(e)+0x28,0x1635b0);
            call(e,0x139040,8,0,0,0);
            w8(e,blk(e)+0xc,6);
        }
        return 0;
    case 6: case 7:
        call(e,r32(e,b+0x28),r16(e,b+0x22),0,0,0);
        if(r8(e,FADE)!=2)return 0;
        b=blk(e);
        if(r8(e,b+0xc)!=6)return 3;
        w8(e,b+0xc,1);
        call(e,0x1390b0,8,0,0,0);
        return 0;
    case 0x11:
        return 1;
    default:
        domain_fault(e,0xa8u+st);
        return 0;
    }
}

int hg_front_end_call(HGResourceEnv *e,uint32_t pc,uint32_t a,uint32_t b,uint32_t c,uint32_t d,uint32_t *result) {
    *result=0;
    switch(pc) {
    case 0x138fa0:hg_fade_reset(e);return 1;
    case 0x138fd0:hg_fade_clear(e,a,b);return 1;
    case 0x139010:hg_fade_black(e,a,b);return 1;
    case 0x139040:hg_fade_out(e,a,b,c,d);return 1;
    case 0x1390b0:hg_fade_in(e,a,b,c,d);return 1;
    case 0x1390f0:hg_music_fade_2(e,a);return 1;
    case 0x139140:hg_music_fade_1(e,a);return 1;
    case 0x139190:hg_music_fade_6(e,a);return 1;
    case 0x1391f0:hg_music_fade_5(e,a);return 1;
    case 0x139240:hg_fade_service(e);return 1;
    case 0x13d970:hg_pad_service(e);return 1;
    case 0x13dc30:hg_pad_port(e,a);return 1;
    case 0x13e090:*result=hg_pad_read(e,a,b);return 1;
    case 0x13e500:*result=hg_pad_stick_dpad(e,a,b);return 1;
    case 0x13e550:hg_pad_digital_pressure(e,a);return 1;
    case 0x13d910:hg_pad_clear_buttons(e,a);return 1;
    case 0x13dad0:hg_pad_clear_port(e,a);return 1;
    case 0x147f10:hg_menu_clear(e);return 1;
    case 0x141980:hg_new_game_state(e);return 1;
    case 0x141810:hg_options_to_game(e);return 1;
    case 0x1418b0:hg_controls_apply(e,a);return 1;
    case 0x2cf020:*result=hg_title_card_scan(e);return 1;
    case 0xa87880:*result=hg_title_menu(e);return 1;
    case 0xa87de0:*result=hg_title_card_poll(e,a);return 1;
    case 0x162750:*result=hg_pause_menu(e);return 1;
    case 0x161cf0:hg_menu_cursor(e,a,b);return 1;
    default:return 0;
    }
}
