/* M17 game lifecycle layer: main loop, mode dispatch, gameplay mode, area
 * load and active-play state machines of the original Japanese SCPS-15046.
 * Original evidence: analysis/game_lifecycle/functions.json (original bytes)
 * and tools/verify_lifecycle_differential.py (original-instruction versus C
 * differential). Analyst reconstruction; no matching compiler is asserted.
 * Field names are inferred; addresses and widths are the original's. */
#include "game_lifecycle.h"

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
static void copy_quad(HGResourceEnv *e,uint32_t dst,uint32_t src) {
    /* lq/sq pair: one aligned 128-bit quadword, read before it is written. */
    uint32_t v[4];
    for(uint32_t i=0;i<4;i++)v[i]=r32(e,src+i*4u);
    for(uint32_t i=0;i<4;i++)w32(e,dst+i*4u,v[i]);
}

#define GP 0x3e7f70u
#define G(off) (GP+(uint32_t)(off))
#define MODE_PTR 0x700000c0u     /* scratch pointer to the current mode block */
#define MODE_BLOCK G(0x2dc0)     /* 003ead30 */
#define FLAG_CC 0x700000ccu      /* gameplay entry kind */
#define LOADER_BUSY G(0x2d7c)    /* 003eacec, nonzero while the loader works */
#define FADE_STATE G(0x2f40)     /* 003eaeb0, 2 when a fade has completed */
#define VOLUME G(0x2b08)         /* 003eaa78, float in [0,1] */
#define RENDER_CTX G(0x3098)     /* 003eb008 */
#define PLAYER 0x3f92d0u
#define CAMERA 0x3f9860u
#define HG_DOMAIN_FAULT 0xfffffff0u
#define HG_GUEST_FRAME 0xfffffff1u
#define HG_CALL_T_REGS 0xfffffff5u
#define HG_CALL_FP_REGS 0xfffffffbu  /* host seam: f12, f13, f14 of the next call */

static uint32_t blk(HGResourceEnv *e) { return r32(e,MODE_PTR); }
static void set8(HGResourceEnv *e,uint32_t off,uint32_t v) { w8(e,blk(e)+off,v); }
static void inc8(HGResourceEnv *e,uint32_t off) {
    uint32_t b=blk(e);
    w8(e,b+off,r8(e,b+off)+1u);
}
static void domain_fault(HGResourceEnv *e,uint32_t code) { call(e,HG_DOMAIN_FAULT,code,0,0,0); }

/* mul.s by 127.0 then cvt.w.s (truncation toward zero), as at every
 * 00156ad0 volume argument. Non-finite or out-of-range values fail closed. */
static uint32_t volume_127(HGResourceEnv *e) {
    uint32_t fault,bits=hg_ee_fp(2,0x42fe0000u,r32(e,VOLUME),&fault);
    if(fault) {domain_fault(e,0x80);return 0;}
    uint32_t field=(bits>>23)&255u;
    if(field==0)return 0;
    if(field>=158u) {domain_fault(e,0x81);return 0;}
    uint32_t m=(bits&0x7fffffu)|0x800000u,v;
    if(field>=150u)v=m<<(field-150u);
    else v=(150u-field)>=32u?0u:m>>(150u-field);
    return (bits&0x80000000u)?0u-v:v;
}
static void sound(HGResourceEnv *e,uint32_t id) {
    uint32_t a2=volume_127(e);
    call(e,HG_CALL_T_REGS,1,0,0,0);              /* t0 = 1 at every call site */
    call(e,0x156ad0,id,0x1000,a2,0x40);
}
static void render_reset(HGResourceEnv *e) {     /* lq 003db290 -> [gp+3098]+3f0 */
    copy_quad(e,r32(e,RENDER_CTX)+0x3f0,0x3db290);
}

/* 00143300 */
void hg_mode_set(HGResourceEnv *e,uint32_t handler) {
    w8(e,blk(e),1);
    w32(e,blk(e)+4,handler);
    uint32_t b=blk(e);
    for(uint32_t off=8;off<0x28;off+=4)w32(e,b+off,0);
}
/* 001432c0: the same layout written directly at gp+2dc0. */
void hg_mode_init(HGResourceEnv *e,uint32_t handler) {
    w32(e,MODE_BLOCK+4,handler);
    w8(e,MODE_BLOCK,1);
    for(uint32_t off=8;off<0x28;off+=4)w32(e,MODE_BLOCK+off,0);
}
/* 00143240 */
void hg_mode_dispatch(HGResourceEnv *e) {
    w32(e,MODE_PTR,MODE_BLOCK);
    uint32_t b=blk(e),state=r8(e,b);
    if(state==1 || state==4)w8(e,b,2);
    else if(state!=2)return;
    call(e,r32(e,blk(e)+4),0,0,0,0);
}

/* 0012d850 */
void hg_gameplay_reset_services(HGResourceEnv *e) {
    call(e,0x25c140,0,0,0,0);
    call(e,0x182430,0,0,0,0);
    w8(e,G(0x2f78),0);
    w8(e,G(0x2f74),0);
    w8(e,0x46e10c,0);
}
/* 0012d7d0 */
uint32_t hg_gameplay_enter_step(HGResourceEnv *e) {
    uint32_t sub=r8(e,blk(e)+0xa);
    if(sub==0) {call(e,0x130170,1,9,0,0);inc8(e,0xa);return 0;}
    if(sub==1)return r8(e,LOADER_BUSY)?0:4;
    return 0;
}
/* 0012d680 */
uint32_t hg_gameplay_title_step(HGResourceEnv *e) {
    uint32_t b=blk(e),minor=r8(e,b+9),sub=r8(e,b+0xa);
    if(minor==0) {
        if(sub==0) {
            w8(e,0x3fa194,0);
            call(e,0x130150,1,0x6c,0,0);
            inc8(e,0xa);
        } else if(sub==1 && !r8(e,LOADER_BUSY)) {
            w8(e,b+9,minor+1);
            set8(e,0xa,0);
            call(e,0x1385b0,r32(e,0x3f450c),0x1e00,0,0);
            call(e,0x1385b0,r32(e,0x3f4508),0x1e00,0,0);
        }
        return 0;
    }
    if(minor!=1)return 0;
    if(sub==0) {call(e,0x147f10,0,0,0,0);inc8(e,0xa);}
    else if(sub!=1)return 0;
    uint32_t v=call(e,0x2cf330,0,0,0,0);
    return v==1 || v==2?v:0;
}
/* 0012e020: step byte at p (the mode block +a); mode is the second argument. */
uint32_t hg_gameplay_reload_step(HGResourceEnv *e,uint32_t p,uint32_t mode) {
    uint32_t s=r8(e,p);
    if(s==3)return r8(e,FADE_STATE)==2?4:0;
    if(s==2) {
        if(!call(e,0x2cfa90,mode!=0,0,0,0))return 0;
        call(e,0x139040,8,0,0,0);
        w8(e,p,r8(e,p)+1u);
        return 0;
    }
    if(s==0) {
        if(r8(e,LOADER_BUSY))return 0;
        w8(e,p,s+1u);
        call(e,0x130150,1,0x69,0,0);
    } else if(s!=1)return 0;
    if(r8(e,LOADER_BUSY))return 0;
    render_reset(e);
    call(e,0x1390b0,8,0,0,0);
    call(e,0x147f10,0,0,0,0);
    w8(e,p,r8(e,p)+1u);
    return 0;
}

/* 0012d1c0: gameplay mode handler. */
void hg_gameplay_mode(HGResourceEnv *e) {
    w32(e,0x700000c4,2);
    uint32_t fault,t=hg_ee_fp(0,r32(e,G(0x2f7c)),0x3f800000u,&fault);
    if(fault)domain_fault(e,0x82);
    uint32_t le=hg_ee_fp(4,t,0x47c35000u,&fault);   /* c.le.s t, 100000.0 */
    if(fault)domain_fault(e,0x83);
    w32(e,G(0x2f7c),t);
    if(!le)w32(e,G(0x2f7c),0);
    w32(e,G(0x2f80),r32(e,G(0x2f80))+1u);
    uint32_t b=blk(e),major=r8(e,b+8);
    switch(major) {
    case 0: {
        uint32_t minor=r8(e,b+9);
        if(minor==1) {
            if(!call(e,0x12d7d0,0,0,0,0))return;
            set8(e,8,r32(e,FLAG_CC)?3:1);
            set8(e,9,0);
            set8(e,0xa,0);
            set8(e,1,0);
            render_reset(e);
            return;
        }
        if(minor!=0)return;
        uint32_t sub=r8(e,b+0xa);
        if(sub==0) {
            if(r32(e,FLAG_CC)) {
                if(r8(e,0x3fa1c9))w8(e,b+0xa,sub+1u);
                else {w8(e,b+9,minor+1u);set8(e,0xa,0);}
                return;
            }
            call(e,0x141980,0,0,0,0);
            call(e,0x12d850,0,0,0,0);
            w16(e,0x3fa1c4,r16(e,G(0x2f34)));
            inc8(e,9);
            set8(e,0xa,0);
        } else if(sub==1) {
            call(e,0x1390b0,8,0,0,0);
            inc8(e,0xa);
        } else if(sub==2) {
            if(!r8(e,FADE_STATE)) {
                if(r16(e,0x95e568)&0xa000u) {
                    sound(e,0x50);
                    w16(e,0x3fa1c4,(1u-r16(e,0x3fa1c4))&0xffu);
                }
                if(r16(e,0x95e568)&0x20u) {
                    call(e,0x139040,8,0,0,0);
                    inc8(e,0xa);
                    sound(e,0x51);
                }
            }
            call(e,0x123250,0,0,0,0);
        } else if(sub==3) {
            if(r8(e,FADE_STATE)==2) {
                w8(e,0x3fa1c9,0);
                w8(e,b+9,r8(e,b+9)+1u);
                set8(e,0xa,0);
            }
            call(e,0x123250,0,0,0,0);
        }
        return;
    }
    case 1: {
        uint32_t v=call(e,0x12d680,0,0,0,0);
        if(v==1) {set8(e,8,5);set8(e,9,0);set8(e,0xa,0);set8(e,0xb,0);}
        else if(v==2) {set8(e,8,5);set8(e,9,4);set8(e,0xa,0);}
        return;
    }
    case 2:
        if(call(e,0x12e020,b+0xa,0,0,0)) {set8(e,8,5);set8(e,9,0);set8(e,0xa,0);set8(e,0xb,0);}
        return;
    case 4:
        w32(e,FLAG_CC,1);   /* the original shares the major-3 body */
        /* falls through */
    case 3:
        call(e,0x12d850,0,0,0,0);
        call(e,0x141760,0,0,0,0);
        set8(e,8,5);set8(e,9,5);set8(e,0xa,0);set8(e,0xb,0);set8(e,0xc,0);set8(e,0xd,0);
        set8(e,1,1);
        return;
    case 5:
        call(e,0x12d9a0,0,0,0,0);
        return;
    default:
        return;
    }
}

/* 0012dc90 */
uint32_t hg_gameplay_entry_step(HGResourceEnv *e) {
    uint32_t p=blk(e)+0xa,sub=r8(e,p);
    if(sub==3)return 4;
    if(sub==0) {w8(e,p,1);return 0;}
    if(sub==1) {
        w8(e,p,sub+1u);
        w16(e,blk(e)+0x1c,0);
        w8(e,blk(e)+0x12,1);
    } else if(sub!=2)return 0;
    w8(e,0x3fa1b1,0);
    w8(e,0x3fa1b2,0);
    w8(e,0x3fa1b0,1);
    w8(e,0x3fa1b3,0);
    inc8(e,0xa);
    return 0;
}

/* 0012d9a0: major-5 minor dispatcher. */
void hg_gameplay_major5(HGResourceEnv *e) {
    uint32_t b=blk(e),minor=r8(e,b+9);
    switch(minor) {
    case 0:
        if(!call(e,0x12dc90,0,0,0,0))return;
        set8(e,9,5);set8(e,0xa,0);set8(e,0xb,0);set8(e,1,1);
        call(e,0x139010,0,0,0,0);
        return;
    case 1:call(e,0x12e700,0,0,0,0);return;
    case 2:call(e,0x12f270,0,0,0,0);return;
    case 3:call(e,0x3372a0,0,0,0,0);return;
    case 4:call(e,0x12f800,0,0,0,0);return;
    case 5: {
        if(!call(e,0x12dd60,0,0,0,0))return;
        uint32_t cc=r32(e,FLAG_CC);
        if(cc==0 || cc==1) {
            set8(e,9,1);set8(e,0xa,0);set8(e,0xb,0);
            w32(e,FLAG_CC,2);
            return;
        }
        uint32_t s0=r8(e,0x3fa1b0);
        int active;
        if(s0==1 && r8(e,0x3fa1b2)==0)active=1;
        else if(s0==0 && r8(e,0x3fa1b1)==8)active=1;
        else active=s0==2 && r8(e,blk(e)+1)==0;
        set8(e,9,active?1:6);set8(e,0xa,0);set8(e,0xb,0);
        return;
    }
    case 6:
        if(call(e,0x12e020,b+0xa,0,0,0)) {set8(e,9,1);set8(e,0xa,0);set8(e,0xb,0);}
        return;
    default:
        return;
    }
}

/* 0012dbd0: PACK directory for the current area triple 003fa1b0/b1/b2. */
uint32_t hg_area_pack_id(HGResourceEnv *e,uint32_t retained) {
    uint32_t area=r8(e,0x3fa1b0)*4u;
    uint32_t stride=r32(e,G(-0x7df0)+area),base=r32(e,G(-0x7e00)+area);
    uint32_t v=base+stride*r8(e,0x3fa1b1);
    return retained?v+0x93u:r8(e,0x3fa1b2)+v+0x94u;
}

static void area_loaded_frame(HGResourceEnv *e) {
    call(e,0x12c3d0,CAMERA,0,0,0);
    call(e,0x270c30,PLAYER,r8(e,blk(e)+1),0,0);
    call(e,0x2821f0,r8(e,blk(e)+1),0,0,0);
}
/* 0012dd60: area load step; returns 4 when the destination is ready. */
uint32_t hg_area_load_step(HGResourceEnv *e) {
    uint32_t b=blk(e),sub=r8(e,b+0xa);
    switch(sub) {
    case 0:
        if(r8(e,b+0xb)==0) {w8(e,b+0xb,1);return 0;}
        if(r8(e,LOADER_BUSY))return 0;
        w8(e,b+0xb,0);
        render_reset(e);
        call(e,0x26ff90,4,0,0,0);
        call(e,0x156e60,0,0,0,0);
        call(e,0x12bf70,0,0,0,0);
        call(e,0x282160,r8(e,blk(e)+1),0,0,0);
        call(e,0x119ac8,PLAYER,0,0x520,0);
        call(e,0x130150,1,r16(e,G(-0x7dd0)+r8(e,blk(e)+1)*2u),0,0);
        inc8(e,0xa);
        /* falls through */
    case 1:
        if(r8(e,LOADER_BUSY))return 0;
        inc8(e,0xa);
        if(r8(e,blk(e)+1)) {
            call(e,0x130150,2,call(e,0x12dbd0,1,0,0,0),0,0);
            sound(e,1);
        } else call(e,0x130150,3,call(e,0x12dbd0,0,0,0,0),0,0);
        /* falls through */
    case 2:
        if(r8(e,FADE_STATE)!=2)return 0;
        w32(e,0x700000d4,0);
        inc8(e,0xa);
        call(e,0x1390b0,6,0,0,0);
        /* falls through */
    case 3:
        if(!r8(e,LOADER_BUSY)) {
            call(e,0x156ce0,0,0,0,0);
            call(e,0x139040,8,0,0,0);
            inc8(e,0xa);
        }
        area_loaded_frame(e);
        return 0;
    case 4:
        area_loaded_frame(e);
        return r8(e,FADE_STATE)==2?4:0;
    default:
        return 0;
    }
}

/* 0012fa00: per-frame gameplay event flags (pause, menu and map requests). */
uint32_t hg_gameplay_event_flags(HGResourceEnv *e) {
    if(r8(e,LOADER_BUSY)|r8(e,0x3f9e3a))return 0;
    if(r32(e,0x46d90c)==0)return 0;
    uint32_t kind=r8(e,0x3f9f3f);
    if(kind==1 || kind==4)return 6;
    if(kind==5)return 0x12;
    if(r8(e,FADE_STATE))return 0;
    if(r8(e,G(0x2f78)) || r8(e,G(0x2f74)))return 0;
    uint32_t pad=r16(e,G(0x2f94));
    if((pad&0x800u) || r8(e,0x95e44a)!=1)return 1;
    if(r8(e,0x3f9f6e) || r8(e,0x3f9f3d))return 0;
    if(pad&0x100u)return 2;
    return (pad&r16(e,G(0x2f54)))?10:0;
}

/* 0012e6d0 */
void hg_gameplay_event_frame(HGResourceEnv *e) {
    w32(e,0x95e57c,0);
    call(e,0x140140,0,0,0,0);
    call(e,0x1b7c80,0,0,0,0);
    call(e,0x12e2c0,0,0,0,0);
}
/* 0012f7d0 */
void hg_gameplay_request_exit(HGResourceEnv *e) {
    set8(e,9,4);set8(e,0xa,0);set8(e,0xb,0);
}
/* 0012f800: leave gameplay and reinstall the lifecycle mode 00121be0. */
void hg_gameplay_teardown(HGResourceEnv *e) {
    call(e,0x141b10,0,0,0,0);
    call(e,0x12bc00,0,0,0,0);
    call(e,0x139140,0x80,0,0,0);
    call(e,0x156e60,0,0,0,0);
    w32(e,0x700000f8,1);
    call(e,0x143300,0x121be0,0,0,0);
}

/* 0012e700 sub 6: restart in area 1 while preserving the selected save
 * fields across the two 00119910 copies of 003fa080/003fa50c. */
static void gameplay_restart(HGResourceEnv *e) {
    uint32_t sp=call(e,HG_GUEST_FRAME,0xa0,1,0,0);
    uint32_t q[4];
    for(int i=0;i<4;i++)q[i]=r32(e,0x3fa180+i*4);
    uint32_t s3=r32(e,0x46e13c);
    uint32_t s1=r16(e,0x3fa1c4);
    call(e,0x11b378,sp+0x80,0x3fa194,0,0);
    uint32_t s5=r32(e,0x46d89c),s2=r32(e,0x3fa190),s0=r8(e,0x3fa1b6),s4=r8(e,0x46e122);
    uint32_t c6=r8(e,0x3fa1c6),c7=r8(e,0x3fa1c7),ca=r8(e,0x3fa1ca);
    call(e,0x119910,0x3fa080,0x3fa50c,0x100,0);
    call(e,0x141980,0,0,0,0);
    call(e,0x12d850,0,0,0,0);
    for(int i=0;i<4;i++)w32(e,0x3fa180+i*4,q[i]);
    w32(e,0x46e13c,s3);
    w16(e,0x3fa1c4,s1);
    call(e,0x11b378,0x3fa194,sp+0x80,0,0);
    w32(e,0x46d89c,s5);
    w32(e,0x3fa190,s2);
    w8(e,0x3fa1b6,s0);
    w8(e,0x46e122,s4);
    call(e,0x119910,0x3fa50c,0x3fa080,0x100,0);
    w8(e,0x3fa1c6,c6);
    w8(e,0x3fa1c7,c7);
    w8(e,0x3fa1ca,ca);
    call(e,0x11e350,c6,c7,ca,0);
    w8(e,0x3fa1b0,0);
    w8(e,0x3fa60b,1);
    w8(e,0x3fa1b1,0);
    w8(e,0x3fa1b2,0);
    w8(e,0x3fa1b3,0);
    call(e,0x11fe20,0xcb,0xff,0,0);
    w32(e,FLAG_CC,2);
    set8(e,9,5);set8(e,0xa,0);set8(e,0xb,0);set8(e,1,1);
    call(e,HG_GUEST_FRAME,0xa0,0,0,0);
}
/* 0012e700 sub 5: warp to the destination triple 0046d92c..f. */
static void gameplay_warp(HGResourceEnv *e) {
    uint32_t a=r8(e,0x46d92c);
    w8(e,0x46e106,1);
    w8(e,0x3f9e39,1);
    uint32_t bb=r8(e,0x46d92d),c=r8(e,0x46d92e),d=r8(e,0x46d92f);
    w8(e,0x3f9e34,a);
    w8(e,0x3f9e35,bb);
    w8(e,0x3f9e36,c);
    if(d&0x80u) {
        w8(e,0x3f9e37,d&0x7fu);
        call(e,0x141330,r8(e,0x3f9e34),r8(e,0x3f9e35),r8(e,0x3f9e36),r8(e,0x3f9e37));
        copy_quad(e,0x3fa1e0,0x3f9300);
        copy_quad(e,0x3fa1f0,0x3f9310);
    } else w8(e,0x3f9e37,d);
    uint32_t v=r16(e,0x3fa1aa);
    w32(e,FLAG_CC,3);
    w16(e,0x3fa1a8,v);
    call(e,0x12e2c0,0,0,0,0);
}
/* 0012e840..0012e864: coordinator selector and call; 0012e868..: the
 * remainder of the active frame. Split only so the host connected frame can
 * stop at the coordinator entry; hg_gameplay_frame runs both in order. */
static void gameplay_active_coordinator(HGResourceEnv *e) {
    if(r8(e,0x3f9f3e))call(e,0x12f950,0,0,0,0);
    else call(e,0x12f850,0,0,0,0);
}
static void gameplay_active_after(HGResourceEnv *e) {
    if(r8(e,0x3f9e39)) {call(e,0x12e6d0,0,0,0,0);return;}
    uint32_t flags=call(e,0x12fa00,0,0,0,0);
    if(flags&1u) {
        w32(e,0x95e57c,0);
        set8(e,0xa,2);set8(e,0xb,0);set8(e,0xc,0);w16(e,blk(e)+0x1c,0);
        call(e,0x1404f0,0,0,0,0);
        call(e,0x156d50,0x3ca3d70au,0,2,0);
        call(e,0x156ce0,0,0,0,0);
        sound(e,0x56);
        call(e,0x139040,8,0,1,0);
        return;
    }
    if(!(flags&2u))return;
    w32(e,0x95e57c,0);
    call(e,0x156d50,0x3ca3d70au,0,2,0);
    call(e,0x156ce0,0,0,0,0);
    sound(e,2);
    set8(e,0xa,3);set8(e,0xb,0);set8(e,0xc,0);w16(e,blk(e)+0x1c,0);
    if(flags&8u) {w8(e,0x3f99da,0);w8(e,0x3f99b4,1);w8(e,0x3f9e07,1);}
    else if(flags&0x10u) {w8(e,0x3f9e07,0);w8(e,0x3f99b4,7);w8(e,0x3f99da,1);}
    else if(flags&4u) {w8(e,0x3f99b4,3);w8(e,0x3f99da,1);w8(e,0x3f9e07,0);}
    else {w8(e,0x3f99b4,0);w8(e,0x3f99da,0);w8(e,0x3f9e07,0);}
    call(e,0x1404f0,0,0,0,0);
    call(e,0x139040,8,0,1,0);
}
static void gameplay_active(HGResourceEnv *e) {
    gameplay_active_coordinator(e);
    gameplay_active_after(e);
}
/* 0012e700: active gameplay (minor 1). */
void hg_gameplay_frame(HGResourceEnv *e) {
    uint32_t b=blk(e),sub=r8(e,b+0xa);
    switch(sub) {
    case 0:
        call(e,0x139140,0x80,0,0,0);
        call(e,0x25c0f0,0,0,0,0);
        call(e,0x182430,0,0,0,0);
        call(e,0x141b10,0,0,0,0);
        call(e,0x141910,0,0,0,0);
        call(e,0x141210,0,0,0,0);
        call(e,0x13fef0,0,0,0,0);
        call(e,0x12bf70,0,0,0,0);
        call(e,0x13a4c0,0x3f97f0,0,0,0);
        call(e,0x189aa0,0,0,0,0);
        call(e,0x156cf0,0,0,0,0);
        call(e,0x1390b0,4,r8(e,0x46e106)?1:0,0,0);
        set8(e,0xa,1);
        if(r8(e,0x3fa1bb)) {
            uint32_t a2=volume_127(e);
            w8(e,0x3fa1bb,0);
            call(e,HG_CALL_T_REGS,1,0,0,0);
            call(e,0x156ad0,0x49c,0x1000,a2,0x40);
        }
        gameplay_active(e);
        return;
    case 1:
        gameplay_active(e);
        return;
    case 2: {                                   /* pause menu */
        uint32_t step=r8(e,b+0xb);
        if(step==0) {
            if(r8(e,FADE_STATE)!=2)return;
            call(e,0x1390b0,8,0,0,0);
            inc8(e,0xb);
        } else if(step==1) {
            uint32_t v=call(e,0x162750,0,0,0,0);
            if(v==1)set8(e,0xb,2);
            else if(v==2)set8(e,0xb,3);
            else if(v==3)set8(e,0xb,4);
        } else if(step==2) {
            w8(e,b+0xa,1);
            set8(e,0xb,0);
            set8(e,0xc,0);
            call(e,0x141810,0,0,0,0);
            call(e,0x1404f0,0,1,0,0);
            call(e,0x156550,0,0,0,0);
            call(e,0x156cf0,0,0,0,0);
        } else if(step==3) {
            call(e,0x139140,0x80,0,0,0);
            uint32_t m=blk(e);
            w32(e,FLAG_CC,1);
            w8(e,m+8,0);
            set8(e,9,0);
            set8(e,0xa,0);
        } else if(step==4) {
            w8(e,b+9,4);
            set8(e,0xa,0);
        }
        return;
    }
    case 3: {
        uint32_t step=r8(e,b+0xb);
        if(step==0) {
            if(r8(e,FADE_STATE)==2)w8(e,b+0xb,step+1u);
        } else if(step==1) {
            w32(e,0x3fa190,r32(e,0x3fa190)+1u);
            if(r32(e,0x3fa190)&0x80000000u)w32(e,0x3fa190,0);
            if(call(e,0x178ec0,0,0,0,0))inc8(e,0xb);
        } else if(step==2) {
            call(e,0x1404f0,0,1,0,0);
            set8(e,0xa,1);
            w16(e,blk(e)+0x1c,0);
            set8(e,0xb,0);
            set8(e,0xc,0);
            call(e,0x156550,0,0,0,0);
            call(e,0x156cf0,0,0,0,0);
            if(!r8(e,0x3f99db))call(e,0x1390b0,0x10,0,0,0);
        }
        return;
    }
    case 4: {
        uint32_t step=r8(e,b+0xb);
        if(step==0) {
            if(r8(e,LOADER_BUSY))return;
            w8(e,b+0xb,step+1u);
            call(e,0x156e60,0,0,0,0);
        } else if(step==1) {
            call(e,0x139140,0x80,0,0,0);
            call(e,0x130170,1,0x4e,0,0);
            inc8(e,0xb);
        } else if(step==2) {
            if(r8(e,LOADER_BUSY))return;
            w16(e,b+0x1e,0x168);
            inc8(e,0xb);
            uint32_t s2=r8(e,0x3f9e35),s1=r8(e,0x3f9e36),s0=r8(e,0x3f9e37);
            call(e,0x26fe40,7,0x7b,0,0);
            call(e,0x1390b0,8,0,0,0);
            call(e,0x166770,0,0,0,0);
            call(e,0x196ad0,0xab,0,0,0);
            w8(e,0x3f9e35,s2);
            w8(e,0x3f9e34,2);
            w8(e,0x3f9e36,s1);
            w8(e,0x3f9e37,s0);
        } else if(step==3 || step==4) {
            uint32_t x=r32(e,0x3f9e50),y=r32(e,0x3f9e54);
            w32(e,0x70000bac,0x43000000u);
            uint32_t z=r32(e,0x3f9e58);
            w32(e,0x70000ba0,x);
            w32(e,0x70000ba4,y);
            w32(e,0x70000ba8,z);
            call(e,0x166910,2,0x70000ba0,0,0);
            call(e,0x26fea0,0,0,0,0);
            uint32_t m=blk(e);
            if(r8(e,m+0xb)==3) {
                uint32_t t=r16(e,m+0x1e);
                if(t)w16(e,m+0x1e,t-1u);
                if(r8(e,FADE_STATE))return;
                if(r16(e,blk(e)+0x1e) && !r8(e,0x3f9f38))return;
                call(e,0x139040,2,0,0,0);
                inc8(e,0xb);
            } else if(r8(e,FADE_STATE)==2)call(e,0x12e2c0,0,0,0,0);
        }
        return;
    }
    case 5:
        if(r8(e,b+0xb)==0 && r8(e,FADE_STATE)==2)gameplay_warp(e);
        return;
    case 6:
        if(r8(e,b+0xb)==0)gameplay_restart(e);
        return;
    default:
        return;
    }
}

/* 001219e0: one timed logo/notice sequence step (a0 = sprite table, a1 =
 * first resource, a2/a3 = follow-up request, t0 = request 4 at the end).
 * 00123370 receives f12 = f13 = 128.0 and f14 = 0 through HG_CALL_FP_REGS. */
static void logo_draw(HGResourceEnv *e,uint32_t table) {
    call(e,HG_CALL_FP_REGS,0x43000000u,0x43000000u,0,0);
    call(e,0x123370,4,table,0xf0,0);
}
uint32_t hg_logo_sequence_step(HGResourceEnv *e,uint32_t table,uint32_t first,uint32_t kind,
                               uint32_t id,uint32_t last) {
    uint32_t p=blk(e)+9,minor=r8(e,p);
    if(minor==0) {
        call(e,0x130150,1,first,0,0);
        inc8(e,9);
        return 0;
    }
    if(minor==1) {
        if(r8(e,LOADER_BUSY))return 0;
        w8(e,p,minor+1u);
        w16(e,blk(e)+0x1a,300);
        call(e,0x1390b0,8,0,0,0);
        logo_draw(e,table);
        call(e,0x130150,kind,id,0,0);
        return 0;
    }
    if(minor==2) {
        logo_draw(e,table);
        uint32_t t=r16(e,blk(e)+0x1a);
        if(t)w16(e,blk(e)+0x1a,t-1u);
        if(r16(e,blk(e)+0x1a) || r8(e,LOADER_BUSY))return 0;
        call(e,0x139040,8,0,0,0);
        if(last)call(e,0x130150,1,4,0,0);
        inc8(e,9);
        return 0;
    }
    if(minor==3) {
        logo_draw(e,table);
        if(r8(e,LOADER_BUSY))return 0;
        return r8(e,FADE_STATE)==2?1:0;
    }
    return 0;
}
/* 00121810: initial (boot) mode installed by main. */
void hg_boot_mode(HGResourceEnv *e) {
    uint32_t b=blk(e),major=r8(e,b+8);
    switch(major) {
    case 0:
        call(e,0x139010,0,0,0,0);
        inc8(e,8);
        call(e,0x130150,0,0,0,0);
        return;
    case 1:
        if(!r8(e,LOADER_BUSY))w8(e,b+8,major+1u);
        return;
    case 2: {
        uint32_t minor=r8(e,b+9);
        if(minor==0) {
            call(e,0x1390b0,0xff,0,0,0);
            call(e,0x147f10,0,0,0,0);
            inc8(e,9);
        } else if(minor==1 && call(e,0x2cdef0,0,0,0,0)) {
            inc8(e,8);
            set8(e,9,0);
        }
        return;
    }
    case 3:
        call(e,HG_CALL_T_REGS,0,0,0,0);
        if(call(e,0x1219e0,G(-0x7e80),0x6a,0,0x6d)) {inc8(e,8);set8(e,9,0);}
        return;
    case 4:
        call(e,HG_CALL_T_REGS,1,0,0,0);
        if(call(e,0x1219e0,G(-0x7eb0),0x54,1,1)) {inc8(e,8);set8(e,9,0);}
        return;
    case 5:
        if(r8(e,LOADER_BUSY))return;
        w32(e,0x700000f8,0);
        call(e,0x143300,0x121be0,0,0,0);
        return;
    default:
        return;
    }
}

static uint32_t request_kind_for_system(HGResourceEnv *e) {
    return r16(e,G(0x2f34))?8u:7u;
}
/* 00121be0: lifecycle (title, new game, continue, area select) mode.
 * Major 2 installs the gameplay mode 0012d1c0. */
void hg_lifecycle_mode(HGResourceEnv *e) {
    w32(e,0x700000c4,1);
    w32(e,G(0x2f80),r32(e,G(0x2f80))+1u);
    uint32_t b=blk(e),major=r8(e,b+8);
    switch(major) {
    case 0: {
        uint32_t minor=r8(e,b+9);
        if(minor==0) {
            uint32_t sub=r8(e,b+0xa);
            if(sub==0) {
                w32(e,0x70000410,0);
                call(e,0x130150,1,0x50,0,0);
                inc8(e,0xa);
            } else if(sub==1) {
                if(r8(e,LOADER_BUSY))return;
                call(e,0x26ff90,4,0,0,0);
                call(e,0x1390b0,6,0,0,0);
                uint32_t kind=request_kind_for_system(e);
                w32(e,0x3f4d5c,0);
                uint32_t v=r16(e,G(0x2f34));
                w16(e,0x700000fe,v);
                call(e,0x130150,1,kind,0,0);
                inc8(e,0xa);
            } else if(sub==2) {
                call(e,0x270c30,PLAYER,0,0,0);
                if(r8(e,LOADER_BUSY))return;
                call(e,0x119ac8,0x3fa180,0,0x73fc0,0);
                w8(e,0x3fa1b0,0);
                w8(e,0x3fa1b1,9);
                uint32_t v=r16(e,G(0x2f34));
                w8(e,0x3fa1b2,0);
                w16(e,0x3fa1c4,v);
                call(e,0x130150,2,call(e,0x12dbd0,1,0,0,0),0,0);
                inc8(e,9);
                set8(e,0xa,0);
            }
        } else if(minor==1) {
            call(e,0x270c30,PLAYER,0,0,0);
            if(r8(e,LOADER_BUSY))return;
            w8(e,b+9,r8(e,b+9)+1u);
            call(e,0x139040,6,0,0,0);
        } else if(minor==2) {
            w8(e,b+9,minor+1u);
            call(e,0x147f10,0,0,0,0);
        } else if(minor==3) {
            uint32_t v=call(e,0x2cf020,0,0,0,0);
            if(!v)return;
            w32(e,0x70000410,v==1?1u:0u);
            set8(e,8,1);set8(e,9,0);set8(e,0xa,0);set8(e,0xb,0);set8(e,0xc,0);
            if(r32(e,0x700000f8)==2)set8(e,0x12,1);
            else {set8(e,0x11,0);set8(e,0x12,r32(e,0x700000f8));}
        }
        return;
    }
    case 1: {
        uint32_t v=call(e,0xa87880,0,0,0,0);
        if(v==3) {
            call(e,0x156550,0,0,0,0);
            set8(e,9,0);set8(e,0xa,0);set8(e,0xb,0);set8(e,0x12,0);
        } else if(v==1) {
            call(e,0x156550,1,0,0,0);
            w32(e,FLAG_CC,0);
            set8(e,8,2);
            uint32_t m=blk(e),choice=r8(e,m+0x11);
            if(choice==0) {w32(e,FLAG_CC,0);w8(e,m+8,2);}
            else if(choice==1) {w32(e,FLAG_CC,1);w8(e,m+8,3);}
            else if(choice==2)w8(e,m+8,4);
            else w8(e,m+8,5);
            set8(e,9,0);set8(e,0xa,0);set8(e,0xb,0);set8(e,0xc,0);
        }
        return;
    }
    case 2:
        call(e,0x143300,0x12d1c0,0,0,0);
        return;
    case 3: {
        uint32_t minor=r8(e,b+9);
        if(minor==2) {
            uint32_t v=call(e,0x2cf5c0,0,0,0,0);
            if(v==1) {
                call(e,0x139040,8,0,0,0);
                w32(e,FLAG_CC,1);
                set8(e,8,2);set8(e,9,0);set8(e,0xa,0);
            } else if(v==2) {
                call(e,0x139010,0,0,0,0);
                set8(e,8,1);set8(e,9,0);set8(e,0xa,0);set8(e,0xb,0);set8(e,0x12,1);
            }
            return;
        }
        if(minor==0) {
            if(r8(e,LOADER_BUSY))return;
            w8(e,b+9,minor+1u);
            call(e,0x130150,1,0x69,0,0);
        } else if(minor!=1)return;
        if(r8(e,LOADER_BUSY))return;
        call(e,0x1390b0,8,0,0,0);
        call(e,0x147f10,0,0,0,0);
        inc8(e,9);
        set8(e,0xa,0);
        return;
    }
    case 4: {
        uint32_t minor=r8(e,b+9);
        if(minor==0) {
            if(!call(e,0x1620d0,0,0,0,0))return;
            uint32_t now=r16(e,G(0x2f34)),was=r16(e,0x700000fe);
            if(was==now) {set8(e,9,2);return;}
            uint32_t kind=now?8u:7u;
            uint32_t v=r16(e,G(0x2f34));
            w16(e,0x700000fe,v);
            w16(e,0x3fa1c4,v);
            call(e,0x130150,1,kind,0,0);
            inc8(e,9);
            return;
        }
        if(minor==1 && r8(e,LOADER_BUSY))return;
        if(minor!=1 && minor!=2)return;
        set8(e,8,1);set8(e,9,0);set8(e,0xa,0);set8(e,0xb,0);set8(e,0x12,1);
        return;
    }
    case 5: {
        uint32_t minor=r8(e,b+9);
        switch(minor) {
        case 0: {
            uint32_t i=0;
            for(uint32_t n=0;n<15;n++) {
                if(r8(e,G(0x2efc)+i))break;
                i++;
                if(i>=15)i=0;
            }
            set8(e,0xb,i);
            set8(e,0xc,(r8(e,G(0x2f0c)+i)&1u)?0u:1u);
            set8(e,0xe,0);
            inc8(e,9);
        }
            /* falls through */
        case 1: {
            uint32_t m=blk(e);
            w8(e,m+9,r8(e,m+9)+1u);
            call(e,0x130150,1,3,0,0);
        }
            /* falls through */
        case 2:
            if(r8(e,LOADER_BUSY))return;
            call(e,0x1390b0,8,0,0,0);
            inc8(e,9);
            set8(e,0xa,0);
            return;
        case 3: {
            uint32_t v=call(e,0x122bd0,0,0,0,0);
            if(v==2) {
                uint32_t m=blk(e);
                if(r8(e,m+0xe)==0) {
                    w8(e,m+8,1);
                    set8(e,9,0);set8(e,0xa,0);set8(e,0xb,0);set8(e,0x12,1);
                } else {
                    w8(e,m+8,0);
                    set8(e,9,0);set8(e,0xa,0);set8(e,0xb,0);
                    w32(e,0x700000f8,2);
                }
            } else if(v==1) {
                inc8(e,9);
                set8(e,0xa,0);set8(e,0xd,0);set8(e,0xe,1);
            }
            return;
        }
        case 4: {
            uint32_t sub=r8(e,b+0xa);
            switch(sub) {
            case 0: {
                call(e,0x141980,0,0,0,0);
                uint32_t m=blk(e);
                w8(e,0x46e121,r8(e,m+0xb));
                w16(e,0x3fa1c4,r8(e,m+0xc));
                w8(e,m+0xa,r8(e,m+0xa)+1u);
            }
                /* falls through */
            case 1:
                if(!call(e,0x336e90,blk(e)+0xd,1,0,0))return;
                inc8(e,0xa);
                set8(e,0xd,0);
                return;
            case 2:
                call(e,0x141570,0,0,0,0);
                if(r8(e,0x3f9f43)<2)return;
                call(e,0x139040,8,0,0,0);
                inc8(e,0xa);
                call(e,0x156d50,0x3ca3d70au,0,2,0);
                call(e,0x156ce0,0,0,0,0);
                return;
            case 3:
                call(e,0x141570,0,0,0,0);
                if(r8(e,FADE_STATE)!=2)return;
                inc8(e,0xa);
                render_reset(e);
                return;
            case 4:
                w8(e,b+9,1);
                set8(e,0xa,0);
                call(e,0x156e60,0,0,0,0);
                return;
            default:
                return;
            }
        }
        default:
            return;
        }
    }
    default:
        return;
    }
}

/* 0011d830 up to the main loop (0011d830..0011d934). */
void hg_main_init(HGResourceEnv *e) {
    call(e,0x11db30,0,0,0,0);
    call(e,0x1482c0,0,0,0,0);
    call(e,0x11da00,0,0x3fff,0,0);
    call(e,0x11dc40,0,0,0,0);
    call(e,0x12fbf0,0,0,0,0);
    call(e,0x1431f0,0,0,0,0);
    call(e,0x13d7d0,0,0,0,0);
    call(e,0x11dc50,0,0,0,0);
    call(e,0x103c40,2,0x11df10,0,0);
    call(e,0x103c40,3,0x11df80,0,0);
    call(e,0x104aa8,2,0,0,0);
    call(e,0x104aa8,3,0,0,0);
    call(e,0x156870,0,0,0,0);
    call(e,0x155bf0,0,0,0,0);
    call(e,0x1432c0,0x121810,0,0,0);
    call(e,0x1465d0,0,0,0,0);
    while(call(e,0x11dfd0,0,0,0,0)==1) {}
    while(call(e,0x11dfd0,0,0,0,0)==0) {}
    w32(e,0x10000010,0x83);
    w32(e,0x10000000,0);
}
/* One iteration of the 0011d830 main loop (0011d938..0011d9fc), split at
 * the return of the mode dispatcher 00143240 for the host connected frame. */
static void main_frame_services(HGResourceEnv *e) {
    w32(e,0x95e308,0);
    call(e,0x148970,0,0,0,0);
    call(e,0x13d970,0,0,0,0);
    call(e,0x156740,0,0,0,0);
}
static void main_frame_head(HGResourceEnv *e) {
    main_frame_services(e);
    call(e,0x143240,0,0,0,0);
}
static void main_frame_tail(HGResourceEnv *e) {
    call(e,0x139240,0,0,0,0);
    call(e,0x1467b0,0,0,0,0);
    call(e,0x13da80,0,0,0,0);
    if(call(e,0x100b80,0,0,0,0)) {
        call(e,0x102738,0,0,0,0);
        call(e,0x102408,0,0,0,0);
        call(e,0x102150,0,0,0,0);
        call(e,0x101cb0,0,0,0,0);
        call(e,0x101780,0,0,0,0);
    }
    while(r32(e,0x95e308)==0)call(e,HG_WAIT_INTERRUPT,0,0,0,0);
    uint32_t x=r16(e,0x700000f0),y=r16(e,0x700000f2);
    call(e,0x149890,x&0x8000u?x|0xffff0000u:x,y&0x8000u?y|0xffff0000u:y,0,0);
    call(e,0x1496e0,0,0,0,0);
    w32(e,0x10000000,0);
    w32(e,G(0x2f84),r32(e,G(0x2f84))+1u);
}
void hg_main_frame(HGResourceEnv *e) {
    main_frame_head(e);
    main_frame_tail(e);
}
/* Host unit (M18): the same loop body entered at the 00143240 call of frame
 * N and left at the 00143240 call of frame N+1 (the native stop point). */
void hg_main_unit(HGResourceEnv *e) {
    call(e,0x143240,0,0,0,0);
    main_frame_tail(e);
    main_frame_services(e);
}

/* 0011df10 (INTC 2, vblank start) and 0011df80 (INTC 3, vblank end),
 * registered by main through 00103c40. 0010cca0 is the DI helper and
 * 00104e10 the iWakeupThread wrapper (kernel seams); the trailing sync/ei
 * only change CP0 Status, which is not memory and is not modeled here. */
uint32_t hg_vblank_start_handler(HGResourceEnv *e) {
    call(e,0x10cca0,0,0,0,0);
    w32(e,0x95e308,r32(e,0x95e308)+1u);
    uint32_t active=r16(e,0x3f8e30);
    w32(e,0x95e300,r32(e,0x95e300)+1u);
    if(active==1)call(e,0x104e10,r32(e,0x3f8e34),0,0,0);
    return 0;
}
uint32_t hg_vblank_end_handler(HGResourceEnv *e) {
    call(e,0x10cca0,0,0,0,0);
    if(r16(e,0x3f6db0)==1)call(e,0x104e10,r32(e,0x3f6db4),0,0,0);
    return 0;
}

/* Host connected frame (M17): one unit from the coordinator entry of frame N
 * to the coordinator entry of frame N+1, through the reconstructed lifecycle
 * layer. The coordinator body is the M13-M16 connected C (hg_coordinator_frame);
 * the rest of 0012e700's active frame, the main-loop tail, the vblank
 * interrupt handlers, the next loop head, 00143240, 0012d1c0, 0012d9a0 and
 * 0012e700 run as C. 001496e0's CPU-side GS field and render-bank effects use
 * the existing M14 model (hg_render_field_bank); 00148970 and all other
 * callees go to the outer bus (original instructions or declared externals).
 * The unit stops when the next active frame reaches its coordinator call. */
typedef struct {
    HGResourceEnv env;
    HGResourceEnv *outer;
    uint32_t frame;
    uint32_t suspended;
} HGConnected;
static uint32_t connected_call(void *user,uint32_t pc,uint32_t a,uint32_t b,uint32_t c,uint32_t d) {
    HGConnected *p=user;
    HGResourceEnv *e=&p->env;
    uint32_t result;
    if(pc==HG_WAIT_INTERRUPT) {
        p->outer->call(p->outer->user,HG_WAIT_INTERRUPT,0,0,0,0);
        hg_vblank_start_handler(e);
        hg_vblank_end_handler(e);
        return 0;
    }
    if(pc==0x12e700) {
        uint32_t m=blk(e);
        if(r8(e,m+0xa)==1 && !r8(e,0x3f9f3e)) {p->suspended=1;return 0;}
        hg_gameplay_frame(e);
        return 0;
    }
    if(pc==0x1496e0) {hg_render_field_bank(p->outer);return 0;}
    if(pc==0x12f850 || pc==0x12f950) {domain_fault(e,0x84);return 0;}
    if(hg_lifecycle_call(e,pc,a,b,c,d,&result))return result;
    return p->outer->call(p->outer->user,pc,a,b,c,d);
}
uint32_t hg_game_frame_connected(HGResourceEnv *outer,uint32_t frame) {
    HGConnected p;
    p.env=*outer;p.env.user=&p;p.env.call=connected_call;
    p.outer=outer;p.frame=frame;p.suspended=0;
    hg_coordinator_frame(outer,frame);     /* 0012f850 from the carried entry state */
    gameplay_active_after(&p.env);         /* rest of 0012e700 sub 1 */
    main_frame_tail(&p.env);               /* after 00143240 returned */
    main_frame_head(&p.env);               /* next iteration up to the coordinator call */
    return p.suspended;
}

int hg_lifecycle_call(HGResourceEnv *e,uint32_t pc,uint32_t a,uint32_t b,uint32_t c,uint32_t d,uint32_t *result) {
    (void)c;(void)d;
    *result=0;
    switch(pc) {
    case 0x143300:hg_mode_set(e,a);return 1;
    case 0x1432c0:hg_mode_init(e,a);return 1;
    case 0x143240:hg_mode_dispatch(e);return 1;
    case 0x12d1c0:hg_gameplay_mode(e);return 1;
    case 0x12d9a0:hg_gameplay_major5(e);return 1;
    case 0x12dc90:*result=hg_gameplay_entry_step(e);return 1;
    case 0x12dd60:*result=hg_area_load_step(e);return 1;
    case 0x12dbd0:*result=hg_area_pack_id(e,a);return 1;
    case 0x12d7d0:*result=hg_gameplay_enter_step(e);return 1;
    case 0x12d850:hg_gameplay_reset_services(e);return 1;
    case 0x12d680:*result=hg_gameplay_title_step(e);return 1;
    case 0x12e020:*result=hg_gameplay_reload_step(e,a,b);return 1;
    case 0x12e700:hg_gameplay_frame(e);return 1;
    case 0x12e6d0:hg_gameplay_event_frame(e);return 1;
    case 0x12fa00:*result=hg_gameplay_event_flags(e);return 1;
    case 0x12f7d0:hg_gameplay_request_exit(e);return 1;
    case 0x12f800:hg_gameplay_teardown(e);return 1;
    case 0x11df10:*result=hg_vblank_start_handler(e);return 1;
    case 0x121810:hg_boot_mode(e);return 1;
    case 0x121be0:hg_lifecycle_mode(e);return 1;
    case 0x11df80:*result=hg_vblank_end_handler(e);return 1;
    default:return 0;
    }
}
