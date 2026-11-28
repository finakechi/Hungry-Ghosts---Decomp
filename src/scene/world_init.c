/* M23 reusable state-provenance harness and original synthetic world-
 * initialization model.
 *
 * Connects existing C interfaces (disc_read.c, resource_loader.c,
 * game_lifecycle.c, front_end.c) on one shared 32 MiB EE RAM + 16 KiB
 * scratchpad bus using an independently authored synthetic scene and PACK
 * container. Keeps synthetic model behavior strictly distinct from retail
 * execution and leaves audio (M24) untouched. */
#include "world_init.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

enum {
    EE_RAM_SIZE = 0x02000000u,
    SCRATCH_BASE = 0x70000000u,
    SCRATCH_SIZE = 0x00004000u,
    SECTOR_SIZE = 2048u,

    GP_BASE = 0x003e7f70u,
    MODE_BLOCK = 0x003ead30u,
    FADE_BLOCK = 0x003eaeb0u,
    PAD_BLOCK = 0x0095e340u,
    PAD_GAME = 0x003eaf00u,
    PLAYER_ADDR = 0x003f92d0u,
    AREA_MAP_BLOCK = 0x003f97f0u,
    CAMERA_ADDR = 0x003f9860u,
    WORLD_ENV_ADDR = 0x003f9e30u,
    HANDLE_TABLE = 0x003f4300u,
    ARENA_TABLE = 0x003f4d3cu,
    TASK_NORMAL = 0x003f8e00u,
    OVERLAY_BASE = 0x00a87800u,
    RENDER_CTX_ADDR = 0x009e64a0u,
    SORT_QUEUE_ADDR = 0x00539470u,

    /* Actor and bone-node pools matching original layout verified in ELF:
     *   Nodes: 0046e140..004d613f (2048 x 0xd0), free stack 004d6140..004d813f,
     *          top pointer 004d8140, free count 004d8144.
     *   Actors: pool 0 at 004d8150 (286 x 0x3d0),
     *           pool 1 at 0051c3b0 (40 x 0x4d0),
     *           pool 2 at 00528430 (256 x 0x110),
     *           control/list heads at 00539430..00539467. */
    NODE_POOL_BASE = 0x0046e140u,
    NODE_STRIDE = 0xd0u,
    NODE_POOL_COUNT = 2048u,
    NODE_STACK_BASE = 0x004d6140u,
    NODE_STACK_TOP = 0x004d8140u,
    NODE_FREE_COUNT = 0x004d8144u,

    ACTOR_POOL0_BASE = 0x004d8150u,
    ACTOR_POOL0_STRIDE = 0x3d0u,
    ACTOR_POOL0_COUNT = 286u,
    ACTOR_POOL1_BASE = 0x0051c3b0u,
    ACTOR_POOL1_STRIDE = 0x4d0u,
    ACTOR_POOL1_COUNT = 40u,
    ACTOR_POOL2_BASE = 0x00528430u,
    ACTOR_POOL2_STRIDE = 0x110u,
    ACTOR_POOL2_COUNT = 256u,
    ACTOR_CTRL_BASE = 0x00539430u,
    ACTOR_LIST_MAIN = 0x00539448u,
    ACTOR_LIST_PRIO = 0x00539450u,
    ACTOR_LIST_TYPE2 = 0x00539458u,
    ACTOR_LIST_TYPE2_PRIO = 0x00539460u,

    /* Synthetic callbacks & addresses (clearly marked synthetic). */
    SYNTH_ROOT_ACTOR_CB = 0x00a89000u,
    SYNTH_NODE_ANCHOR_CB = 0x00a89100u,
    SYNTH_NODE_AUX1_CB = 0x00a89120u,
    SYNTH_NODE_AUX2_CB = 0x00a89140u,
    SYNTH_SPAWN_TABLE = 0x003e5d40u,
    SYNTH_FRAME_STACK = 0x01ff0000u,
    SYNTH_FIXUP_FRAME = 0x01fe8000u,
    SYNTH_TITLE_SENTINEL = 0x01660000u,

    HANDLE_PLAYER = 0x3fu,
    HANDLE_AREA = 0x96u,
    HANDLE_TITLE = 0x22fu,
    PACK_DIR_PLAYER = 0x3fu,
    PACK_DIR_AREA = 0x102u,
    AREA_OVERLAY_SIZE = 2048u,

    STAGE_COLD_BOOT = 0u,
    STAGE_PUBLISH = 1u,
    STAGE_WORLD_INIT = 2u,
    STAGE_ACTIVE_FRAME = 3u,
    STAGE_TEARDOWN = 4u
};

#define HG_DOMAIN_FAULT 0xfffffff0u
#define HG_GUEST_FRAME 0xfffffff1u
#define HG_CALL_RETURN 0xfffffff2u
#define HG_EXTERNAL_CALL 0xfffffff3u
#define HG_CALL_WIDE_A3 0xfffffff4u
#define HG_CALL_T_REGS 0xfffffff5u
#define HG_SERVICE_MARK 0xfffffff6u
#define HG_RENDER_FIELD 0xfffffff8u
#define HG_CALL_EXTRA_FP 0xfffffff9u
#define HG_CALL_FP_REGS 0xfffffffbu

struct HGWorldInitHarness {
    HGSyntheticSceneConfig cfg;
    HGWorldFaultInject faults;
    uint8_t *ee;
    uint8_t *scratch;
    uint8_t *synth_disc;
    size_t synth_disc_size;

    HGReadState read_state;
    HGReadEnv read_env;
    HGResourceEnv res_env;

    /* Synthetic controller input delivered through real hg_pad_service. */
    HGSyntheticPadInput current_pad;

    /* Pending register seams emitted by existing C functions. */
    uint32_t pending_f12;
    uint32_t pending_f13;
    uint32_t pending_f14;
    bool has_pending_fp;
    uint32_t pending_t0;
    uint32_t guest_sp;

    /* Lifecycle / provenance tracking. */
    uint32_t cycle;
    uint32_t frame;
    uint32_t current_stage;
    bool cold_booted;
    bool resources_published;
    bool world_entered;
    bool pool_init_ran;
    bool spawn_init_ran;
    bool camera_init_ran;
    bool state0_init_ran;
    bool order_violation_seen;
    bool position_injected;
    bool camera_updated_this_cycle;
    uint32_t order_sub0_step;
    uint32_t world_root_actor;
    uint32_t initial_node_free_after_init;

    /* Observed counters & error latches. */
    uint32_t domain_fault_count;
    uint32_t last_domain_fault;
    uint32_t unknown_call_count;
    uint32_t wall_contact_events;
    uint32_t floor_query_events;
    uint32_t actor_ticks_main;
    uint32_t actor_ticks_type2;
    uint32_t lcg_state;
    uint32_t cdvd_reads;
    HGValueProvenance player_pos_prov;
    HGValueProvenance camera_prov;

    HGCallRecord calls[HG_WORLD_MAX_CALLS];
    size_t call_count;
    HGProvenanceRecord provenance[HG_WORLD_MAX_PROVENANCE];
    size_t provenance_count;

    /* ---- M25 session state (unused unless m25) ---- */
    bool m25;
    HGWorldSceneSpec spec;
    HGWorldSessionFaults sfaults;
    HGWorldReadiness readiness;
    HGWorldInitError init_error;   /* first failure latched during world setup */
    uint32_t post_ready_updates;
    uint32_t coordinator_ticks;
    uint32_t session_seq;
    bool spawn_effect_done;        /* swap fault bookkeeping (model-internal) */
    uint32_t live_props[HG_WORLD_MAX_PROPS];
    uint32_t live_prop_count;
    HGWorldStateEvent history[HG_WORLD_MAX_HISTORY];
    size_t history_count;
    HGOwnershipEvent ownership[HG_WORLD_MAX_OWNERSHIP];
    size_t ownership_count;
    HGEffectEvent effects[HG_WORLD_MAX_EFFECTS];
    size_t effect_count;
};

/* ---- Bit/float conversion helpers -------------------------------------- */

static uint32_t f2u(float v) {
    uint32_t bits = 0;
    memcpy(&bits, &v, sizeof(bits));
    return bits;
}

static float u2f(uint32_t bits) {
    float v = 0.0f;
    memcpy(&v, &bits, sizeof(v));
    return v;
}

static void w16_buf(uint8_t *buf, size_t off, uint16_t v) {
    buf[off] = (uint8_t)(v & 0xffu);
    buf[off + 1] = (uint8_t)((v >> 8) & 0xffu);
}

static void w32_buf(uint8_t *buf, size_t off, uint32_t v) {
    for (unsigned i = 0; i < 4u; ++i)
        buf[off + i] = (uint8_t)((v >> (8u * i)) & 0xffu);
}

/* ---- Guest bus memory access ------------------------------------------- */

static uint8_t *map_guest(const HGWorldInitHarness *h, uint32_t addr, uint32_t width) {
    if (width != 1u && width != 2u && width != 4u)
        return NULL;
    if (addr >= SCRATCH_BASE && addr <= SCRATCH_BASE + SCRATCH_SIZE - width)
        return h->scratch + (addr - SCRATCH_BASE);
    if (addr <= EE_RAM_SIZE - width)
        return h->ee + addr;
    return NULL;
}

static uint32_t bus_load(void *user, uint32_t addr, uint32_t width) {
    const HGWorldInitHarness *h = user;
    uint8_t *p = map_guest(h, addr, width);
    if (!p)
        return 0;
    uint32_t v = 0;
    for (uint32_t i = 0; i < width; ++i)
        v |= (uint32_t)p[i] << (8u * i);
    return v;
}

static void bus_store(void *user, uint32_t addr, uint32_t width, uint32_t value) {
    HGWorldInitHarness *h = user;
    uint8_t *p = map_guest(h, addr, width);
    if (!p)
        return;
    for (uint32_t i = 0; i < width; ++i)
        p[i] = (uint8_t)((value >> (8u * i)) & 0xffu);
}

static uint32_t r32(const HGWorldInitHarness *h, uint32_t a) { return bus_load((void *)h, a, 4u); }
static uint32_t r16(const HGWorldInitHarness *h, uint32_t a) { return bus_load((void *)h, a, 2u); }
static uint32_t r8(const HGWorldInitHarness *h, uint32_t a) { return bus_load((void *)h, a, 1u); }
static void w32(HGWorldInitHarness *h, uint32_t a, uint32_t v) { bus_store(h, a, 4u, v); }
static void w16(HGWorldInitHarness *h, uint32_t a, uint32_t v) { bus_store(h, a, 2u, v); }
static void w8(HGWorldInitHarness *h, uint32_t a, uint32_t v) { bus_store(h, a, 1u, v); }

static void record_call(HGWorldInitHarness *h, uint32_t pc, uint32_t a0, uint32_t a1,
                        HGValueProvenance prov) {
    if (h->call_count < HG_WORLD_MAX_CALLS) {
        h->calls[h->call_count++] = (HGCallRecord){
            .frame = h->frame,
            .entry_pc = pc,
            .a0 = a0,
            .a1 = a1,
            .provenance = prov,
            .stage = h->current_stage
        };
    }
}

/* ---- M25 ledgers ---------------------------------------------------------- */

static void m25_effect(HGWorldInitHarness *h, uint32_t pc, HGValueProvenance prov) {
    if (!h->m25 || h->effect_count >= HG_WORLD_MAX_EFFECTS)
        return;
    h->effects[h->effect_count++] = (HGEffectEvent){
        .seq = h->session_seq++, .cycle = h->cycle, .frame = h->frame,
        .pc = pc, .provenance = (uint32_t)prov
    };
}

static void m25_own(HGWorldInitHarness *h, HGOwnershipOp op, uint32_t object,
                    uint32_t owner, uint32_t producer_pc) {
    if (!h->m25 || h->ownership_count >= HG_WORLD_MAX_OWNERSHIP)
        return;
    h->ownership[h->ownership_count++] = (HGOwnershipEvent){
        .seq = h->session_seq++, .cycle = h->cycle, .frame = h->frame,
        .op = (uint32_t)op, .object = object, .owner = owner, .producer_pc = producer_pc
    };
}

static void m25_set_ready(HGWorldInitHarness *h, HGWorldReadiness to, uint32_t detail) {
    if (!h->m25 || h->readiness == to)
        return;
    if (h->history_count < HG_WORLD_MAX_HISTORY) {
        h->history[h->history_count++] = (HGWorldStateEvent){
            .seq = h->session_seq++, .cycle = h->cycle, .frame = h->frame,
            .from = (uint32_t)h->readiness, .to = (uint32_t)to, .detail = detail
        };
    }
    h->readiness = to;
}

static void m25_latch(HGWorldInitHarness *h, HGWorldInitError err) {
    if (h->init_error == HG_WORLD_OK)
        h->init_error = err;
}

static void record_provenance(HGWorldInitHarness *h, const char *label,
                              uint32_t addr, uint32_t size, uint32_t val,
                              HGValueProvenance prov, uint32_t producer_pc) {
    if (h->provenance_count >= HG_WORLD_MAX_PROVENANCE)
        return;
    HGProvenanceRecord *rec = &h->provenance[h->provenance_count++];
    memset(rec, 0, sizeof(*rec));
    size_t n = strlen(label);
    if (n >= sizeof(rec->label))
        n = sizeof(rec->label) - 1u;
    memcpy(rec->label, label, n);
    rec->guest_addr = addr;
    rec->size = size;
    rec->value_u32 = val;
    rec->provenance = prov;
    rec->producer_pc = producer_pc;
    rec->frame = h->frame;
}

/* ---- Real HGReadEnv CDVD callbacks backed by synthetic in-memory disc --- */

static uint32_t synth_cdvd_read32(void *user, uint32_t addr) {
    return bus_load(user, addr, 4u);
}

static int32_t synth_cdvd_sync(void *user, int32_t mode) {
    (void)user;
    (void)mode;
    return 0; /* idle / transfer completed */
}

static int32_t synth_cdvd_disk_ready(void *user, int32_t mode) {
    (void)user;
    (void)mode;
    return 2; /* ready */
}

static int32_t synth_cdvd_read(void *user, uint32_t lba, uint32_t count,
                               uint32_t destination, const uint8_t *mode) {
    HGWorldInitHarness *h = user;
    (void)mode;
    size_t byte_off = (size_t)lba * SECTOR_SIZE;
    size_t byte_len = (size_t)count * SECTOR_SIZE;
    if (byte_off > h->synth_disc_size || byte_len > h->synth_disc_size - byte_off)
        return 0;
    if (destination > EE_RAM_SIZE || byte_len > EE_RAM_SIZE - destination)
        return 0;
    memcpy(h->ee + destination, h->synth_disc + byte_off, byte_len);
    h->cdvd_reads++;
    return 1;
}

static int32_t synth_cdvd_get_error(void *user) {
    (void)user;
    return 0;
}

/* ---- Original synthetic PACK + FLK5 disc image builder ------------------ */

/* Build an independently authored synthetic PACK container in h->synth_disc:
 *   - Directory 0x3f (sector 0x3f): publishes handle 0x3f (synthetic player
 *     FLK5 with a valid multi-bone model and animation stub).
 *   - Directory 0x102 (sector 0x102? No: PACK file starts at LBA 16; directory
 *     offset is id << 11 inside the PACK file, so we size synth_disc >= 256
 *     sectors? Wait: 0x102 = 258! Let's use PACK file LBA 0 and 300 sectors,
 *     or directory 0x102 -> sector 258!). Let's allocate 320 sectors (640 KiB)
 *     so both directory 0x3f (sector 63) and directory 0x102 (sector 258) sit
 *     cleanly inside the synthetic disc image with zero overlap! */
enum {
    SYNTH_DISC_TOTAL_SECTORS = 320u,
    SYNTH_PACK_FILE_LBA = 0u,
    SYNTH_AREA_FILE_LBA = 280u,
    SYNTH_PLAYER_PAYLOAD_SECTOR = 80u,
    SYNTH_AREA_PAYLOAD_SECTOR = 290u
};

static void build_synthetic_player_flk5(uint8_t *dst, uint32_t bone_count, float head_offset) {
    /* FLK5 header: magic "FLK5", count=2, size=0x1000.
     *   entry 0 (type 0x01000000): synthetic player model at +0x40
     *   entry 1 (type 0x09000000): synthetic animation block at +0xc00 */
    w32_buf(dst, 0x00u, 0x354b4c46u);
    w32_buf(dst, 0x04u, 2u);
    w32_buf(dst, 0x08u, 0x1000u);
    w32_buf(dst, 0x10u, 0x01000040u); /* type 0x01, offset 0x40 */
    w32_buf(dst, 0x14u, 0x09000c00u); /* type 0x09, offset 0xc00 */

    /* Synthetic model block at +0x40:
     *   +0x08: bone count (e.g. 20, must be >= 18 for camera bone 0x11)
     *   +0x0c: relative offset to bone pose table (0x80 -> 0x40 + 0x80 = 0xc0)
     *   +0x34: attachment relative offset = 0 (no external attachment alloc)
     *   +0x38: bounds record relative offset = 0 */
    uint8_t *model = dst + 0x40u;
    w32_buf(model, 0x08u, bone_count);
    w32_buf(model, 0x0cu, 0x80u);
    w32_buf(model, 0x34u, 0u);
    w32_buf(model, 0x38u, 0u);

    /* Bone pose records at model + 0x80, stride 0x50 each. */
    uint8_t *bones = model + 0x80u;
    for (uint32_t i = 0; i < bone_count; ++i) {
        uint8_t *rec = bones + i * 0x50u;
        w32_buf(rec, 0x04u, i == 0 ? 0xffffffffu : 0u);
        /* Identity rotation / default local offset (head bone height 16.5 on bone 0). */
        w32_buf(rec, 0x10u, 0u);
        w32_buf(rec, 0x14u, 0u);
        w32_buf(rec, 0x18u, 0u);
        w32_buf(rec, 0x1cu, f2u(1.0f));
        w32_buf(rec, 0x40u, 0u);
        w32_buf(rec, 0x44u, i == 2u ? f2u(head_offset) : 0u); /* bone 2 = head/eye node */
        w32_buf(rec, 0x48u, 0u);
    }

    /* Synthetic animation header stub at +0xc00. */
    w32_buf(dst + 0xc00u, 0x00u, 1u);
}

static void build_synthetic_area_flk5(uint8_t *dst) {
    /* FLK5 with 1 map geometry/collision marker (type 0x01) + 1 collision marker (0x0d). */
    w32_buf(dst, 0x00u, 0x354b4c46u);
    w32_buf(dst, 0x04u, 2u);
    w32_buf(dst, 0x08u, 0x200u);
    w32_buf(dst, 0x10u, 0x01000040u);
    w32_buf(dst, 0x14u, 0x0d000100u);
    w32_buf(dst + 0x40u, 0x00u, 0x4d323341u); /* "M23A" synthetic marker */
    w32_buf(dst + 0x100u, 0x00u, 0x4d323343u); /* "M23C" synthetic collision */
}

static uint32_t scene_player_dir(const HGWorldInitHarness *h) {
    return h->m25 ? h->spec.player_dir : PACK_DIR_PLAYER;
}

static uint32_t scene_area_dir(const HGWorldInitHarness *h) {
    return h->m25 ? h->spec.area_dir : PACK_DIR_AREA;
}

static void build_synthetic_disc_image(HGWorldInitHarness *h) {
    memset(h->synth_disc, 0, h->synth_disc_size);

    /* 1. PACK directory 0x3f at sector 0x3f: publishes handle 0x3f. */
    uint8_t *dir_player = h->synth_disc + (size_t)scene_player_dir(h) * SECTOR_SIZE;
    uint32_t p_off = SYNTH_PLAYER_PAYLOAD_SECTOR * SECTOR_SIZE;
    uint32_t p_size = 2u * SECTOR_SIZE; /* 4096 bytes */
    w32_buf(dir_player, 0x04u, 1u);     /* non-zero member flag */
    w32_buf(dir_player, 0x08u, 1u);     /* 1 published member */
    w32_buf(dir_player, 0x10u, p_off);  /* bulk byte offset in PACK file */
    w32_buf(dir_player, 0x14u, p_size); /* bulk byte size */
    w16_buf(dir_player, 0x18u, 0u);     /* 0 audio special members */
    w16_buf(dir_player, 0x1au, 0u);     /* 0 fixup special members */
    w16_buf(dir_player, 0x1cu, 0u);     /* 0 extra special members */
    w32_buf(dir_player, 0x20u, 0u);     /* 0 fixups in phase 7 */
    /* Member 0 descriptor at +0x40: {file_off, size, handle_id, 0}. */
    w32_buf(dir_player, 0x40u, p_off);
    w32_buf(dir_player, 0x44u, p_size);
    w32_buf(dir_player, 0x48u, HANDLE_PLAYER);
    build_synthetic_player_flk5(h->synth_disc + p_off, h->cfg.player_bones,
                                h->m25 ? h->spec.head_offset : 16.5f);

    /* 2. PACK directory 0x102 at sector 0x102: triggers mode 8 in
     *    hg_pack_general_prepare -> hg_resource_clear_dynamic_handles +
     *    hg_area_read_step + publishes handle 0x96. */
    uint8_t *dir_area = h->synth_disc + (size_t)scene_area_dir(h) * SECTOR_SIZE;
    uint32_t a_off = SYNTH_AREA_PAYLOAD_SECTOR * SECTOR_SIZE;
    uint32_t a_size = SECTOR_SIZE;
    w32_buf(dir_area, 0x04u, 1u);
    w32_buf(dir_area, 0x08u, 1u);
    w32_buf(dir_area, 0x10u, a_off);
    w32_buf(dir_area, 0x14u, a_size);
    w16_buf(dir_area, 0x18u, 0u);
    w16_buf(dir_area, 0x1au, 0u);
    w16_buf(dir_area, 0x1cu, 0u);
    w32_buf(dir_area, 0x20u, 0u);
    w32_buf(dir_area, 0x40u, a_off);
    w32_buf(dir_area, 0x44u, a_size);
    w32_buf(dir_area, 0x48u, HANDLE_AREA);
    build_synthetic_area_flk5(h->synth_disc + a_off);

    /* 3. Synthetic AREA overlay binary at SYNTH_AREA_FILE_LBA (2048 bytes). */
    uint8_t *ovl = h->synth_disc + (size_t)SYNTH_AREA_FILE_LBA * SECTOR_SIZE;
    w32_buf(ovl, 0x00u, 0x33324d48u); /* "HM23" synthetic overlay header */
    w32_buf(ovl, 0x04u, AREA_OVERLAY_SIZE);
}

/* ---- Original synthetic math & geometry helpers ------------------------- */

static void synth_mat_identity(HGWorldInitHarness *h, uint32_t dst) {
    for (uint32_t i = 0; i < 16u; ++i)
        w32(h, dst + i * 4u, (i % 5u == 0u) ? f2u(1.0f) : 0u);
}

static void synth_mat_mul_rot_x(HGWorldInitHarness *h, uint32_t angle_bits,
                                uint32_t dst, uint32_t src) {
    float a = u2f(angle_bits);
    float c = cosf(a), s = sinf(a);
    float m[16], r[16];
    for (uint32_t i = 0; i < 16u; ++i)
        m[i] = u2f(r32(h, src + i * 4u));
    /* Row-vector convention matching EE/VU0 matrix helpers (row 0..3):
     * Rx: y' = y*c - z*s, z' = y*s + z*c (multiply R * M). */
    memcpy(r, m, sizeof(r));
    if (h->m25) {
        /* M25: dst = src * Rx (columns 1, 2), the convention measured by
         * executing original 00143c60 on a test matrix. */
        for (int row = 0; row < 4; ++row) {
            r[row * 4 + 1] = m[row * 4 + 1] * c - m[row * 4 + 2] * s;
            r[row * 4 + 2] = m[row * 4 + 1] * s + m[row * 4 + 2] * c;
        }
    } else {
        for (int col = 0; col < 4; ++col) {
            r[4 + col] = c * m[4 + col] + s * m[8 + col];
            r[8 + col] = -s * m[4 + col] + c * m[8 + col];
        }
    }
    for (uint32_t i = 0; i < 16u; ++i)
        w32(h, dst + i * 4u, f2u(r[i]));
}

static void synth_mat_mul_rot_y(HGWorldInitHarness *h, uint32_t angle_bits,
                                uint32_t dst, uint32_t src) {
    float a = u2f(angle_bits);
    float c = cosf(a), s = sinf(a);
    float m[16], r[16];
    for (uint32_t i = 0; i < 16u; ++i)
        m[i] = u2f(r32(h, src + i * 4u));
    /* Forward vector (0,0,20,1) transformed by Ry(yaw) yields
     * (20*sin(yaw), 0, 20*cos(yaw), 1), matching hg_player_walk sin/cos! */
    memcpy(r, m, sizeof(r));
    if (h->m25) {
        /* M25: dst = src * Ry (columns 0, 2), measured on original 00143d30. */
        for (int row = 0; row < 4; ++row) {
            r[row * 4 + 0] = m[row * 4 + 0] * c + m[row * 4 + 2] * s;
            r[row * 4 + 2] = -m[row * 4 + 0] * s + m[row * 4 + 2] * c;
        }
    } else {
        for (int col = 0; col < 4; ++col) {
            r[0 + col] = c * m[0 + col] - s * m[8 + col];
            r[8 + col] = s * m[0 + col] + c * m[8 + col];
        }
    }
    for (uint32_t i = 0; i < 16u; ++i)
        w32(h, dst + i * 4u, f2u(r[i]));
}

static void synth_mat_mul_rot_z(HGWorldInitHarness *h, uint32_t angle_bits,
                                uint32_t dst, uint32_t src) {
    float a = u2f(angle_bits);
    float c = cosf(a), s = sinf(a);
    float m[16], r[16];
    for (uint32_t i = 0; i < 16u; ++i)
        m[i] = u2f(r32(h, src + i * 4u));
    memcpy(r, m, sizeof(r));
    if (h->m25) {
        /* M25: dst = src * Rz (columns 0, 1), measured on original 00143b90. */
        for (int row = 0; row < 4; ++row) {
            r[row * 4 + 0] = m[row * 4 + 0] * c - m[row * 4 + 1] * s;
            r[row * 4 + 1] = m[row * 4 + 0] * s + m[row * 4 + 1] * c;
        }
    } else {
        for (int col = 0; col < 4; ++col) {
            r[0 + col] = c * m[0 + col] + s * m[4 + col];
            r[4 + col] = -s * m[0 + col] + c * m[4 + col];
        }
    }
    for (uint32_t i = 0; i < 16u; ++i)
        w32(h, dst + i * 4u, f2u(r[i]));
}

static void synth_build_transform(HGWorldInitHarness *h, uint32_t out_mat,
                                  uint32_t pos_addr, uint32_t rot_addr) {
    synth_mat_identity(h, out_mat);
    synth_mat_mul_rot_x(h, r32(h, rot_addr + 0u), out_mat, out_mat);
    synth_mat_mul_rot_y(h, r32(h, rot_addr + 4u), out_mat, out_mat);
    synth_mat_mul_rot_z(h, r32(h, rot_addr + 8u), out_mat, out_mat);
    for (uint32_t i = 0; i < 3u; ++i)
        w32(h, out_mat + 0x30u + i * 4u, r32(h, pos_addr + i * 4u));
    w32(h, out_mat + 0x3cu, f2u(1.0f));
}

static void synth_update_bones(HGWorldInitHarness *h, uint32_t obj, uint32_t parent_mat) {
    uint32_t count = r32(h, obj + 0x54u);
    uint32_t table = r32(h, obj + 0x58u);
    if (!table || count == 0u || count > 64u)
        return;
    for (uint32_t i = 0; i < count; ++i) {
        uint32_t node = r32(h, table + i * 4u);
        if (!node)
            continue;
        for (uint32_t w = 0; w < 16u; ++w)
            w32(h, node + w * 4u, r32(h, parent_mat + w * 4u));
        /* Apply bone local translation populated by real hg_actor_default_pose
         * at node+0x90..+0x98 to row 3 of the bone's world matrix (node+0x30..). */
        for (uint32_t axis = 0; axis < 3u; ++axis) {
            float base = u2f(r32(h, parent_mat + 0x30u + axis * 4u));
            float off = u2f(r32(h, node + 0x90u + axis * 4u));
            w32(h, node + 0x30u + axis * 4u, f2u(base + off));
        }
        w32(h, node + 0x3cu, f2u(1.0f));
    }
}

static void synth_camera_lookat(HGWorldInitHarness *h, uint32_t cam) {
    float ex = u2f(r32(h, cam + 0x00u));
    float ey = u2f(r32(h, cam + 0x04u));
    float ez = u2f(r32(h, cam + 0x08u));
    float tx = u2f(r32(h, cam + 0x10u));
    float ty = u2f(r32(h, cam + 0x14u));
    float tz = u2f(r32(h, cam + 0x18u));
    float dx = tx - ex, dy = ty - ey, dz = tz - ez;
    float horiz = sqrtf(dx * dx + dz * dz);
    float len = sqrtf(dx * dx + dy * dy + dz * dz);
    if (len < 1e-4f) {
        dx = 0.0f; dy = 0.0f; dz = 1.0f;
        len = 1.0f; horiz = 1.0f;
    }
    float fx = dx / len, fy = dy / len, fz = dz / len;
    w32(h, cam + 0x30u, f2u(fx));
    w32(h, cam + 0x34u, f2u(fy));
    w32(h, cam + 0x38u, f2u(fz));
    w32(h, cam + 0x3cu, f2u(1.0f));
    w32(h, cam + 0xc0u, f2u(dx));
    w32(h, cam + 0xc4u, f2u(dy));
    w32(h, cam + 0xc8u, f2u(dz));
    w32(h, cam + 0xccu, f2u(horiz));
    float yaw = atan2f(dx, dz);
    float pitch = atan2f(-dy, horiz > 1e-4f ? horiz : 1e-4f);
    w32(h, cam + 0xd0u, f2u(pitch));
    w32(h, cam + 0xd4u, f2u(yaw));
    w32(h, cam + 0xd8u, 0u);
    w32(h, cam + 0xdcu, f2u(1.0f));
    synth_mat_identity(h, cam + 0x40u);
    w32(h, cam + 0x70u, f2u(ex));
    w32(h, cam + 0x74u, f2u(ey));
    w32(h, cam + 0x78u, f2u(ez));
    w32(h, cam + 0x7cu, f2u(1.0f));
    h->camera_prov = HG_PROV_SYNTHETIC_MODEL;
}

/* Synthetic scene wall-segment collision query (001446a0):
 * Checks segment p0 -> p1 against synthetic courtyard box
 * [wall_min_x, wall_max_x] x [wall_min_z, wall_max_z]. */
static uint32_t synth_wall_query(HGWorldInitHarness *h, uint32_t p0_addr, uint32_t p1_addr) {
    float x1 = u2f(r32(h, p1_addr + 0u));
    float z1 = u2f(r32(h, p1_addr + 8u));
    (void)p0_addr;
    float nx = 0.0f, nz = 0.0f;
    float px = 0.0f, pz = 0.0f;
    bool hit = false;
    if (h->m25 && h->sfaults.disable_walls_from != 0u &&
        h->post_ready_updates >= h->sfaults.disable_walls_from)
        return 0u; /* fault: collision service stops reporting walls */

    if (x1 > h->cfg.wall_max_x) {
        nx = -1.0f; px = h->cfg.wall_max_x - x1; hit = true;
    } else if (x1 < h->cfg.wall_min_x) {
        nx = 1.0f; px = h->cfg.wall_min_x - x1; hit = true;
    } else if (z1 > h->cfg.wall_max_z) {
        nz = -1.0f; pz = h->cfg.wall_max_z - z1; hit = true;
    } else if (z1 < h->cfg.wall_min_z) {
        nz = 1.0f; pz = h->cfg.wall_min_z - z1; hit = true;
    }
    if (!hit)
        return 0u;

    w32(h, 0x003f39b0u, f2u(px));
    w32(h, 0x003f39b4u, 0u);
    w32(h, 0x003f39b8u, f2u(pz));
    w32(h, 0x003f39bcu, 0u);
    w16(h, 0x003f39c4u, 2u); /* normal wall flag required by hg_player_static_constraint */
    w32(h, 0x003f3a10u, f2u(nx));
    w32(h, 0x003f3a14u, 0u);
    w32(h, 0x003f3a18u, f2u(nz));
    w32(h, 0x003f3a1cu, 0u);
    h->wall_contact_events++;
    return 0x101u;
}

/* Synthetic scene floor probe (00145630): snaps to synthetic floor_y when
 * within vertical probe span [base_y, base_y + rise]. */
static uint32_t synth_floor_query(HGWorldInitHarness *h, uint32_t rise_bits,
                                  uint32_t seg_bottom_addr) {
    float x = u2f(r32(h, seg_bottom_addr + 0u));
    float y_lo = u2f(r32(h, seg_bottom_addr + 4u));
    float z = u2f(r32(h, seg_bottom_addr + 8u));
    float y_hi = y_lo + u2f(rise_bits);
    h->floor_query_events++;
    float floor_y = h->cfg.floor_y;
    if (h->m25 && !h->sfaults.ignore_floor_tiers) {
        /* M25 independent model: the highest raised tier containing (x, z). */
        for (uint32_t i = 0; i < h->spec.tier_count && i < HG_WORLD_MAX_TIERS; ++i) {
            const HGWorldFloorTier *t = &h->spec.tiers[i];
            if (x >= t->min_x && x <= t->max_x && z >= t->min_z && z <= t->max_z &&
                t->height > floor_y)
                floor_y = t->height;
        }
    }
    if (floor_y < y_lo - 0.5f || floor_y > y_hi + 0.5f)
        return 0u;
    w32(h, 0x003f39a0u, f2u(x));
    w32(h, 0x003f39a4u, f2u(floor_y));
    w32(h, 0x003f39a8u, f2u(z));
    w32(h, 0x003f39acu, f2u(1.0f));
    w16(h, 0x003f39c6u, 0u); /* ordinary floor surface type */
    w16(h, 0x003f39c8u, 0u);
    w16(h, 0x003f39ccu, 0u);
    return 0x101u;
}

/* ---- Original synthetic world-init subsystem models --------------------- */

static void synth_init_node_pool(HGWorldInitHarness *h) {
    /* Mirrors 00141ce0 layout: 2048 nodes of 0xd0 at 0046e140..004d613f,
     * free pointer stack at 004d6140..004d813f, top=004d8140, count=2048. */
    memset(h->ee + NODE_POOL_BASE, 0, NODE_POOL_COUNT * NODE_STRIDE);
    for (uint32_t i = 0; i < NODE_POOL_COUNT; ++i)
        w32(h, NODE_STACK_BASE + i * 4u, NODE_POOL_BASE + i * NODE_STRIDE);
    w32(h, NODE_STACK_TOP, NODE_STACK_TOP);
    w32(h, NODE_FREE_COUNT, NODE_POOL_COUNT);
}

static void synth_init_actor_pool(HGWorldInitHarness *h, uint32_t base,
                                  uint32_t stride, uint32_t count, uint8_t tag29) {
    memset(h->ee + base, 0, count * stride);
    for (uint32_t i = 0; i < count; ++i) {
        uint32_t slot = base + i * stride;
        uint32_t next = (i + 1u < count) ? (slot + stride) : 0u;
        w32(h, slot + 0x24u, next);
        w8(h, slot + 0x29u, tag29);
    }
}

static void synth_init_actor_pools(HGWorldInitHarness *h) {
    /* Mirrors 00141f60 layout consumed by real hg_actor_allocate / walkers. */
    synth_init_actor_pool(h, ACTOR_POOL0_BASE, ACTOR_POOL0_STRIDE, ACTOR_POOL0_COUNT, 0u);
    synth_init_actor_pool(h, ACTOR_POOL1_BASE, ACTOR_POOL1_STRIDE, ACTOR_POOL1_COUNT, 1u);
    synth_init_actor_pool(h, ACTOR_POOL2_BASE, ACTOR_POOL2_STRIDE, ACTOR_POOL2_COUNT, 2u);
    memset(h->ee + ACTOR_CTRL_BASE, 0, 0x38u);
    w32(h, ACTOR_CTRL_BASE + 0x00u, ACTOR_POOL0_BASE);
    w32(h, ACTOR_CTRL_BASE + 0x04u, ACTOR_POOL1_BASE);
    w32(h, ACTOR_CTRL_BASE + 0x08u, ACTOR_POOL2_BASE);
    w32(h, ACTOR_CTRL_BASE + 0x0cu, ACTOR_POOL0_COUNT);
    w32(h, ACTOR_CTRL_BASE + 0x10u, ACTOR_POOL1_COUNT);
    w32(h, ACTOR_CTRL_BASE + 0x14u, ACTOR_POOL2_COUNT);
}

static void synth_00141b10_world_reset(HGWorldInitHarness *h) {
    memset(h->ee + WORLD_ENV_ADDR, 0, 0x250u);
    w8(h, GP_BASE + 0x2f78u, 0u);
    w8(h, GP_BASE + 0x2f64u, 0u);
    w8(h, GP_BASE + 0x2f74u, 0u);
    w16(h, 0x0046e10au, 0u);
    w16(h, 0x0046e108u, 0u);
    m25_effect(h, 0x00141b10u, HG_PROV_SYNTHETIC_MODEL);
    record_provenance(h, "world_env_guards_003eaee8", GP_BASE + 0x2f78u, 1u, 0u,
                      HG_PROV_SYNTHETIC_MODEL, 0x00141b10u);
}

static void synth_00141910_pools_reset(HGWorldInitHarness *h) {
    if (h->faults.omit_pool_init)
        return;
    memset(h->ee + 0x003f99b0u, 0, 0x480u);
    memset(h->ee + PLAYER_ADDR, 0, 0x520u);
    memset(h->ee + CAMERA_ADDR, 0, 0x150u);
    synth_init_actor_pools(h);
    synth_init_node_pool(h);
    m25_effect(h, 0x00141910u, HG_PROV_SYNTHETIC_MODEL);
    h->pool_init_ran = true;
    h->world_root_actor = 0u;
    record_provenance(h, "node_pool_free_004d8144", NODE_FREE_COUNT, 4u, NODE_POOL_COUNT,
                      HG_PROV_SYNTHETIC_MODEL, 0x00141910u);
    record_provenance(h, "actor_pool0_free_0053943c", ACTOR_CTRL_BASE + 0x0cu, 4u,
                      ACTOR_POOL0_COUNT, HG_PROV_SYNTHETIC_MODEL, 0x00141910u);
}

static void synth_00141210_spawn_select(HGWorldInitHarness *h) {
    if (h->faults.omit_spawn_init)
        return;
    /* Read original synthetic spawn quad from SYNTH_SPAWN_TABLE (fixture). */
    uint32_t sx = r32(h, SYNTH_SPAWN_TABLE + 0u);
    uint32_t sy = r32(h, SYNTH_SPAWN_TABLE + 4u);
    uint32_t sz = r32(h, SYNTH_SPAWN_TABLE + 8u);
    uint32_t syaw_raw = r32(h, SYNTH_SPAWN_TABLE + 12u);
    uint32_t syaw = hg_angle_wrap(&h->res_env, syaw_raw);

    w32(h, PLAYER_ADDR + 0x30u, sx);
    w32(h, PLAYER_ADDR + 0x34u, sy);
    w32(h, PLAYER_ADDR + 0x38u, sz);
    w32(h, PLAYER_ADDR + 0x3cu, f2u(1.0f));
    w32(h, PLAYER_ADDR + 0x40u, 0u);
    w32(h, PLAYER_ADDR + 0x44u, syaw);
    w32(h, PLAYER_ADDR + 0x48u, 0u);
    w32(h, PLAYER_ADDR + 0x4cu, f2u(1.0f));
    w8(h, PLAYER_ADDR + 0x4a0u, r8(h, 0x003fa1bbu));

    /* Pitch bias +0.05235988 (0x3d567750) matching 001412a0..b8 and 00158968. */
    uint32_t fault = 0u;
    uint32_t biased = hg_ee_fp(0u, 0x3d567750u, r32(h, PLAYER_ADDR + 0x40u), &fault);
    w32(h, PLAYER_ADDR + 0x3c0u, hg_angle_wrap(&h->res_env, biased));
    w32(h, PLAYER_ADDR + 0x3c4u, 0u);
    w32(h, PLAYER_ADDR + 0x3c8u, 0u);
    w32(h, PLAYER_ADDR + 0x3ccu, f2u(1.0f));

    /* Mirror spawn position to scratch 70000040, player+390 history, 700000e0. */
    for (uint32_t i = 0; i < 4u; ++i) {
        uint32_t v = r32(h, PLAYER_ADDR + 0x30u + i * 4u);
        w32(h, SCRATCH_BASE + 0x40u + i * 4u, v);
        w32(h, PLAYER_ADDR + 0x390u + i * 4u, v);
        w32(h, SCRATCH_BASE + 0xe0u + i * 4u, v);
    }
    h->spawn_init_ran = true;
    m25_effect(h, 0x00141210u, HG_PROV_SYNTHETIC_MODEL);
    h->player_pos_prov = HG_PROV_SYNTHETIC_MODEL;
    record_provenance(h, "player_spawn_x_003f9300", PLAYER_ADDR + 0x30u, 4u, sx,
                      HG_PROV_SYNTHETIC_MODEL, 0x00141210u);
    record_provenance(h, "player_spawn_yaw_003f9314", PLAYER_ADDR + 0x44u, 4u, syaw,
                      HG_PROV_C_PRODUCED, 0x001204e0u);
}

static void synth_0013fef0_env_init(HGWorldInitHarness *h) {
    w32(h, 0x003f9e40u, f2u(1.0f));
    w32(h, 0x003f9e44u, f2u(1.0f));
    w32(h, 0x003f9e48u, f2u(1.0f));
    w32(h, 0x003f9e4cu, f2u(1.0f));
    w32(h, 0x0046d90cu, 1000u); /* event flags enabled (non-zero gameplay timer) */
    m25_effect(h, 0x0013fef0u, HG_PROV_SYNTHETIC_MODEL);
    record_provenance(h, "event_timer_0046d90c", 0x0046d90cu, 4u, 1000u,
                      HG_PROV_SYNTHETIC_MODEL, 0x0013fef0u);
}

static void synth_0012bf70_camera_init(HGWorldInitHarness *h) {
    if (h->faults.omit_camera_init)
        return;
    w32(h, CAMERA_ADDR + 0x00u, 0u);
    w32(h, CAMERA_ADDR + 0x04u, 0u);
    w32(h, CAMERA_ADDR + 0x08u, 0u);
    w32(h, CAMERA_ADDR + 0x0cu, f2u(1.0f));
    w32(h, CAMERA_ADDR + 0x10u, 0u);
    w32(h, CAMERA_ADDR + 0x14u, 0u);
    w32(h, CAMERA_ADDR + 0x18u, f2u(20.0f));
    w32(h, CAMERA_ADDR + 0x1cu, f2u(1.0f));
    w32(h, CAMERA_ADDR + 0x20u, 0u);
    w32(h, CAMERA_ADDR + 0x24u, f2u(1.0f));
    w32(h, CAMERA_ADDR + 0x28u, 0u);
    w32(h, CAMERA_ADDR + 0x2cu, f2u(1.0f));
    w32(h, CAMERA_ADDR + 0xd0u, 0u);
    w32(h, CAMERA_ADDR + 0xd4u, 0u);
    w32(h, CAMERA_ADDR + 0xd8u, 0u);
    w32(h, CAMERA_ADDR + 0xdcu, f2u(1.0f));
    synth_camera_lookat(h, CAMERA_ADDR);
    h->camera_init_ran = true;
    m25_effect(h, 0x0012bf70u, HG_PROV_SYNTHETIC_MODEL);
    record_provenance(h, "camera_init_003f9860", CAMERA_ADDR, 0x150u,
                      r32(h, CAMERA_ADDR + 0x18u), HG_PROV_SYNTHETIC_MODEL, 0x0012bf70u);
}

static void synth_0013a4c0_area_map_bind(HGWorldInitHarness *h, uint32_t block) {
    memset(h->ee + block, 0, 0x64u);
    w32(h, block + 0x00u, CAMERA_ADDR);
    m25_effect(h, 0x0013a4c0u, HG_PROV_SYNTHETIC_MODEL);
    uint32_t area_handle = r32(h, HANDLE_TABLE + HANDLE_AREA * 4u);
    if (area_handle != 0u) {
        uint32_t geom = hg_actor_resource_lookup(&h->res_env, area_handle, 0x01000000u);
        uint32_t coll = hg_actor_resource_lookup(&h->res_env, area_handle, 0x0d000000u);
        w32(h, block + 0x04u, geom);
        w32(h, block + 0x58u, coll);
        record_provenance(h, "area_map_geom_003f97f4", block + 0x04u, 4u, geom,
                          HG_PROV_C_PRODUCED, 0x00138370u);
        record_provenance(h, "area_map_coll_003f9848", block + 0x58u, 4u, coll,
                          HG_PROV_C_PRODUCED, 0x00138370u);
    }
}

static void synth_00189aa0_spawn_world_actors(HGWorldInitHarness *h) {
    /* Allocate synthetic world root actor onto main list 00539448 via REAL
     * hg_actor_allocate (001429b0). */
    uint32_t actor = hg_actor_allocate(&h->res_env, 0u, 8u, SYNTH_ROOT_ACTOR_CB);
    if (actor != 0u) {
        w32(h, actor + 0x04u, 8u);
        w8(h, actor + 0x18u, 0u);
        h->world_root_actor = actor;
        record_provenance(h, "world_root_actor", actor, 0x3d0u, SYNTH_ROOT_ACTOR_CB,
                          HG_PROV_C_PRODUCED, 0x001429b0u);
    }
}

static uint32_t synth_00196ad0_spawn_type2_node(HGWorldInitHarness *h, uint32_t id) {
    uint32_t cb = SYNTH_NODE_AUX2_CB;
    if (id == 0x31u)
        cb = SYNTH_NODE_ANCHOR_CB;
    else if (id == 0xaau)
        cb = SYNTH_NODE_AUX1_CB;
    uint32_t node = hg_actor_allocate(&h->res_env, 2u, 7u, cb);
    if (node != 0u) {
        w32(h, node + 0x04u, id);
        w8(h, node + 0x18u, 1u);
        w8(h, node + 0x19u, 0u);
        m25_own(h, HG_OWN_ACTOR_ALLOC, node, PLAYER_ADDR, 0x00196ad0u);
    } else if (h->m25) {
        m25_latch(h, HG_WORLD_ERR_CAPACITY);
    }
    return node;
}

/* ======================================================================
 * M25 session model. Everything below runs only when h->m25 is set.
 * ====================================================================== */

enum {
    SYNTH_PROP_CB = 0x00a89200u,
    SYNTH_FILLER_CB = 0x00a89300u
};
#define M25_OWNER_WORLD 0u
#define M25_OWNER_HOG 0xfe25a110u
#define M25_OWNER_FILLER 0xfe25f111u

/* 0012f850 service order, read from the authenticated ELF (jal targets at
 * 0012f884..0012f934; the body is linear with no conditional calls). */
static const uint32_t M25_COORDINATOR_ORDER[18] = {
    0x0031bb80u, 0x00198f60u, 0x0017f6d0u, 0x0017f2d0u, 0x00158560u, 0x0012c790u,
    0x0013b6e0u, 0x001430a0u, 0x00143100u, 0x0012bc70u, 0x0018c520u, 0x00143150u,
    0x001431a0u, 0x0025c170u, 0x00182480u, 0x0013ce80u, 0x00137890u, 0x00189be0u
};

static bool m25_pool_of(uint32_t actor, uint32_t *index, uint32_t *base,
                        uint32_t *stride, uint32_t *count) {
    static const uint32_t bases[3] = {ACTOR_POOL0_BASE, ACTOR_POOL1_BASE, ACTOR_POOL2_BASE};
    static const uint32_t strides[3] = {ACTOR_POOL0_STRIDE, ACTOR_POOL1_STRIDE, ACTOR_POOL2_STRIDE};
    static const uint32_t counts[3] = {ACTOR_POOL0_COUNT, ACTOR_POOL1_COUNT, ACTOR_POOL2_COUNT};
    for (uint32_t i = 0; i < 3u; ++i) {
        if (actor >= bases[i] && actor < bases[i] + strides[i] * counts[i] &&
            (actor - bases[i]) % strides[i] == 0u) {
            *index = i; *base = bases[i]; *stride = strides[i]; *count = counts[i];
            return true;
        }
    }
    return false;
}

static bool m25_node_valid(uint32_t node) {
    return node >= NODE_POOL_BASE && node < NODE_POOL_BASE + NODE_POOL_COUNT * NODE_STRIDE &&
           (node - NODE_POOL_BASE) % NODE_STRIDE == 0u;
}

/* Independent model of a node release: the inverse of C 00141de0's pop. No
 * retail release routine is characterized; double frees are refused. */
static bool m25_release_node(HGWorldInitHarness *h, uint32_t node, uint32_t owner) {
    uint32_t top = r32(h, NODE_STACK_TOP);
    if (!m25_node_valid(node) || top < NODE_STACK_BASE || top >= NODE_STACK_TOP ||
        (top & 3u) != 0u || r32(h, NODE_FREE_COUNT) >= NODE_POOL_COUNT)
        return false;
    w32(h, top, node);
    w32(h, NODE_STACK_TOP, top + 4u);
    w32(h, NODE_FREE_COUNT, r32(h, NODE_FREE_COUNT) + 1u);
    m25_own(h, HG_OWN_NODE_RELEASE, node, owner, HG_EFFECT_RELEASE_NODES);
    return true;
}

/* Independent model of an actor retire: unlink from the list chosen by the
 * C registration rule (pool tag +29, priority +28), then push the slot back
 * on its pool free list with the 00141f60 slot layout. */
static bool m25_retire_actor(HGWorldInitHarness *h, uint32_t actor) {
    uint32_t index, base, stride, count;
    if (!m25_pool_of(actor, &index, &base, &stride, &count))
        return false;
    uint32_t head = ACTOR_POOL0_BASE + (r8(h, actor + 0x29u) == 2u ? 0x61308u : 0x612f8u);
    if (r8(h, actor + 0x28u) != 0u)
        head += 8u;
    uint32_t prev = r32(h, actor + 0x20u), next = r32(h, actor + 0x24u);
    if (prev != 0u) w32(h, prev + 0x24u, next); else w32(h, head, next);
    if (next != 0u) w32(h, next + 0x20u, prev); else w32(h, head + 4u, prev);
    memset(h->ee + actor, 0, stride);
    w8(h, actor + 0x29u, index);
    uint32_t free_head = ACTOR_CTRL_BASE + index * 4u;
    w32(h, actor + 0x24u, r32(h, free_head));
    w32(h, free_head, actor);
    w32(h, free_head + 0x0cu, r32(h, free_head + 0x0cu) + 1u);
    m25_own(h, HG_OWN_ACTOR_RELEASE, actor, 0u, HG_EFFECT_RETIRE_ACTOR);
    return true;
}

typedef struct {
    uint8_t node_live[NODE_POOL_COUNT];
    uint32_t node_owner[NODE_POOL_COUNT];
    uint32_t node_seq[NODE_POOL_COUNT];
    uint32_t actors[ACTOR_POOL0_COUNT + ACTOR_POOL1_COUNT + ACTOR_POOL2_COUNT];
    uint32_t actor_seq[ACTOR_POOL0_COUNT + ACTOR_POOL1_COUNT + ACTOR_POOL2_COUNT];
    uint32_t actor_count;
} M25LiveSet;

/* Replay the model's own ownership ledger (the checker keeps its own). */
static void m25_live_set(const HGWorldInitHarness *h, M25LiveSet *live) {
    memset(live, 0, sizeof(*live));
    for (size_t i = 0; i < h->ownership_count; ++i) {
        const HGOwnershipEvent *ev = &h->ownership[i];
        if (ev->op == HG_OWN_NODE_ALLOC || ev->op == HG_OWN_NODE_RELEASE) {
            if (!m25_node_valid(ev->object))
                continue;
            uint32_t k = (ev->object - NODE_POOL_BASE) / NODE_STRIDE;
            live->node_live[k] = ev->op == HG_OWN_NODE_ALLOC;
            live->node_owner[k] = ev->owner;
            live->node_seq[k] = ev->seq;
        } else if (ev->op == HG_OWN_ACTOR_ALLOC || ev->op == HG_OWN_ACTOR_RELEASE) {
            uint32_t j = 0;
            while (j < live->actor_count && live->actors[j] != ev->object)
                ++j;
            if (ev->op == HG_OWN_ACTOR_ALLOC && j == live->actor_count &&
                live->actor_count < sizeof(live->actors) / sizeof(live->actors[0])) {
                live->actors[live->actor_count] = ev->object;
                live->actor_seq[live->actor_count++] = ev->seq;
            } else if (ev->op == HG_OWN_ACTOR_RELEASE && j < live->actor_count) {
                live->actors[j] = live->actors[--live->actor_count];
                live->actor_seq[j] = live->actor_seq[live->actor_count];
            }
        }
    }
}

/* Retire every live allocation: bone nodes first (newest first), then actors
 * (newest first). Teardown faults withhold one node or one prop. */
static void m25_retire_all(HGWorldInitHarness *h, bool teardown) {
    M25LiveSet *live = calloc(1u, sizeof(*live));
    if (!live) {
        m25_latch(h, HG_WORLD_ERR_CAPACITY);
        return;
    }
    m25_live_set(h, live);
    uint32_t kept_prop = 0u;
    if (teardown && h->sfaults.leave_prop_linked && h->live_prop_count > 0u)
        kept_prop = h->live_props[0];
    bool leaked = false;
    for (;;) {
        uint32_t best = NODE_POOL_COUNT, best_seq = 0u;
        for (uint32_t k = 0; k < NODE_POOL_COUNT; ++k) {
            if (live->node_live[k] && (best == NODE_POOL_COUNT || live->node_seq[k] > best_seq)) {
                best = k;
                best_seq = live->node_seq[k];
            }
        }
        if (best == NODE_POOL_COUNT)
            break;
        live->node_live[best] = 0u;
        uint32_t owner = live->node_owner[best];
        if (kept_prop != 0u && owner == kept_prop)
            continue;
        if (teardown && h->sfaults.leak_player_node && owner == PLAYER_ADDR && !leaked) {
            leaked = true;
            continue;
        }
        if (!m25_release_node(h, NODE_POOL_BASE + best * NODE_STRIDE, owner))
            m25_latch(h, HG_WORLD_ERR_NODE_LEAK);
    }
    m25_effect(h, HG_EFFECT_RELEASE_NODES, HG_PROV_SYNTHETIC_MODEL);
    while (live->actor_count > 0u) {
        uint32_t j = 0;
        for (uint32_t i = 1; i < live->actor_count; ++i)
            if (live->actor_seq[i] > live->actor_seq[j])
                j = i;
        uint32_t actor = live->actors[j];
        live->actors[j] = live->actors[--live->actor_count];
        live->actor_seq[j] = live->actor_seq[live->actor_count];
        if (actor == kept_prop)
            continue;
        if (!m25_retire_actor(h, actor))
            m25_latch(h, HG_WORLD_ERR_STALE_ACTORS);
    }
    m25_effect(h, HG_EFFECT_RETIRE_ACTOR, HG_PROV_SYNTHETIC_MODEL);
    h->live_prop_count = 0u;
    h->world_root_actor = 0u;
    free(live);
}

static void m25_clear_player_camera(HGWorldInitHarness *h) {
    memset(h->ee + PLAYER_ADDR, 0, 0x520u);
    memset(h->ee + CAMERA_ADDR, 0, 0x150u);
    m25_effect(h, HG_EFFECT_CLEAR_PLAYER_CAMERA, HG_PROV_SYNTHETIC_MODEL);
}

/* 00189aa0 (M25 independent model): root actors and props allocated through
 * C 001429b0; prop bone nodes bound through C 00141e40/00141de0. */
static void m25_spawn_world_actors(HGWorldInitHarness *h) {
    m25_effect(h, 0x00189aa0u, HG_PROV_SYNTHETIC_MODEL);
    if (h->sfaults.exhaust_actor_pool0) {
        uint32_t keep = h->spec.root_actors - 1u;
        while (r32(h, ACTOR_CTRL_BASE + 0x0cu) > keep) {
            uint32_t filler = hg_actor_allocate(&h->res_env, 0u, 0x7eu, SYNTH_FILLER_CB);
            if (filler == 0u)
                break;
            m25_own(h, HG_OWN_ACTOR_ALLOC, filler, M25_OWNER_FILLER, 0x001429b0u);
        }
    }
    for (uint32_t i = 0; i < h->spec.root_actors; ++i) {
        uint32_t actor = hg_actor_allocate(&h->res_env, 0u, 8u, SYNTH_ROOT_ACTOR_CB);
        if (actor == 0u) {
            m25_latch(h, HG_WORLD_ERR_CAPACITY);
            return;
        }
        w32(h, actor + 0x04u, 8u);
        w8(h, actor + 0x18u, 0u);
        m25_own(h, HG_OWN_ACTOR_ALLOC, actor, M25_OWNER_WORLD, 0x001429b0u);
        if (i == 0u)
            h->world_root_actor = actor;
    }
    for (uint32_t i = 0; i < h->spec.prop_count; ++i) {
        const HGWorldPropSpec *ps = &h->spec.props[i];
        uint32_t actor = hg_actor_allocate(&h->res_env, ps->priority ? 0x80u : 0u, 9u, SYNTH_PROP_CB);
        if (actor == 0u) {
            m25_latch(h, HG_WORLD_ERR_CAPACITY);
            return;
        }
        w32(h, actor + 0x04u, 9u + i);
        m25_own(h, HG_OWN_ACTOR_ALLOC, actor, M25_OWNER_WORLD, 0x001429b0u);
        h->live_props[h->live_prop_count++] = actor;
        uint32_t object = actor + 0x90u;
        uint32_t got = ps->nodes ? hg_actor_allocate_nodes(&h->res_env, object, ps->nodes) : 0u;
        w32(h, object + 0x58u, object + 0x60u + r32(h, object + 0x54u) * 4u);
        w32(h, object + 0x54u, got);
        for (uint32_t k = 0; k < got; ++k)
            m25_own(h, HG_OWN_NODE_ALLOC, r32(h, object + 0x60u + k * 4u), actor, 0x00141de0u);
        if (got != ps->nodes) {
            m25_latch(h, HG_WORLD_ERR_CAPACITY);
            return;
        }
    }
    if (h->sfaults.exhaust_nodes_before_player) {
        /* Allocation fault: another owner leaves one node fewer than the
         * player model needs. The nodes are taken through C 00141de0. */
        while (r32(h, NODE_FREE_COUNT) >= h->cfg.player_bones) {
            uint32_t node = hg_actor_take_node(&h->res_env);
            m25_own(h, HG_OWN_NODE_ALLOC, node, M25_OWNER_HOG, 0x00141de0u);
        }
    }
}

/* 001e3440(PLAYER, f12 = +8.0, f13 = -3.0) as a floor snap. The argument
 * constants are original bytes (001585c8..001585d4, loaded before the jal);
 * reading them as an upward and downward probe span is an inference. */
static void m25_floor_snap(HGWorldInitHarness *h, float up, float down) {
    for (uint32_t i = 0; i < 4u; ++i)
        w32(h, SCRATCH_BASE + 0xbe0u + i * 4u, r32(h, PLAYER_ADDR + 0x30u + i * 4u));
    w32(h, SCRATCH_BASE + 0xbe4u, f2u(u2f(r32(h, PLAYER_ADDR + 0x34u)) + down));
    if (synth_floor_query(h, f2u(up - down), SCRATCH_BASE + 0xbe0u) != 0u) {
        w32(h, PLAYER_ADDR + 0x34u, r32(h, 0x003f39a4u));
        w8(h, PLAYER_ADDR + 0x2c3u, 1u);
    }
    m25_effect(h, 0x001e3440u, HG_PROV_SYNTHETIC_MODEL);
}

/* Common tail 00158968..00158a0c. Characterized from original bytes and
 * matching the existing C state-1 tail (hg_player_dispatch): f12 = +3c0 -
 * 0x3d567750 is formed by sub.s in the jal 001204e0 delay slot; the 003f9f6e
 * sum sign-extends +4ac (lb) and +45a (lh) and adds 1 when +464 is zero; lq/sq
 * copy all four lanes of +30 to +390. */
static void m25_state_tail(HGWorldInitHarness *h) {
    uint32_t fault = 0u;
    uint32_t f12 = hg_ee_fp(1u, r32(h, PLAYER_ADDR + 0x3c0u), 0x3d567750u, &fault);
    w32(h, PLAYER_ADDR + 0x40u, hg_angle_wrap(&h->res_env, f12));
    uint32_t sum = r8(h, 0x003f9e3fu) + r8(h, PLAYER_ADDR + 0x44bu);
    sum += (uint32_t)(int32_t)(int8_t)r8(h, PLAYER_ADDR + 0x4acu);
    sum += r8(h, PLAYER_ADDR + 0x4adu);
    sum += (uint32_t)(int32_t)(int16_t)r16(h, PLAYER_ADDR + 0x45au);
    sum += r8(h, PLAYER_ADDR + 0x49fu);
    sum += r8(h, 0x003fa067u);
    if (r8(h, PLAYER_ADDR + 0x464u) == 0u)
        sum++;
    w8(h, 0x003f9f6eu, sum != 0u ? 1u : 0u);
    for (uint32_t i = 0; i < 4u; ++i)
        w32(h, PLAYER_ADDR + 0x390u + i * 4u, r32(h, PLAYER_ADDR + 0x30u + i * 4u));
    m25_effect(h, 0x00158968u, HG_PROV_ORIGINAL_BYTES);
    record_provenance(h, "m25_input_block_003f9f6e", 0x003f9f6eu, 1u, r8(h, 0x003f9f6eu),
                      HG_PROV_ORIGINAL_BYTES, 0x00158968u);
}

/* 00158560 state 0 (jump table 003db540[0] = 001585c0), M25 session. */
static void m25_state0(HGWorldInitHarness *h) {
    w32(h, 0x0095e57cu, 0u);
    for (uint32_t i = 0; i < 4u; ++i)
        w32(h, SCRATCH_BASE + 0xe0u + i * 4u, r32(h, PLAYER_ADDR + 0x30u + i * 4u));

    record_call(h, 0x00158a10u, PLAYER_ADDR, 0u, HG_PROV_C_PRODUCED);
    hg_player_initialize_fixed(&h->res_env, PLAYER_ADDR);
    m25_effect(h, 0x00158a10u, HG_PROV_C_PRODUCED);
    uint32_t object = PLAYER_ADDR + 0x90u;
    uint32_t model = r32(h, object + 0x10u);
    uint32_t bound = r32(h, object + 0x54u), table = r32(h, object + 0x58u);
    for (uint32_t k = 0; k < bound && k < HG_WORLD_MAX_PLAYER_BONES; ++k)
        m25_own(h, HG_OWN_NODE_ALLOC, r32(h, table + k * 4u), PLAYER_ADDR, 0x00141de0u);
    uint32_t requested = model ? r32(h, model + 0x08u) : 0u;
    if (model == 0u || requested < HG_WORLD_MIN_PLAYER_BONES || requested > HG_WORLD_MAX_PLAYER_BONES)
        m25_latch(h, HG_WORLD_ERR_INVALID_PLAYER_BINDING);
    else if (bound != requested)
        m25_latch(h, HG_WORLD_ERR_CAPACITY);
    record_provenance(h, "m25_player_nodes_003f93b4", object + 0x54u, 4u, bound,
                      HG_PROV_C_PRODUCED, 0x00139d20u);

    record_call(h, 0x001e3440u, PLAYER_ADDR, f2u(8.0f), HG_PROV_SYNTHETIC_MODEL);
    m25_floor_snap(h, 8.0f, -3.0f);
    record_provenance(h, "m25_floor_snap_y_003f9304", PLAYER_ADDR + 0x34u, 4u,
                      r32(h, PLAYER_ADDR + 0x34u), HG_PROV_SYNTHETIC_MODEL, 0x001e3440u);

    /* Selector 001585e0..001586f4 (unsigned lbu reads). Only the ordinary
     * entry is supported; event entries fail closed with phase left at 0. */
    uint32_t t0 = r8(h, 0x003fa1b0u), t1 = r8(h, 0x003fa1b1u), t2 = r8(h, 0x003fa1b2u);
    if ((t0 == 1u && t1 == 0u && t2 == 1u) || (t0 == 0u && t1 == 8u && t2 == 1u) ||
        r8(h, 0x0046e106u) == 1u || r8(h, PLAYER_ADDR + 0x4a0u) == 1u) {
        m25_latch(h, HG_WORLD_ERR_UNSUPPORTED_ENTRY);
        return;
    }
    w8(h, PLAYER_ADDR + 0x18u, 1u); /* stored in the jal 00158470 delay slot */
    record_call(h, 0x00158470u, PLAYER_ADDR, 0u, HG_PROV_UNKNOWN);
    m25_effect(h, 0x00158470u, HG_PROV_UNKNOWN); /* policy: no area-entry event */
    m25_state_tail(h);
    h->state0_init_ran = true;
    h->player_pos_prov = HG_PROV_C_PRODUCED;
    record_provenance(h, "m25_player_phase_003f92e8", PLAYER_ADDR + 0x18u, 1u, 1u,
                      HG_PROV_ORIGINAL_BYTES, 0x001586c4u);
}

/* 0012f850 for a session: the 18 services in original order. C runs where it
 * exists; the rest are recorded seams with no modeled effect. */
static void m25_coordinator(HGWorldInitHarness *h) {
    record_call(h, 0x0012f850u, 0u, 0u, HG_PROV_C_PRODUCED);
    h->coordinator_ticks++;
    hg_frame_advance(&h->res_env); /* records 0031bb80 through the bus */
    record_call(h, 0x00198f60u, 0u, 0u, HG_PROV_UNKNOWN);
    record_call(h, 0x0017f6d0u, WORLD_ENV_ADDR, 0u, HG_PROV_UNKNOWN);
    record_call(h, 0x0017f2d0u, 0u, 0u, HG_PROV_UNKNOWN);
    uint32_t phase = r8(h, PLAYER_ADDR + 0x18u);
    record_call(h, 0x00158560u, PLAYER_ADDR, phase,
                phase == 0u ? HG_PROV_SYNTHETIC_MODEL : HG_PROV_C_PRODUCED);
    if (phase == 0u) {
        m25_state0(h);
    } else {
        hg_player_dispatch_fixed(&h->res_env, SYNTH_FRAME_STACK);
        if (h->domain_fault_count == 0u)
            h->player_pos_prov = HG_PROV_C_PRODUCED;
    }
    if (h->init_error != HG_WORLD_OK || h->domain_fault_count != 0u)
        return; /* fail closed: no service consumes an incomplete player */
    record_call(h, 0x0012c790u, 0u, 0u, HG_PROV_C_PRODUCED);
    bool skip_camera = h->sfaults.skip_camera_from != 0u &&
                       h->post_ready_updates >= h->sfaults.skip_camera_from;
    if (!skip_camera) {
        bool first = !h->camera_updated_this_cycle;
        hg_camera_update_fixed(&h->res_env);
        h->camera_updated_this_cycle = true;
        h->camera_prov = HG_PROV_C_PRODUCED;
        if (first)
            m25_effect(h, 0x0012c790u, HG_PROV_C_PRODUCED);
    }
    record_call(h, 0x0013b6e0u, AREA_MAP_BLOCK, 0u, HG_PROV_UNKNOWN);
    record_call(h, 0x001430a0u, 0u, 0u, HG_PROV_C_PRODUCED);
    hg_actor_update_main(&h->res_env);
    record_call(h, 0x00143100u, 0u, 0u, HG_PROV_C_PRODUCED);
    hg_actor_update_priority(&h->res_env);
    record_call(h, 0x0012bc70u, 0u, 0u, HG_PROV_UNKNOWN); /* player draw: GS output */
    record_call(h, 0x0018c520u, 0u, 0u, HG_PROV_UNKNOWN);
    record_call(h, 0x00143150u, 0u, 0u, HG_PROV_C_PRODUCED);
    hg_actor_update_type2(&h->res_env);
    record_call(h, 0x001431a0u, 0u, 0u, HG_PROV_C_PRODUCED);
    hg_actor_update_type2_priority(&h->res_env);
    record_call(h, 0x0025c170u, 0u, 0u, HG_PROV_UNKNOWN);
    record_call(h, 0x00182480u, 0u, 0u, HG_PROV_UNKNOWN);
    record_call(h, 0x0013ce80u, AREA_MAP_BLOCK, 0u, HG_PROV_UNKNOWN);
    record_call(h, 0x00137890u, 0u, 0u, HG_PROV_C_PRODUCED);
    hg_counter_advance(&h->res_env);
    record_call(h, 0x00189be0u, 0u, 0u, HG_PROV_UNKNOWN);
}

/* State-0 branch of 00158560 (verified against ELF 001585c0..001586f4 and
 * common tail 00158968..00158a0c): calls real hg_player_initialize_fixed
 * (00158a10), runs synthetic initial floor snap (001e3440), sets PLAYER+0x18 = 1,
 * and updates PLAYER+0x40 / 003f9f6e / PLAYER+0x390. */
static void synth_player_dispatch_state0(HGWorldInitHarness *h) {
    w32(h, 0x0095e57cu, 0u);
    for (uint32_t i = 0; i < 4u; ++i)
        w32(h, SCRATCH_BASE + 0xe0u + i * 4u, r32(h, PLAYER_ADDR + 0x30u + i * 4u));

    record_call(h, 0x00158a10u, PLAYER_ADDR, 0u, HG_PROV_C_PRODUCED);
    hg_player_initialize_fixed(&h->res_env, PLAYER_ADDR);

    /* Initial floor snap (001e3440(player, +8.0, -3.0)): snap Y to floor_y. */
    record_call(h, 0x001e3440u, PLAYER_ADDR, f2u(8.0f), HG_PROV_SYNTHETIC_MODEL);
    w32(h, SCRATCH_BASE + 0xbe0u, r32(h, PLAYER_ADDR + 0x30u));
    w32(h, SCRATCH_BASE + 0xbe4u, f2u(u2f(r32(h, PLAYER_ADDR + 0x34u)) - 3.0f));
    w32(h, SCRATCH_BASE + 0xbe8u, r32(h, PLAYER_ADDR + 0x38u));
    w32(h, SCRATCH_BASE + 0xbecu, f2u(1.0f));
    if (synth_floor_query(h, f2u(11.0f), SCRATCH_BASE + 0xbe0u) != 0u) {
        w32(h, PLAYER_ADDR + 0x34u, r32(h, 0x003f39a4u));
        w8(h, PLAYER_ADDR + 0x2c3u, 1u);
    }

    /* Transition player phase 0 -> 1 (delay slot 001586c4 on area (1,0,0)). */
    w8(h, PLAYER_ADDR + 0x18u, 1u);

    /* Common tail 00158968..00158a0c. */
    uint32_t fault = 0u;
    uint32_t pitch = hg_ee_fp(1u, r32(h, PLAYER_ADDR + 0x3c0u), 0x3d567750u, &fault);
    w32(h, PLAYER_ADDR + 0x40u, hg_angle_wrap(&h->res_env, pitch));
    w8(h, 0x003f9f6eu, 0u);
    for (uint32_t i = 0; i < 4u; ++i)
        w32(h, PLAYER_ADDR + 0x390u + i * 4u, r32(h, PLAYER_ADDR + 0x30u + i * 4u));

    h->state0_init_ran = true;
    h->player_pos_prov = HG_PROV_C_PRODUCED;
    record_provenance(h, "player_phase_003f92e8", PLAYER_ADDR + 0x18u, 1u, 1u,
                      HG_PROV_SYNTHETIC_MODEL, 0x001585c0u);
    record_provenance(h, "player_node_count_003f93b4", PLAYER_ADDR + 0x90u + 0x54u, 4u,
                      r32(h, PLAYER_ADDR + 0x90u + 0x54u), HG_PROV_C_PRODUCED, 0x00139d20u);
}

/* Coordinator 0012f850 connected step: runs real hg_frame_advance, player
 * dispatch (state 0 on first entry unless fault-omitted, then real
 * hg_player_dispatch_fixed on state 1), real hg_camera_update_fixed, real
 * hg_actor_update_main/type2, and real hg_counter_advance. */
static void run_coordinator_step(HGWorldInitHarness *h) {
    record_call(h, 0x0012f850u, 0u, 0u, HG_PROV_C_PRODUCED);
    hg_frame_advance(&h->res_env);

    /* Optional negative control: swap camera before player initialization. */
    if (h->faults.swap_camera_before_player && !h->state0_init_ran) {
        h->order_violation_seen = true;
        record_call(h, 0x0012c790u, 0u, 0u, HG_PROV_C_PRODUCED);
        hg_camera_update_fixed(&h->res_env);
        return;
    }

    uint32_t phase = r8(h, PLAYER_ADDR + 0x18u);
    if (phase == 0u && !h->faults.omit_state0_init) {
        synth_player_dispatch_state0(h);
    } else {
        record_call(h, 0x00158560u, PLAYER_ADDR, phase, HG_PROV_C_PRODUCED);
        hg_player_dispatch_fixed(&h->res_env, SYNTH_FRAME_STACK);
        if (h->domain_fault_count == 0u && !h->position_injected)
            h->player_pos_prov = HG_PROV_C_PRODUCED;
    }

    if (!h->faults.omit_camera_init && h->domain_fault_count == 0u) {
        record_call(h, 0x0012c790u, 0u, 0u, HG_PROV_C_PRODUCED);
        hg_camera_update_fixed(&h->res_env);
        h->camera_updated_this_cycle = true;
        h->camera_prov = HG_PROV_C_PRODUCED;
    }

    record_call(h, 0x001430a0u, 0u, 0u, HG_PROV_C_PRODUCED);
    hg_actor_update_main(&h->res_env);
    record_call(h, 0x00143150u, 0u, 0u, HG_PROV_C_PRODUCED);
    hg_actor_update_type2(&h->res_env);
    hg_counter_advance(&h->res_env);
}

/* ---- Unified HGResourceEnv bus router ---------------------------------- */

static uint32_t bus_call(void *user, uint32_t entry,
                         uint32_t a0, uint32_t a1, uint32_t a2, uint32_t a3) {
    HGWorldInitHarness *h = user;
    uint32_t result = 0u;

    /* Host-only protocol seams emitted by existing C functions. */
    if (entry == HG_DOMAIN_FAULT) {
        h->domain_fault_count++;
        h->last_domain_fault = a0;
        record_call(h, entry, a0, a1, HG_PROV_C_PRODUCED);
        return 0u;
    }
    if (entry == HG_GUEST_FRAME) {
        if (a1 == 1u) {
            h->guest_sp -= a0;
            return h->guest_sp;
        }
        h->guest_sp += a0;
        return 0u;
    }
    if (entry == HG_CALL_FP_REGS) {
        h->pending_f12 = a0;
        h->pending_f13 = a1;
        h->pending_f14 = a2;
        h->has_pending_fp = true;
        return 0u;
    }
    if (entry == HG_CALL_T_REGS) {
        h->pending_t0 = a0;
        return 0u;
    }
    if (entry == HG_CALL_RETURN || entry == HG_SERVICE_MARK ||
        entry == HG_EXTERNAL_CALL || entry == HG_CALL_WIDE_A3 ||
        entry == HG_CALL_EXTRA_FP || entry == 0xfffffffcu) {
        return 0u;
    }
    if (entry == HG_WAIT_INTERRUPT) {
        hg_vblank_start_handler(&h->res_env);
        hg_vblank_end_handler(&h->res_env);
        return 0u;
    }

    /* Core libc guest memory operations (00119ac8 memset, 00119910 memcpy). */
    if (entry == 0x00119ac8u) {
        if (a2 <= EE_RAM_SIZE && a0 <= EE_RAM_SIZE - a2)
            memset(h->ee + a0, (int)(a1 & 0xffu), a2);
        else if (a0 >= SCRATCH_BASE && a0 <= SCRATCH_BASE + SCRATCH_SIZE - a2)
            memset(h->scratch + (a0 - SCRATCH_BASE), (int)(a1 & 0xffu), a2);
        return a0;
    }
    if (entry == 0x00119910u) {
        for (uint32_t i = 0; i < a2; ++i)
            w8(h, a0 + i, r8(h, a1 + i));
        return a0;
    }

    /* Existing reconstructed lifecycle & front-end C routers! */
    if (hg_lifecycle_call(&h->res_env, entry, a0, a1, a2, a3, &result)) {
        record_call(h, entry, a0, a1, HG_PROV_C_PRODUCED);
        return result;
    }
    if (hg_front_end_call(&h->res_env, entry, a0, a1, a2, a3, &result)) {
        record_call(h, entry, a0, a1, HG_PROV_C_PRODUCED);
        return result;
    }

    /* Direct C resource/actor/player/camera helpers reachable via bus. */
    switch (entry) {
    case 0x0012f850u:
        if (h->m25)
            m25_coordinator(h);
        else
            run_coordinator_step(h);
        return 0u;
    case 0x0031bb80u:
        if (h->m25)
            record_call(h, entry, 0u, 0u, HG_PROV_C_PRODUCED);
        hg_frame_edge_sync(&h->res_env);
        return 0u;
    case 0x0012fb30u:
        if (!h->faults.retain_stale_handle)
            hg_resource_clear_dynamic_handles(&h->res_env);
        record_call(h, entry, a0, a1, HG_PROV_C_PRODUCED);
        return 0u;
    case 0x00138b70u: return hg_flk5_count(&h->res_env, a0);
    case 0x00138af0u: return hg_flk5_entry(&h->res_env, a0, a1);
    case 0x00138b30u: return hg_flk5_type(&h->res_env, a0, a1);
    case 0x00138370u: return hg_actor_resource_lookup(&h->res_env, a0, a1);
    case 0x001429b0u: return hg_actor_allocate(&h->res_env, a0, a1, a2);
    case 0x00141de0u: /* M25 sessions: node pop reached through C 00141e40 */
        if (!h->m25)
            break;
        record_call(h, entry, 0u, 0u, HG_PROV_C_PRODUCED);
        return hg_actor_take_node(&h->res_env);
    case 0x00141e40u:
        if (!h->m25)
            break;
        record_call(h, entry, a0, a1, HG_PROV_C_PRODUCED);
        return hg_actor_allocate_nodes(&h->res_env, a0, a1);
    case 0x001204e0u: return hg_angle_wrap(&h->res_env, a0);
    case 0x00143530u: return hg_resource_sqrt(a0);
    case 0x0015ebc0u: return hg_player_static_constraint_fixed(&h->res_env, a0, a1);
    case 0x0015fc10u: hg_player_grounded_fixed(&h->res_env, a0); return 0u;
    case 0x0015e420u: return hg_player_floor(&h->res_env, a0);

    /* World-initialization sequence called by real hg_gameplay_frame sub 0. */
    case 0x0025c0f0u:
    case 0x00182430u:
        record_call(h, entry, a0, a1, HG_PROV_SYNTHETIC_MODEL);
        return 0u;
    case 0x00141b10u:
        record_call(h, entry, a0, a1, HG_PROV_SYNTHETIC_MODEL);
        synth_00141b10_world_reset(h);
        if (h->faults.swap_spawn_before_pool_clear && h->current_stage == STAGE_WORLD_INIT) {
            /* Deliberate ordering defect: spawn runs BEFORE 00141910 clears PLAYER! */
            h->order_violation_seen = true;
            record_call(h, 0x00141210u, 0u, 0u, HG_PROV_SYNTHETIC_MODEL);
            synth_00141210_spawn_select(h);
        }
        return 0u;
    case 0x00141910u:
        record_call(h, entry, a0, a1, HG_PROV_SYNTHETIC_MODEL);
        if (h->m25 && h->sfaults.swap_pool_and_spawn && h->current_stage == STAGE_WORLD_INIT) {
            synth_00141210_spawn_select(h); /* fault: spawn effect runs first */
            h->spawn_effect_done = true;
        }
        synth_00141910_pools_reset(h);
        return 0u;
    case 0x00141210u:
        if (h->m25 && h->spawn_effect_done) {
            record_call(h, entry, a0, a1, HG_PROV_SYNTHETIC_MODEL);
            h->spawn_effect_done = false;
            return 0u;
        }
        if (h->faults.swap_spawn_before_pool_clear && h->current_stage == STAGE_WORLD_INIT) {
            /* Already ran prematurely before 00141910 wiped PLAYER. */
            return 0u;
        }
        record_call(h, entry, a0, a1, HG_PROV_SYNTHETIC_MODEL);
        synth_00141210_spawn_select(h);
        return 0u;
    case 0x0013fef0u:
        record_call(h, entry, a0, a1, HG_PROV_SYNTHETIC_MODEL);
        synth_0013fef0_env_init(h);
        return 0u;
    case 0x0012bf70u:
        record_call(h, entry, a0, a1, HG_PROV_SYNTHETIC_MODEL);
        synth_0012bf70_camera_init(h);
        return 0u;
    case 0x0013a4c0u:
        record_call(h, entry, a0, a1, HG_PROV_SYNTHETIC_MODEL);
        synth_0013a4c0_area_map_bind(h, a0);
        return 0u;
    case 0x00189aa0u:
        record_call(h, entry, a0, a1, HG_PROV_SYNTHETIC_MODEL);
        if (h->m25)
            m25_spawn_world_actors(h);
        else
            synth_00189aa0_spawn_world_actors(h);
        return 0u;
    case 0x0012bc00u:
        /* Camera teardown helper called by hg_gameplay_teardown (0012f800). */
        record_call(h, entry, a0, a1, HG_PROV_SYNTHETIC_MODEL);
        w8(h, CAMERA_ADDR + 0x143u, 0u);
        return 0u;

    /* Audio seams: explicitly logged and untouched (M24 ownership). */
    case 0x00156cf0u:
    case 0x00156ce0u:
    case 0x00156d50u:
    case 0x00156e60u:
    case 0x00156ad0u:
    case 0x00156550u:
        record_call(h, entry, a0, a1, HG_PROV_UNKNOWN);
        return 0u;

    /* Synthetic libpad IOP boundary consumed by real hg_pad_service/port/read. */
    case 0x0010dcf8u: /* scePadGetState -> 6 (STABLE) */
        return 6u;
    case 0x0010db90u: { /* scePadRead(port, slot, buf) */
        /* Buffer layout read by hg_pad_read (src/front_end.c:269-307):
         *   buf+0 = 0 (ok), buf+1 = 0x73 (DualShock2 analog ID 7),
         *   buf+2/+3 = active-low 16-bit digital buttons,
         *   buf+4/+5 = right stick X/Y, buf+6/+7 = left stick X/Y. */
        uint16_t raw_btn = (uint16_t)(~h->current_pad.buttons);
        w8(h, a2 + 0u, 0u);
        w8(h, a2 + 1u, 0x73u);
        w8(h, a2 + 2u, (uint8_t)((raw_btn >> 8) & 0xffu));
        w8(h, a2 + 3u, (uint8_t)(raw_btn & 0xffu));
        w8(h, a2 + 4u, h->current_pad.rx);
        w8(h, a2 + 5u, h->current_pad.ry);
        w8(h, a2 + 6u, h->current_pad.lx);
        w8(h, a2 + 7u, h->current_pad.ly);
        return 1u;
    }
    case 0x0013e5b0u: {
        /* Analog stick byte conversion called by hg_pad_read:
         * f12 = raw X [0..255], f13 = raw Y [0..255], a0 = PAD, a1 = stick (0=L, 1=R).
         * Notice PS2 stick Y: 0x00 is full forward (+1.0), 0x80 is 0.0, 0xff is -1.0.
         * Stick X: 0x00 is left (-1.0), 0x80 is 0.0, 0xff is right (+1.0). */
        float rx = u2f(h->pending_f12);
        float ry = u2f(h->pending_f13);
        h->has_pending_fp = false;
        float nx = (rx - 128.0f) / 127.0f;
        float ny = (128.0f - ry) / 127.0f;
        if (nx > 1.0f) nx = 1.0f;
        if (nx < -1.0f) nx = -1.0f;
        if (ny > 1.0f) ny = 1.0f;
        if (ny < -1.0f) ny = -1.0f;
        if (fabsf(nx) < 0.02f) nx = 0.0f;
        if (fabsf(ny) < 0.02f) ny = 0.0f;
        float mag = sqrtf(nx * nx + ny * ny);
        uint32_t base_off = (a1 == 0u) ? 0x200u : 0x210u;
        w32(h, a0 + base_off + 0u, f2u(nx));
        w32(h, a0 + base_off + 4u, f2u(ny));
        w32(h, a0 + base_off + 8u, f2u(mag));
        return 0u;
    }

    /* Math leaves called by player movement, constraint, and camera C code. */
    case 0x00143560u: return f2u(sinf(u2f(a0)));
    case 0x00143620u: return f2u(cosf(u2f(a0)));
    case 0x00120830u: {
        float dx = u2f(r32(h, a0 + 0u)) - u2f(r32(h, a1 + 0u));
        float dy = u2f(r32(h, a0 + 4u)) - u2f(r32(h, a1 + 4u));
        float dz = u2f(r32(h, a0 + 8u)) - u2f(r32(h, a1 + 8u));
        return f2u(sqrtf(dx * dx + dy * dy + dz * dz));
    }
    case 0x00120580u: {
        float x = u2f(a0) - u2f(r32(h, a2 + 0u));
        float z = u2f(a1) - u2f(r32(h, a2 + 8u));
        return f2u(atan2f(x, z));
    }
    case 0x00143c60u: synth_mat_mul_rot_x(h, a0, a1, a2); return 0u;
    case 0x00143d30u: synth_mat_mul_rot_y(h, a0, a1, a2); return 0u;
    case 0x00143b90u: synth_mat_mul_rot_z(h, a0, a1, a2); return 0u;
    case 0x0011ad58u:
        h->lcg_state = h->lcg_state * 0x41c64e6du + 0x3039u;
        return h->lcg_state;

    /* Synthetic player/actor/camera engine leaf implementations. */
    case 0x001330f0u:
        w8(h, a0 + 0x18u, 1u); /* object+0x38 pose-enabled flag */
        return 0u;
    case 0x00139ff0u:
        /* Animation bind -> run real hg_actor_default_pose! */
        hg_actor_default_pose(&h->res_env, a0);
        return 0u;
    case 0x00143e60u:
        for (uint32_t i = 0; i < 4u; ++i)
            w32(h, a0 + i * 4u, r32(h, a1 + i * 4u));
        return 0u;
    case 0x00144340u:
        synth_build_transform(h, a0, a1, a2);
        return 0u;
    case 0x0013a240u:
        synth_update_bones(h, a0, a1);
        return 0u;
    case 0x0015e1d0u: {
        /* Refresh player transform + compute eye position at pos + (0, 16.5, 0). */
        synth_build_transform(h, a0 + 0x50u, a0 + 0x30u, a0 + 0x40u);
        if (h->m25)
            return 0u; /* M25: +380 comes from bone 2 (C 0013a2a0), not a constant */
        w32(h, a0 + 0x380u, r32(h, a0 + 0x30u));
        w32(h, a0 + 0x384u, f2u(u2f(r32(h, a0 + 0x34u)) + 16.5f));
        w32(h, a0 + 0x388u, r32(h, a0 + 0x38u));
        w32(h, a0 + 0x38cu, f2u(1.0f));
        return 0u;
    }
    case 0x0012c640u: {
        /* Copy head matrix forward/angles into camera yaw/pitch preview. */
        float fx = u2f(r32(h, a1 + 0x20u));
        float fy = u2f(r32(h, a1 + 0x24u));
        float fz = u2f(r32(h, a1 + 0x28u));
        w32(h, a0 + 0x30u, f2u(fx));
        w32(h, a0 + 0x34u, f2u(fy));
        w32(h, a0 + 0x38u, f2u(fz));
        return 0u;
    }
    case 0x0012cce0u: /* camera shake check: 0 = no shake -> copy +e0/+f0 to +00/+10 */
        return 0u;
    case 0x0012c3d0u:
        synth_camera_lookat(h, a0);
        return 0u;
    case 0x00196ad0u:
        record_call(h, entry, a0, a1, HG_PROV_SYNTHETIC_MODEL);
        return synth_00196ad0_spawn_type2_node(h, a0);
    case 0x0013a060u:
        /* Set player animation sequence halfword at object+0x26 (player+0xb6). */
        w16(h, a2 + 0x26u, a3 & 0xffffu);
        return 0u;
    case 0x001446a0u:
        return synth_wall_query(h, a1, a2);
    case 0x00145630u:
        return synth_floor_query(h, a0, a2);
    case 0x001b7f00u:
    case 0x00267450u:
    case 0x0015d770u:
    case 0x0013a050u:
    case 0x00160ff0u:
    case 0x00160c60u:
    case 0x0015aea0u:
    case 0x00161110u:
    case 0x00188650u:
    case 0x0015bf30u:
    case 0x0014a0a0u:
    case 0x0014f370u:
    case 0x001404f0u:
    case 0x0010cca0u:
    case 0x00104e10u:
    case 0x00100000u:
    case 0x001041e0u: /* FlushCache(2) called by hg_area_finalize */
    case 0x0010b0c8u: /* FlushCache during area finalize */
    case 0x00131dd0u: /* hg_area_finalize */
        return 0u;

    /* Synthetic world actors invoked by real hg_actor_update_main/type2! */
    case SYNTH_ROOT_ACTOR_CB:
        if (r8(h, a0 + 0x18u) == 0u)
            w8(h, a0 + 0x18u, 1u);
        h->actor_ticks_main++;
        record_call(h, entry, a0, r8(h, a0 + 0x18u), HG_PROV_SYNTHETIC_MODEL);
        return 0u;
    case SYNTH_PROP_CB:
    case SYNTH_FILLER_CB:
        if (!h->m25) { /* not an M23 address: same as the default branch */
            h->unknown_call_count++;
            record_call(h, entry, a0, a1, HG_PROV_UNKNOWN);
            return 0u;
        }
        h->actor_ticks_main++;
        record_call(h, entry, a0, r32(h, a0 + 0x04u), HG_PROV_SYNTHETIC_MODEL);
        return 0u;
    case SYNTH_NODE_ANCHOR_CB:
    case SYNTH_NODE_AUX1_CB:
    case SYNTH_NODE_AUX2_CB:
        h->actor_ticks_type2++;
        record_call(h, entry, a0, r32(h, a0 + 0x04u), HG_PROV_SYNTHETIC_MODEL);
        return 0u;

    default:
        break;
    }
    h->unknown_call_count++;
    record_call(h, entry, a0, a1, HG_PROV_UNKNOWN);
    return 0u;
}

/* ---- Public configuration defaults ------------------------------------- */

HGSyntheticSceneConfig hg_synthetic_scene_default(void) {
    /* Independently authored synthetic courtyard:
     *   spawn = (12.0, 10.0, 20.0), yaw = 0.0 (+Z)
     *   floor = 10.0, walls x in [2.0, 22.0], z in [10.0, 26.0]
     * Explicitly distinct from retail AREA1 coordinates. */
    HGSyntheticSceneConfig cfg = {
        .spawn_x = 12.0f,
        .spawn_y = 10.0f,
        .spawn_z = 20.0f,
        .spawn_yaw = 0.0f,
        .floor_y = 10.0f,
        .wall_min_x = 2.0f,
        .wall_max_x = 22.0f,
        .wall_min_z = 10.0f,
        .wall_max_z = 32.0f,
        .player_bones = 20u,
        .area_triple = {1u, 0u, 0u, 0u},
        .seed_stale_title_handle = true
    };
    return cfg;
}

HGSyntheticPadInput hg_synthetic_pad_neutral(void) {
    return (HGSyntheticPadInput){
        .buttons = 0u,
        .lx = 0x80u,
        .ly = 0x80u,
        .rx = 0x80u,
        .ry = 0x80u
    };
}

HGWorldInitHarness *hg_world_harness_create(const HGSyntheticSceneConfig *cfg,
                                            const HGWorldFaultInject *faults) {
    HGWorldInitHarness *h = calloc(1u, sizeof(*h));
    if (!h)
        return NULL;
    h->cfg = cfg ? *cfg : hg_synthetic_scene_default();
    if (faults)
        h->faults = *faults;
    h->ee = calloc(EE_RAM_SIZE, 1u);
    h->scratch = calloc(SCRATCH_SIZE, 1u);
    h->synth_disc_size = (size_t)SYNTH_DISC_TOTAL_SECTORS * SECTOR_SIZE;
    h->synth_disc = calloc(h->synth_disc_size, 1u);
    if (!h->ee || !h->scratch || !h->synth_disc) {
        hg_world_harness_destroy(h);
        return NULL;
    }
    h->read_env = (HGReadEnv){
        .user = h,
        .read32 = synth_cdvd_read32,
        .sync = synth_cdvd_sync,
        .disk_ready = synth_cdvd_disk_ready,
        .read = synth_cdvd_read,
        .get_error = synth_cdvd_get_error
    };
    h->res_env = (HGResourceEnv){
        .user = h,
        .load = bus_load,
        .store = bus_store,
        .call = bus_call,
        .read_state = &h->read_state,
        .read_env = &h->read_env
    };
    h->current_pad = hg_synthetic_pad_neutral();
    h->guest_sp = SYNTH_FRAME_STACK;
    h->lcg_state = 0x00015046u;
    return h;
}

void hg_world_harness_destroy(HGWorldInitHarness *h) {
    if (!h)
        return;
    free(h->synth_disc);
    free(h->scratch);
    free(h->ee);
    free(h);
}

/* ---- Stage 1: Cold startup & declared synthetic fixtures --------------- */

static void stage_synthetic_static_tables(HGWorldInitHarness *h) {
    /* 1. Identity matrix at 003db2b0 (read by hg_camera_head_matrix / update). */
    synth_mat_identity(h, 0x003db2b0u);
    record_provenance(h, "fixture_identity_003db2b0", 0x003db2b0u, 64u, f2u(1.0f),
                      HG_PROV_FIXTURE, 0u);

    /* 2. Synthetic movement speed scales and rate tables (original synthetic
     *    tuning values, distinctly chosen for smooth synthetic response). */
    const float walk_scales[4] = {1.0f, 1.5f, 2.0f, 2.5f};
    const float turn_scales[4] = {2.0f, 2.5f, 4.0f, 3.5f};
    for (uint32_t i = 0; i < 4u; ++i) {
        w32(h, 0x003e0860u + i * 4u, f2u(walk_scales[i]));
        w32(h, 0x003e0870u + i * 4u, f2u(turn_scales[i]));
    }
    const float walk_rows[4][4] = {
        {0.025f, 2.0f, 0.025f, 4.0f},
        {0.015f, 0.12f, 0.015f, 0.10f},
        {0.025f, 0.22f, 0.025f, 0.18f},
        {0.035f, 0.30f, 0.035f, 0.25f}
    };
    const float turn_rows[4][4] = {
        {0.004f, 2.0f, 0.004f, 2.0f},
        {0.002f, 0.012f, 0.002f, 0.012f},
        {0.003f, 0.020f, 0.003f, 0.020f},
        {0.005f, 0.030f, 0.005f, 0.030f}
    };
    for (uint32_t row = 0; row < 4u; ++row) {
        for (uint32_t col = 0; col < 4u; ++col) {
            w32(h, 0x0039a3b0u + row * 16u + col * 4u, f2u(walk_rows[row][col]));
            w32(h, 0x0039a3f0u + row * 16u + col * 4u, f2u(turn_rows[row][col]));
        }
    }
    record_provenance(h, "fixture_move_tables_0039a3b0", 0x0039a3b0u, 128u,
                      f2u(walk_rows[3][3]), HG_PROV_FIXTURE, 0u);

    /* 3. Palette table at 0037b9a0 (read by hg_actor_set_palette). */
    for (uint32_t i = 0; i < 6u; ++i)
        w32(h, 0x0037b9a0u + i * 4u, 0x42210103u);

    /* 4. Digital pressure mask table at 003db240 (read by hg_pad_digital_pressure). */
    for (uint32_t i = 0; i < 16u; ++i)
        w32(h, 0x003db240u + i * 4u, 1u << i);

    /* 5. Render context & sort queue pointers + area tables. */
    w32(h, GP_BASE + 0x3098u, RENDER_CTX_ADDR);
    w32(h, GP_BASE + 0x3094u, SORT_QUEUE_ADDR);
    w32(h, GP_BASE + 0x2b08u, f2u(1.0f)); /* audio volume 1.0 */
    w32(h, 0x003db290u, 0u);
    w32(h, 0x003db294u, 0u);
    w32(h, 0x003db298u, 0u);
    w32(h, 0x003db29cu, f2u(1.0f));

    /* 6. PACK / AREA file records (read by disc_read.c / hg_pack_general_step). */
    w32(h, 0x003ead20u, SYNTH_PACK_FILE_LBA);
    w32(h, 0x003ead24u, (uint32_t)h->synth_disc_size);
    w32(h, 0x003dff04u, OVERLAY_BASE); /* AREA overlay destination for hg_area_read_step */
    for (uint32_t idx = 0; idx < 12u; ++idx) {
        w32(h, 0x003f3a30u + idx * 8u, SYNTH_AREA_FILE_LBA);
        w32(h, 0x003f3a34u + idx * 8u, AREA_OVERLAY_SIZE);
    }

    /* 7. Arena pointers at 003f4d3c..003f4d5f (consumed by hg_pack_general_prepare). */
    w32(h, 0x003f4d3cu, 0x00d00000u);
    w32(h, 0x003f4d40u, 0x00d40000u);
    w32(h, 0x003f4d44u, 0x00da0000u);
    w32(h, 0x003f4d48u, h->m25 ? h->spec.player_arena : 0x00e00000u); /* mode 4 -> player FLK5 */
    w32(h, 0x003f4d4cu, 0x01000000u);
    w32(h, 0x003f4d50u, h->m25 ? h->spec.area_arena : 0x01100000u); /* mode 8 -> area FLK5 */
    w32(h, 0x003f4d54u, 0x01200000u);
    w32(h, 0x003f4d58u, 0x01300000u);
    w32(h, 0x003f4d5cu, 0x01400000u);

    /* 8. Synthetic spawn entry at SYNTH_SPAWN_TABLE (fixture input to 00141210). */
    w32(h, SYNTH_SPAWN_TABLE + 0u, f2u(h->cfg.spawn_x));
    w32(h, SYNTH_SPAWN_TABLE + 4u, f2u(h->cfg.spawn_y));
    w32(h, SYNTH_SPAWN_TABLE + 8u, f2u(h->cfg.spawn_z));
    w32(h, SYNTH_SPAWN_TABLE + 12u, f2u(h->cfg.spawn_yaw));
    record_provenance(h, "fixture_spawn_table", SYNTH_SPAWN_TABLE, 16u,
                      f2u(h->cfg.spawn_x), HG_PROV_FIXTURE, 0u);

    /* 9. Pad port block initialization so real hg_pad_service reads port 0 analog. */
    w32(h, PAD_BLOCK + 0x100u, 0u); /* port 0 */
    w32(h, PAD_BLOCK + 0x104u, 0u); /* slot 0 */
    w16(h, PAD_BLOCK + 0x108u, 7u); /* DualShock2 ID */
    w8(h, PAD_BLOCK + 0x10au, 1u);  /* port active-read state */
    w8(h, PAD_BLOCK + 0x10bu, 0u);  /* digital pressure via 003db240 */
    w8(h, PAD_BLOCK + 0x1a3u, 7u);  /* terminal ID matches */
    w32(h, PAD_BLOCK + 0x23cu, 1u); /* secondary buttons enabled */
    record_provenance(h, "fixture_pad_port_0095e440", PAD_BLOCK + 0x100u, 16u, 7u,
                      HG_PROV_FIXTURE, 0u);

    /* 10. Area triple at 003fa1b0..3. */
    for (uint32_t i = 0; i < 4u; ++i)
        w8(h, 0x003fa1b0u + i, h->cfg.area_triple[i]);
}

static void m25_reset_session(HGWorldInitHarness *h);
static void m25_write_scene_inputs(HGWorldInitHarness *h);

HGWorldInitError hg_world_harness_cold_boot(HGWorldInitHarness *h) {
    if (!h)
        return HG_WORLD_ERR_NULL_ARG;
    if (h->m25)
        m25_reset_session(h);
    h->current_stage = STAGE_COLD_BOOT;
    memset(h->ee, 0, EE_RAM_SIZE);
    memset(h->scratch, 0, SCRATCH_SIZE);
    memset(&h->read_state, 0, sizeof(h->read_state));
    h->frame = 0u;
    h->cycle = 0u;
    h->call_count = 0u;
    h->provenance_count = 0u;
    h->domain_fault_count = 0u;
    h->last_domain_fault = 0u;
    h->unknown_call_count = 0u;
    h->wall_contact_events = 0u;
    h->floor_query_events = 0u;
    h->actor_ticks_main = 0u;
    h->actor_ticks_type2 = 0u;
    h->cdvd_reads = 0u;
    h->guest_sp = SYNTH_FRAME_STACK;
    h->resources_published = false;
    h->world_entered = false;
    h->pool_init_ran = false;
    h->spawn_init_ran = false;
    h->camera_init_ran = false;
    h->state0_init_ran = false;
    h->order_violation_seen = false;
    h->position_injected = false;
    h->camera_updated_this_cycle = false;
    h->world_root_actor = 0u;
    h->player_pos_prov = HG_PROV_UNKNOWN;
    h->camera_prov = HG_PROV_UNKNOWN;

    build_synthetic_disc_image(h);
    stage_synthetic_static_tables(h);
    if (h->m25)
        m25_write_scene_inputs(h);

    /* Optionally seed a simulated prior title handle (0x22f) to prove that real
     * hg_pack_general_prepare -> hg_resource_clear_dynamic_handles revokes it! */
    if (h->cfg.seed_stale_title_handle) {
        w32(h, HANDLE_TABLE + HANDLE_TITLE * 4u, SYNTH_TITLE_SENTINEL);
        record_provenance(h, "prior_title_handle_0x22f", HANDLE_TABLE + HANDLE_TITLE * 4u,
                          4u, SYNTH_TITLE_SENTINEL, HG_PROV_FIXTURE, 0u);
    }

    hg_resource_init(&h->res_env);
    record_call(h, 0x001431f0u, 0u, 0u, HG_PROV_C_PRODUCED);
    h->cold_booted = true;
    m25_set_ready(h, HG_READY_BOOTED, 0u);
    return HG_WORLD_OK;
}

/* ---- Stage 2: Resource availability & publication via real loader ------- */

static HGWorldInitError run_pack_dir(HGWorldInitHarness *h, uint32_t dir_id) {
    w8(h, TASK_NORMAL + 0x39u, 0u);
    w8(h, TASK_NORMAL + 0x3au, 0u);
    w8(h, TASK_NORMAL + 0x3bu, 0u);
    w32(h, TASK_NORMAL + 0x44u, dir_id);
    w32(h, TASK_NORMAL + 0x4cu, 0x003f3b40u); /* member cursor at header + 0x40 */

    record_call(h, 0x00130900u, TASK_NORMAL, dir_id, HG_PROV_C_PRODUCED);
    hg_pack_general_prepare(&h->res_env, TASK_NORMAL);
    for (unsigned step = 0u; step < 32u; ++step) {
        if (hg_pack_general_step(&h->res_env, TASK_NORMAL) == 1u)
            return HG_WORLD_OK;
    }
    return HG_WORLD_ERR_MISSING_RESOURCE;
}

static HGWorldInitError m25_publish(HGWorldInitHarness *h);
static HGWorldInitError m25_enter(HGWorldInitHarness *h);
static HGWorldInitError m25_step(HGWorldInitHarness *h, const HGSyntheticPadInput *pad,
                                 HGWorldFrameSnapshot *out);
static HGWorldInitError m25_teardown(HGWorldInitHarness *h);

HGWorldInitError hg_world_harness_publish_resources(HGWorldInitHarness *h) {
    if (!h)
        return HG_WORLD_ERR_NULL_ARG;
    if (h->m25)
        return m25_publish(h);
    if (!h->cold_booted)
        return HG_WORLD_ERR_CALLBACK_ORDER;

    h->current_stage = STAGE_PUBLISH;
    if (h->faults.omit_resource_publish)
        return HG_WORLD_OK;

    /* 1. Publish synthetic player FLK5 (handle 0x3f) via real loader. */
    HGWorldInitError err = run_pack_dir(h, PACK_DIR_PLAYER);
    if (err != HG_WORLD_OK)
        return err;

    /* 2. Publish synthetic area FLK5 (handle 0x96) + overlay via mode 8
     *    (which invokes real hg_resource_clear_dynamic_handles to clear 0x22f!). */
    if (h->faults.retain_stale_handle) {
        /* Negative control: re-assert title handle after clearing would happen. */
        err = run_pack_dir(h, PACK_DIR_AREA);
        if (err != HG_WORLD_OK)
            return err;
        w32(h, HANDLE_TABLE + HANDLE_TITLE * 4u, SYNTH_TITLE_SENTINEL);
    } else {
        err = run_pack_dir(h, PACK_DIR_AREA);
        if (err != HG_WORLD_OK)
            return err;
    }

    uint32_t h_player = r32(h, HANDLE_TABLE + HANDLE_PLAYER * 4u);
    uint32_t h_area = r32(h, HANDLE_TABLE + HANDLE_AREA * 4u);
    record_provenance(h, "published_handle_0x3f", HANDLE_TABLE + HANDLE_PLAYER * 4u,
                      4u, h_player, HG_PROV_C_PRODUCED, 0x00130900u);
    record_provenance(h, "published_handle_0x96", HANDLE_TABLE + HANDLE_AREA * 4u,
                      4u, h_area, HG_PROV_C_PRODUCED, 0x00130900u);
    record_provenance(h, "cleared_title_handle_0x22f", HANDLE_TABLE + HANDLE_TITLE * 4u,
                      4u, r32(h, HANDLE_TABLE + HANDLE_TITLE * 4u),
                      HG_PROV_C_PRODUCED, 0x0012fb30u);

    if (r32(h, HANDLE_TABLE + HANDLE_TITLE * 4u) != 0u)
        return HG_WORLD_ERR_STALE_HANDLE;
    if (h_player == 0u || h_area == 0u)
        return HG_WORLD_ERR_MISSING_RESOURCE;

    h->resources_published = true;
    return HG_WORLD_OK;
}

/* ---- Prerequisite and invariant validation ----------------------------- */

static uint32_t count_actor_list(const HGWorldInitHarness *h, uint32_t head_addr) {
    uint32_t cur = r32(h, head_addr);
    uint32_t count = 0u;
    while (cur != 0u && count < 512u) {
        cur = r32(h, cur + 0x24u);
        count++;
    }
    return count;
}

HGWorldInitError hg_world_harness_validate_prereqs(const HGWorldInitHarness *h,
                                                   bool require_active_player) {
    if (!h)
        return HG_WORLD_ERR_NULL_ARG;
    if (h->unknown_call_count > 0u)
        return HG_WORLD_ERR_UNKNOWN_CALL;
    if (h->order_violation_seen)
        return HG_WORLD_ERR_CALLBACK_ORDER;
    uint32_t h_player = r32(h, HANDLE_TABLE + HANDLE_PLAYER * 4u);
    uint32_t h_area = r32(h, HANDLE_TABLE + HANDLE_AREA * 4u);
    if (h_player == 0u || h_area == 0u)
        return HG_WORLD_ERR_MISSING_RESOURCE;
    if (r32(h, HANDLE_TABLE + HANDLE_TITLE * 4u) != 0u)
        return HG_WORLD_ERR_STALE_HANDLE;

    if (r32(h, NODE_STACK_TOP) == 0u || r32(h, NODE_FREE_COUNT) == 0u ||
        r32(h, ACTOR_CTRL_BASE + 0x00u) == 0u)
        return HG_WORLD_ERR_UNINITIALIZED_POOLS;

    /* Check spawn / position finiteness and non-zero W lane. */
    float px = u2f(r32(h, PLAYER_ADDR + 0x30u));
    float py = u2f(r32(h, PLAYER_ADDR + 0x34u));
    float pz = u2f(r32(h, PLAYER_ADDR + 0x38u));
    float pw = u2f(r32(h, PLAYER_ADDR + 0x3cu));
    if (!isfinite(px) || !isfinite(py) || !isfinite(pz) || pw != 1.0f)
        return HG_WORLD_ERR_MISSING_SPAWN;

    if (h->position_injected || h->player_pos_prov == HG_PROV_FIXTURE)
        return HG_WORLD_ERR_PROVENANCE_VIOLATION;

    if (h->domain_fault_count > 0u) {
        if (h->last_domain_fault == 0x30u && r8(h, PLAYER_ADDR + 0x18u) == 0u)
            return HG_WORLD_ERR_PLAYER_NOT_INITIALIZED;
        return HG_WORLD_ERR_DOMAIN_FAULT;
    }

    if (require_active_player) {
        if (r8(h, PLAYER_ADDR + 0x18u) != 1u || r8(h, PLAYER_ADDR + 0x00u) == 0u)
            return HG_WORLD_ERR_PLAYER_NOT_INITIALIZED;
        uint32_t bone_count = r32(h, PLAYER_ADDR + 0x90u + 0x54u);
        uint32_t bone_table = r32(h, PLAYER_ADDR + 0x90u + 0x58u);
        if (bone_count < 18u || bone_table == 0u || r32(h, PLAYER_ADDR + 0x90u + 0x10u) == 0u)
            return HG_WORLD_ERR_INVALID_PLAYER_BINDING;
        if (!h->camera_init_ran || !h->camera_updated_this_cycle)
            return HG_WORLD_ERR_CAMERA_UNINITIALIZED;
        float cw = u2f(r32(h, CAMERA_ADDR + 0x0cu));
        float cx = u2f(r32(h, CAMERA_ADDR + 0x00u));
        float cy = u2f(r32(h, CAMERA_ADDR + 0x04u));
        float cz = u2f(r32(h, CAMERA_ADDR + 0x08u));
        float dx = cx - px, dy = cy - (py + 16.5f), dz = cz - pz;
        if (cw != 1.0f || (dx * dx + dy * dy + dz * dz) > 1.0f)
            return HG_WORLD_ERR_CAMERA_UNINITIALIZED;
    }

    /* Check actor/node leak invariants across cycles. */
    if (h->cycle > 1u) {
        if (count_actor_list(h, ACTOR_LIST_MAIN) > 1u ||
            count_actor_list(h, ACTOR_LIST_TYPE2) > 3u)
            return HG_WORLD_ERR_STALE_ACTORS;
        if (h->initial_node_free_after_init != 0u &&
            r32(h, NODE_FREE_COUNT) != h->initial_node_free_after_init)
            return HG_WORLD_ERR_NODE_LEAK;
    }

    return HG_WORLD_OK;
}

/* ---- Stage 3: World activation via real hg_gameplay_frame sub 0 -------- */

HGWorldInitError hg_world_harness_enter_world(HGWorldInitHarness *h) {
    if (!h)
        return HG_WORLD_ERR_NULL_ARG;
    if (h->m25)
        return m25_enter(h);
    if (!h->cold_booted)
        return HG_WORLD_ERR_CALLBACK_ORDER;

    /* Fail closed before entering world if resources were omitted or stale. */
    if (r32(h, HANDLE_TABLE + HANDLE_PLAYER * 4u) == 0u ||
        r32(h, HANDLE_TABLE + HANDLE_AREA * 4u) == 0u)
        return HG_WORLD_ERR_MISSING_RESOURCE;
    if (r32(h, HANDLE_TABLE + HANDLE_TITLE * 4u) != 0u)
        return HG_WORLD_ERR_STALE_HANDLE;

    h->cycle++;
    h->current_stage = STAGE_WORLD_INIT;
    h->pool_init_ran = false;
    h->spawn_init_ran = false;
    h->camera_init_ran = false;
    h->state0_init_ran = false;
    h->order_violation_seen = false;
    h->camera_updated_this_cycle = false;

    /* Install gameplay mode 0012d1c0 at major=5, minor=1, sub=0 (exact entry
     * state left by hg_area_load_step returning 4 in hg_gameplay_major5). */
    hg_mode_init(&h->res_env, 0x0012d1c0u);
    w8(h, MODE_BLOCK + 0x01u, 1u); /* area = 1 */
    w8(h, MODE_BLOCK + 0x08u, 5u); /* major = 5 */
    w8(h, MODE_BLOCK + 0x09u, 1u); /* minor = 1 (active play) */
    w8(h, MODE_BLOCK + 0x0au, 0u); /* sub = 0 (world init entry!) */
    w32(h, SCRATCH_BASE + 0xc0u, MODE_BLOCK);
    w32(h, SCRATCH_BASE + 0xc4u, 2u);
    w32(h, SCRATCH_BASE + 0xccu, 2u); /* FLAG_CC = 2 (normal area entry) */

    /* If negative control requested stale actors/nodes across reinit, keep
     * previous pools intact instead of running 00141910 reset cleanly. */
    bool saved_omit_pool = h->faults.omit_pool_init;
    if (h->faults.skip_teardown_pool_reset && h->cycle > 1u)
        h->faults.omit_pool_init = true;

    /* Match black screen left by hg_area_load_step before sub 0 fade-in. */
    hg_fade_black(&h->res_env, 0u, 0u);

    /* Execute real hg_mode_dispatch -> hg_gameplay_mode -> hg_gameplay_major5 ->
     * hg_gameplay_frame (sub 0 -> world init -> sub=1 -> gameplay_active ->
     * coordinator 0012f850 -> state-0 player init + camera update + actor ticks)! */
    hg_mode_dispatch(&h->res_env);
    h->faults.omit_pool_init = saved_omit_pool;

    /* Negative control: fixture substitution / injected final coordinates. */
    if (h->faults.inject_final_position) {
        w32(h, PLAYER_ADDR + 0x30u, f2u(h->faults.injected_pos[0]));
        w32(h, PLAYER_ADDR + 0x34u, f2u(h->faults.injected_pos[1]));
        w32(h, PLAYER_ADDR + 0x38u, f2u(h->faults.injected_pos[2]));
        h->position_injected = true;
        h->player_pos_prov = HG_PROV_FIXTURE;
        record_provenance(h, "INJECTED_player_pos_003f9300", PLAYER_ADDR + 0x30u, 12u,
                          f2u(h->faults.injected_pos[0]), HG_PROV_FIXTURE, 0u);
    }

    if (h->cycle == 1u && h->domain_fault_count == 0u && !h->faults.omit_pool_init)
        h->initial_node_free_after_init = r32(h, NODE_FREE_COUNT);

    HGWorldInitError err = hg_world_harness_validate_prereqs(h, true);
    if (err != HG_WORLD_OK)
        return err;

    /* Step fade service once at end of entry frame (matching main-loop tail). */
    hg_fade_service(&h->res_env);
    h->world_entered = true;
    h->frame++;
    return HG_WORLD_OK;
}

/* ---- Stage 4: Consecutive controller-driven gameplay updates ------------ */

HGWorldInitError hg_world_harness_step_frame(HGWorldInitHarness *h,
                                             const HGSyntheticPadInput *pad,
                                             HGWorldFrameSnapshot *out) {
    if (!h)
        return HG_WORLD_ERR_NULL_ARG;
    if (h->m25)
        return m25_step(h, pad, out);
    if (!h->world_entered)
        return HG_WORLD_ERR_CALLBACK_ORDER;

    h->current_stage = STAGE_ACTIVE_FRAME;
    h->current_pad = pad ? *pad : hg_synthetic_pad_neutral();

    /* Main-loop head: real hg_pad_service (0013d970 -> 0013dc30 -> 0013e090)
     * writes held/edge/analog floats directly into 003eaf00..003eaf3f! */
    hg_pad_service(&h->res_env);
    record_call(h, 0x0013d970u, h->current_pad.buttons,
                ((uint32_t)h->current_pad.lx << 8) | h->current_pad.ly,
                HG_PROV_C_PRODUCED);

    /* Mode dispatch: real hg_mode_dispatch -> hg_gameplay_mode ->
     * hg_gameplay_major5 -> hg_gameplay_frame (sub 1) -> coordinator ->
     * hg_player_dispatch_fixed -> hg_camera_update_fixed -> actor lists ->
     * hg_counter_advance -> hg_gameplay_event_flags! */
    hg_mode_dispatch(&h->res_env);

    /* Main-loop tail: real hg_fade_service (00139240) advances fade-in/out! */
    hg_fade_service(&h->res_env);
    record_call(h, 0x00139240u, r8(h, FADE_BLOCK), r16(h, FADE_BLOCK + 4u),
                HG_PROV_C_PRODUCED);

    /* Advance main-loop counter [gp+2f84] and vblank counter 0095e300 via C. */
    hg_vblank_start_handler(&h->res_env);
    hg_vblank_end_handler(&h->res_env);
    w32(h, GP_BASE + 0x2f84u, r32(h, GP_BASE + 0x2f84u) + 1u);

    if (out)
        hg_world_harness_snapshot(h, out);

    h->frame++;
    return hg_world_harness_validate_prereqs(h, true);
}

/* ---- Stage 5: Reset / teardown without stale actors or handles ---------- */

HGWorldInitError hg_world_harness_teardown(HGWorldInitHarness *h) {
    if (!h)
        return HG_WORLD_ERR_NULL_ARG;
    if (h->m25)
        return m25_teardown(h);
    h->current_stage = STAGE_TEARDOWN;

    /* Run real hg_gameplay_teardown (0012f800): resets world globals via
     * 00141b10, tears down camera via 0012bc00, sets 700000f8=1, installs
     * lifecycle mode 00121be0 via hg_mode_set! */
    record_call(h, 0x0012f800u, 0u, 0u, HG_PROV_C_PRODUCED);
    hg_gameplay_teardown(&h->res_env);

    if (!h->faults.retain_stale_handle) {
        record_call(h, 0x0012fb30u, 0u, 0u, HG_PROV_C_PRODUCED);
        hg_resource_clear_dynamic_handles(&h->res_env);
    }
    if (!h->faults.skip_teardown_pool_reset) {
        /* Cleanly retire actor lists, bone nodes, player and camera state. */
        synth_00141910_pools_reset(h);
        w8(h, FADE_BLOCK, 0u);
        w8(h, FADE_BLOCK + 3u, 0u);
        w16(h, FADE_BLOCK + 4u, 0u);
    }

    h->world_entered = false;
    h->resources_published = false;
    h->state0_init_ran = false;
    h->camera_init_ran = false;
    h->camera_updated_this_cycle = false;
    h->world_root_actor = 0u;
    h->player_pos_prov = HG_PROV_UNKNOWN;
    h->camera_prov = HG_PROV_UNKNOWN;
    record_provenance(h, "teardown_mode_handler", MODE_BLOCK + 4u, 4u,
                      r32(h, MODE_BLOCK + 4u), HG_PROV_C_PRODUCED, 0x0012f800u);
    return HG_WORLD_OK;
}

/* ---- Snapshot and query helpers ---------------------------------------- */

HGWorldInitError hg_world_harness_snapshot(const HGWorldInitHarness *h,
                                           HGWorldFrameSnapshot *out) {
    if (!h || !out)
        return HG_WORLD_ERR_NULL_ARG;
    memset(out, 0, sizeof(*out));
    out->frame = h->frame;
    out->cycle = h->cycle;
    out->mode_handler = r32(h, MODE_BLOCK + 4u);
    out->mode_major = (uint8_t)r8(h, MODE_BLOCK + 8u);
    out->mode_minor = (uint8_t)r8(h, MODE_BLOCK + 9u);
    out->mode_sub = (uint8_t)r8(h, MODE_BLOCK + 10u);
    out->fade_phase = (uint8_t)r8(h, FADE_BLOCK);
    out->player_active = (uint8_t)r8(h, PLAYER_ADDR + 0x00u);
    out->player_phase = (uint8_t)r8(h, PLAYER_ADDR + 0x18u);
    out->player_subphase = (uint8_t)r8(h, PLAYER_ADDR + 0x19u);
    out->player_substate = (uint8_t)r8(h, PLAYER_ADDR + 0x1au);
    out->player_grounded_flag = (uint8_t)r8(h, PLAYER_ADDR + 0x2c3u);
    out->player_wall_result = (uint16_t)r16(h, PLAYER_ADDR + 0x49au);
    for (uint32_t i = 0; i < 4u; ++i) {
        out->player_pos[i] = u2f(r32(h, PLAYER_ADDR + 0x30u + i * 4u));
        out->player_prev_pos[i] = u2f(r32(h, PLAYER_ADDR + 0x390u + i * 4u));
        out->player_rot[i] = u2f(r32(h, PLAYER_ADDR + 0x40u + i * 4u));
        out->player_eye[i] = u2f(r32(h, PLAYER_ADDR + 0x380u + i * 4u));
        out->camera_eye[i] = u2f(r32(h, CAMERA_ADDR + 0x00u + i * 4u));
        out->camera_target[i] = u2f(r32(h, CAMERA_ADDR + 0x10u + i * 4u));
    }
    out->player_speed = u2f(r32(h, PLAYER_ADDR + 0x438u));
    out->player_turn_rate = u2f(r32(h, PLAYER_ADDR + 0x440u));
    out->camera_pitch = u2f(r32(h, CAMERA_ADDR + 0xd0u));
    out->camera_yaw = u2f(r32(h, CAMERA_ADDR + 0xd4u));
    out->main_actor_count = count_actor_list(h, ACTOR_LIST_MAIN);
    out->type2_actor_count = count_actor_list(h, ACTOR_LIST_TYPE2);
    out->free_node_count = r32(h, NODE_FREE_COUNT);
    out->player_node_count = r32(h, PLAYER_ADDR + 0x90u + 0x54u);
    out->handle_player = r32(h, HANDLE_TABLE + HANDLE_PLAYER * 4u);
    out->handle_area = r32(h, HANDLE_TABLE + HANDLE_AREA * 4u);
    out->handle_title = r32(h, HANDLE_TABLE + HANDLE_TITLE * 4u);
    out->counter_3fa190 = r32(h, 0x003fa190u);
    out->counter_46e118 = r32(h, 0x0046e118u);
    out->counter_46d930 = r32(h, 0x0046d930u);
    out->frame_tick_3eaef0 = r32(h, GP_BASE + 0x2f80u);
    out->wall_contact_events = h->wall_contact_events;
    out->floor_query_events = h->floor_query_events;
    out->domain_fault_count = h->domain_fault_count;
    out->last_domain_fault = h->last_domain_fault;
    out->player_pos_provenance = h->player_pos_prov;
    out->camera_provenance = h->camera_prov;
    return HG_WORLD_OK;
}

size_t hg_world_harness_call_count(const HGWorldInitHarness *h) {
    return h ? h->call_count : 0u;
}

const HGCallRecord *hg_world_harness_call_at(const HGWorldInitHarness *h, size_t index) {
    if (!h || index >= h->call_count)
        return NULL;
    return &h->calls[index];
}

size_t hg_world_harness_provenance_count(const HGWorldInitHarness *h) {
    return h ? h->provenance_count : 0u;
}

const HGProvenanceRecord *hg_world_harness_provenance_at(const HGWorldInitHarness *h,
                                                         size_t index) {
    if (!h || index >= h->provenance_count)
        return NULL;
    return &h->provenance[index];
}

uint32_t hg_world_harness_guest_load(const HGWorldInitHarness *h,
                                     uint32_t address, uint32_t width) {
    return h ? bus_load((void *)h, address, width) : 0u;
}

uint8_t *hg_world_harness_guest_ram(HGWorldInitHarness *h, uint32_t address, uint32_t size) {
    if (!h || size == 0u || address >= EE_RAM_SIZE || size > EE_RAM_SIZE - address)
        return NULL;
    return h->ee + address;
}

uint32_t hg_world_harness_last_domain_fault(const HGWorldInitHarness *h) {
    return h ? h->last_domain_fault : 0u;
}

uint32_t hg_world_harness_unknown_call_count(const HGWorldInitHarness *h) {
    return h ? h->unknown_call_count : 0u;
}

uint32_t hg_world_harness_actor_ticks_main(const HGWorldInitHarness *h) {
    return h ? h->actor_ticks_main : 0u;
}

uint32_t hg_world_harness_actor_ticks_type2(const HGWorldInitHarness *h) {
    return h ? h->actor_ticks_type2 : 0u;
}

const char *hg_world_harness_error_name(HGWorldInitError err) {
    switch (err) {
    case HG_WORLD_OK: return "OK";
    case HG_WORLD_ERR_NULL_ARG: return "NULL_ARG";
    case HG_WORLD_ERR_MISSING_RESOURCE: return "MISSING_RESOURCE";
    case HG_WORLD_ERR_UNINITIALIZED_POOLS: return "UNINITIALIZED_POOLS";
    case HG_WORLD_ERR_MISSING_SPAWN: return "MISSING_SPAWN";
    case HG_WORLD_ERR_PLAYER_NOT_INITIALIZED: return "PLAYER_NOT_INITIALIZED";
    case HG_WORLD_ERR_INVALID_PLAYER_BINDING: return "INVALID_PLAYER_BINDING";
    case HG_WORLD_ERR_CAMERA_UNINITIALIZED: return "CAMERA_UNINITIALIZED";
    case HG_WORLD_ERR_CALLBACK_ORDER: return "CALLBACK_ORDER";
    case HG_WORLD_ERR_STALE_HANDLE: return "STALE_HANDLE";
    case HG_WORLD_ERR_STALE_ACTORS: return "STALE_ACTORS";
    case HG_WORLD_ERR_NODE_LEAK: return "NODE_LEAK";
    case HG_WORLD_ERR_PROVENANCE_VIOLATION: return "PROVENANCE_VIOLATION";
    case HG_WORLD_ERR_DOMAIN_FAULT: return "DOMAIN_FAULT";
    case HG_WORLD_ERR_UNKNOWN_CALL: return "UNKNOWN_CALL";
    case HG_WORLD_ERR_INVALID_CONFIG: return "INVALID_CONFIG";
    case HG_WORLD_ERR_CAPACITY: return "CAPACITY";
    case HG_WORLD_ERR_UNSUPPORTED_ENTRY: return "UNSUPPORTED_ENTRY";
    case HG_WORLD_ERR_LIFECYCLE_STATE: return "LIFECYCLE_STATE";
    default: return "UNKNOWN_ERROR";
    }
}

const char *hg_world_provenance_name(HGValueProvenance prov) {
    switch (prov) {
    case HG_PROV_UNKNOWN: return "unknown";
    case HG_PROV_FIXTURE: return "fixture";
    case HG_PROV_C_PRODUCED: return "c-produced";
    case HG_PROV_SYNTHETIC_MODEL: return "synthetic-model";
    case HG_PROV_ORIGINAL_BYTES: return "original-bytes";
    case HG_PROV_RUNTIME_OBSERVATION: return "runtime-observation";
    case HG_PROV_INFERENCE: return "inference";
    default: return "invalid";
    }
}

/* ======================================================================
 * M25 session lifecycle and public API
 * ====================================================================== */

static void m25_reset_session(HGWorldInitHarness *h) {
    h->readiness = HG_READY_NONE;
    h->init_error = HG_WORLD_OK;
    h->post_ready_updates = 0u;
    h->coordinator_ticks = 0u;
    h->session_seq = 0u;
    h->spawn_effect_done = false;
    h->live_prop_count = 0u;
    h->history_count = 0u;
    h->ownership_count = 0u;
    h->effect_count = 0u;
}

static void m25_write_scene_inputs(HGWorldInitHarness *h) {
    build_synthetic_disc_image(h);
    w32(h, 0x003f4d48u, h->spec.player_arena);
    w32(h, 0x003f4d50u, h->spec.area_arena);
    w32(h, SYNTH_SPAWN_TABLE + 0u, f2u(h->cfg.spawn_x));
    w32(h, SYNTH_SPAWN_TABLE + 4u, f2u(h->cfg.spawn_y));
    w32(h, SYNTH_SPAWN_TABLE + 8u, f2u(h->cfg.spawn_z));
    w32(h, SYNTH_SPAWN_TABLE + 12u, f2u(h->cfg.spawn_yaw));
    for (uint32_t i = 0; i < 4u; ++i)
        w8(h, 0x003fa1b0u + i, h->cfg.area_triple[i]);
    w8(h, 0x0046e106u, h->spec.entry_event_flag);
    w8(h, 0x003fa1bbu, h->spec.player_event_4a0); /* 00141210 copies it to +4a0 */
}

static HGWorldInitError m25_validate(const HGWorldInitHarness *h) {
    if (h->unknown_call_count > 0u)
        return HG_WORLD_ERR_UNKNOWN_CALL;
    if (r32(h, HANDLE_TABLE + HANDLE_PLAYER * 4u) == 0u ||
        r32(h, HANDLE_TABLE + HANDLE_AREA * 4u) == 0u)
        return HG_WORLD_ERR_MISSING_RESOURCE;
    if (r32(h, HANDLE_TABLE + HANDLE_TITLE * 4u) != 0u)
        return HG_WORLD_ERR_STALE_HANDLE;
    if (r32(h, NODE_STACK_TOP) == 0u || r32(h, ACTOR_CTRL_BASE + 0x04u) == 0u)
        return HG_WORLD_ERR_UNINITIALIZED_POOLS;
    float px = u2f(r32(h, PLAYER_ADDR + 0x30u)), py = u2f(r32(h, PLAYER_ADDR + 0x34u));
    float pz = u2f(r32(h, PLAYER_ADDR + 0x38u)), pw = u2f(r32(h, PLAYER_ADDR + 0x3cu));
    if (!isfinite(px) || !isfinite(py) || !isfinite(pz) || pw != 1.0f)
        return HG_WORLD_ERR_MISSING_SPAWN;
    if (h->domain_fault_count > 0u)
        return h->last_domain_fault == 0x30u && r8(h, PLAYER_ADDR + 0x18u) == 0u
                   ? HG_WORLD_ERR_PLAYER_NOT_INITIALIZED : HG_WORLD_ERR_DOMAIN_FAULT;
    if (r8(h, PLAYER_ADDR + 0x18u) != 1u || r8(h, PLAYER_ADDR + 0x00u) == 0u)
        return HG_WORLD_ERR_PLAYER_NOT_INITIALIZED;
    uint32_t object = PLAYER_ADDR + 0x90u;
    if (r32(h, object + 0x10u) == 0u || r32(h, object + 0x58u) == 0u ||
        r32(h, object + 0x54u) < HG_WORLD_MIN_PLAYER_BONES)
        return HG_WORLD_ERR_INVALID_PLAYER_BINDING;
    if (!h->camera_init_ran || !h->camera_updated_this_cycle)
        return HG_WORLD_ERR_CAMERA_UNINITIALIZED;
    return HG_WORLD_OK;
}

static HGWorldInitError m25_publish(HGWorldInitHarness *h) {
    if (h->readiness != HG_READY_BOOTED && h->readiness != HG_READY_TORN_DOWN)
        return HG_WORLD_ERR_LIFECYCLE_STATE;
    h->current_stage = STAGE_PUBLISH;
    uint32_t title_before = r32(h, HANDLE_TABLE + HANDLE_TITLE * 4u);
    HGWorldInitError err = run_pack_dir(h, h->spec.player_dir);
    if (err != HG_WORLD_OK) {
        m25_set_ready(h, HG_READY_FAILED, (uint32_t)err);
        return err;
    }
    uint32_t hp = r32(h, HANDLE_TABLE + HANDLE_PLAYER * 4u);
    if (hp != 0u)
        m25_own(h, HG_OWN_HANDLE_PUBLISH, HANDLE_PLAYER, hp, 0x00130900u);
    if (!h->sfaults.omit_area_publish) {
        err = run_pack_dir(h, h->spec.area_dir);
        if (err != HG_WORLD_OK) {
            m25_set_ready(h, HG_READY_FAILED, (uint32_t)err);
            return err;
        }
        uint32_t ha = r32(h, HANDLE_TABLE + HANDLE_AREA * 4u);
        if (ha != 0u)
            m25_own(h, HG_OWN_HANDLE_PUBLISH, HANDLE_AREA, ha, 0x00130900u);
        if (title_before != 0u && r32(h, HANDLE_TABLE + HANDLE_TITLE * 4u) == 0u)
            m25_own(h, HG_OWN_HANDLE_REVOKE, HANDLE_TITLE, title_before, 0x0012fb30u);
    }
    if (h->sfaults.bind_player_to_area && r32(h, HANDLE_TABLE + HANDLE_AREA * 4u) != 0u)
        w32(h, HANDLE_TABLE + HANDLE_PLAYER * 4u, r32(h, HANDLE_TABLE + HANDLE_AREA * 4u));
    record_provenance(h, "m25_handle_0x3f", HANDLE_TABLE + HANDLE_PLAYER * 4u, 4u,
                      r32(h, HANDLE_TABLE + HANDLE_PLAYER * 4u), HG_PROV_C_PRODUCED, 0x00130900u);
    record_provenance(h, "m25_handle_0x96", HANDLE_TABLE + HANDLE_AREA * 4u, 4u,
                      r32(h, HANDLE_TABLE + HANDLE_AREA * 4u), HG_PROV_C_PRODUCED, 0x00130900u);
    h->resources_published = true;
    m25_set_ready(h, HG_READY_PUBLISHED, 0u);
    return HG_WORLD_OK;
}

static HGWorldInitError m25_fail(HGWorldInitHarness *h, HGWorldInitError err, bool cleanup) {
    if (cleanup) {
        m25_retire_all(h, false);
        m25_clear_player_camera(h);
        m25_effect(h, HG_EFFECT_PARTIAL_CLEANUP, HG_PROV_SYNTHETIC_MODEL);
    }
    h->world_entered = false;
    m25_set_ready(h, HG_READY_FAILED, (uint32_t)err);
    return err;
}

static HGWorldInitError m25_enter(HGWorldInitHarness *h) {
    if (h->readiness != HG_READY_PUBLISHED)
        return HG_WORLD_ERR_LIFECYCLE_STATE;
    if (r32(h, HANDLE_TABLE + HANDLE_PLAYER * 4u) == 0u ||
        r32(h, HANDLE_TABLE + HANDLE_AREA * 4u) == 0u)
        return m25_fail(h, HG_WORLD_ERR_MISSING_RESOURCE, false);
    if (r32(h, HANDLE_TABLE + HANDLE_TITLE * 4u) != 0u)
        return m25_fail(h, HG_WORLD_ERR_STALE_HANDLE, false);

    h->cycle++;
    h->current_stage = STAGE_WORLD_INIT;
    h->pool_init_ran = false;
    h->spawn_init_ran = false;
    h->camera_init_ran = false;
    h->state0_init_ran = false;
    h->camera_updated_this_cycle = false;
    h->init_error = HG_WORLD_OK;
    h->domain_fault_count = 0u;
    h->last_domain_fault = 0u;
    h->post_ready_updates = 0u;
    h->live_prop_count = 0u;
    h->spawn_effect_done = false;
    m25_set_ready(h, HG_READY_WORLD_SETUP, 0u);

    /* Same entry state as M23 (hg_area_load_step returning 4). */
    hg_mode_init(&h->res_env, 0x0012d1c0u);
    w8(h, MODE_BLOCK + 0x01u, 1u);
    w8(h, MODE_BLOCK + 0x08u, 5u);
    w8(h, MODE_BLOCK + 0x09u, 1u);
    w8(h, MODE_BLOCK + 0x0au, 0u);
    w32(h, SCRATCH_BASE + 0xc0u, MODE_BLOCK);
    w32(h, SCRATCH_BASE + 0xc4u, 2u);
    w32(h, SCRATCH_BASE + 0xccu, 2u);
    hg_fade_black(&h->res_env, 0u, 0u);

    /* Real C 0012e700 sub 0 -> M25 sub-0 models -> sub 1 -> 0012f850. */
    hg_mode_dispatch(&h->res_env);

    if (h->init_error != HG_WORLD_OK)
        return m25_fail(h, h->init_error, true);
    if (h->domain_fault_count != 0u)
        return m25_fail(h, h->last_domain_fault == 0x30u && r8(h, PLAYER_ADDR + 0x18u) == 0u
                               ? HG_WORLD_ERR_PLAYER_NOT_INITIALIZED : HG_WORLD_ERR_DOMAIN_FAULT, true);
    HGWorldInitError err = m25_validate(h);
    if (err != HG_WORLD_OK)
        return m25_fail(h, err, true);
    m25_set_ready(h, HG_READY_FIRST_TICK, 0u);
    hg_fade_service(&h->res_env);
    h->world_entered = true;
    h->frame++;
    m25_set_ready(h, HG_READY_FADING, 0u);
    return HG_WORLD_OK;
}

static HGWorldInitError m25_step(HGWorldInitHarness *h, const HGSyntheticPadInput *pad,
                                 HGWorldFrameSnapshot *out) {
    if (!h->world_entered || (h->readiness != HG_READY_FADING && h->readiness != HG_READY_ACTIVE))
        return HG_WORLD_ERR_LIFECYCLE_STATE;
    h->current_stage = STAGE_ACTIVE_FRAME;
    h->current_pad = pad ? *pad : hg_synthetic_pad_neutral();
    bool active = h->readiness == HG_READY_ACTIVE;
    if (active)
        h->post_ready_updates++;

    hg_pad_service(&h->res_env);
    record_call(h, 0x0013d970u, h->current_pad.buttons,
                ((uint32_t)h->current_pad.lx << 8) | h->current_pad.ly, HG_PROV_C_PRODUCED);
    bool drop = active && h->sfaults.drop_update_at != 0u &&
                h->post_ready_updates == h->sfaults.drop_update_at;
    if (!drop)
        hg_mode_dispatch(&h->res_env);
    hg_fade_service(&h->res_env);
    record_call(h, 0x00139240u, r8(h, FADE_BLOCK), r16(h, FADE_BLOCK + 4u), HG_PROV_C_PRODUCED);
    hg_vblank_start_handler(&h->res_env);
    hg_vblank_end_handler(&h->res_env);
    w32(h, GP_BASE + 0x2f84u, r32(h, GP_BASE + 0x2f84u) + 1u);
    if (active && h->sfaults.teleport_at != 0u && h->post_ready_updates == h->sfaults.teleport_at) {
        w32(h, PLAYER_ADDR + 0x30u, f2u(u2f(r32(h, PLAYER_ADDR + 0x30u)) + 2.5f));
        w32(h, PLAYER_ADDR + 0x38u, f2u(u2f(r32(h, PLAYER_ADDR + 0x38u)) + 2.5f));
    }
    if (h->readiness == HG_READY_FADING && r8(h, FADE_BLOCK) == 0u &&
        r8(h, PLAYER_ADDR + 0x19u) == 1u)
        m25_set_ready(h, HG_READY_ACTIVE, 0u);
    if (out)
        hg_world_harness_snapshot(h, out);
    h->frame++;
    HGWorldInitError err = m25_validate(h);
    if (err != HG_WORLD_OK)
        m25_set_ready(h, HG_READY_FAILED, (uint32_t)err);
    return err;
}

static HGWorldInitError m25_teardown(HGWorldInitHarness *h) {
    HGWorldReadiness r = h->readiness;
    if (r != HG_READY_PUBLISHED && r != HG_READY_WORLD_SETUP && r != HG_READY_FIRST_TICK &&
        r != HG_READY_FADING && r != HG_READY_ACTIVE && r != HG_READY_FAILED)
        return HG_WORLD_ERR_LIFECYCLE_STATE;
    h->current_stage = STAGE_TEARDOWN;
    h->init_error = HG_WORLD_OK;
    record_call(h, 0x0012f800u, 0u, 0u, HG_PROV_C_PRODUCED);
    hg_gameplay_teardown(&h->res_env);
    m25_effect(h, 0x0012f800u, HG_PROV_C_PRODUCED);
    m25_retire_all(h, true);
    if (!h->sfaults.skip_area_handle_revoke) {
        uint32_t ha = r32(h, HANDLE_TABLE + HANDLE_AREA * 4u);
        record_call(h, 0x0012fb30u, 0u, 0u, HG_PROV_C_PRODUCED);
        hg_resource_clear_dynamic_handles(&h->res_env);
        m25_effect(h, 0x0012fb30u, HG_PROV_C_PRODUCED);
        if (ha != 0u)
            m25_own(h, HG_OWN_HANDLE_REVOKE, HANDLE_AREA, ha, 0x0012fb30u);
    }
    uint32_t hp = r32(h, HANDLE_TABLE + HANDLE_PLAYER * 4u);
    if (hp != 0u) {
        /* 0x3f lies outside the C dynamic range (0x96..0x28e); the session
         * published it, so the session revokes it (model policy). */
        w32(h, HANDLE_TABLE + HANDLE_PLAYER * 4u, 0u);
        m25_own(h, HG_OWN_HANDLE_REVOKE, HANDLE_PLAYER, hp, HG_EFFECT_REVOKE_PLAYER_HANDLE);
        m25_effect(h, HG_EFFECT_REVOKE_PLAYER_HANDLE, HG_PROV_SYNTHETIC_MODEL);
    }
    m25_clear_player_camera(h);
    w8(h, FADE_BLOCK, 0u);
    w8(h, FADE_BLOCK + 3u, 0u);
    w16(h, FADE_BLOCK + 4u, 0u);
    h->world_entered = false;
    h->resources_published = false;
    h->state0_init_ran = false;
    h->camera_init_ran = false;
    h->camera_updated_this_cycle = false;
    h->player_pos_prov = HG_PROV_UNKNOWN;
    h->camera_prov = HG_PROV_UNKNOWN;
    HGWorldInitError err = h->init_error;
    m25_set_ready(h, HG_READY_TORN_DOWN, (uint32_t)err);
    return err;
}

/* ---- Built-in original scenes ------------------------------------------ */

uint32_t hg_world_scene_builtin_count(void) { return 2u; }

HGWorldSceneSpec hg_world_scene_builtin(uint32_t index) {
    HGWorldSceneSpec s;
    memset(&s, 0, sizeof(s));
    s.base.area_triple[0] = 1u;
    s.base.seed_stale_title_handle = true;
    if (index == 0u) {
        /* Lantern court: the M23 courtyard extended with a raised dais in
         * the forward path, one world root and one prop with four nodes. */
        memcpy(s.name, "lantern-court", 14u);
        s.base.spawn_x = 12.0f; s.base.spawn_y = 10.0f; s.base.spawn_z = 20.0f;
        s.base.spawn_yaw = 0.0f; s.base.floor_y = 10.0f;
        s.base.wall_min_x = 2.0f; s.base.wall_max_x = 22.0f;
        s.base.wall_min_z = 10.0f; s.base.wall_max_z = 32.0f;
        s.base.player_bones = 20u;
        s.head_offset = 16.5f;
        s.tier_count = 1u;
        s.tiers[0] = (HGWorldFloorTier){9.0f, 15.5f, 23.0f, 27.0f, 10.5f};
        s.root_actors = 1u;
        s.prop_count = 1u;
        s.props[0] = (HGWorldPropSpec){4u, 0u};
        s.player_dir = 0x3fu; s.area_dir = 0x102u;
        s.player_arena = 0x00e00000u; s.area_arena = 0x01100000u;
    } else if (index == 1u) {
        /* Terrace gallery: different spawn, heading, enclosure, two rising
         * terraces, a 24-bone player with a lower head, two roots and three
         * props (one on the priority list) on different PACK placements. */
        memcpy(s.name, "terrace-gallery", 16u);
        s.base.spawn_x = -30.0f; s.base.spawn_y = 4.5f; s.base.spawn_z = -12.0f;
        s.base.spawn_yaw = 1.5707964f; s.base.floor_y = 4.5f;
        s.base.wall_min_x = -40.0f; s.base.wall_max_x = -6.0f;
        s.base.wall_min_z = -20.0f; s.base.wall_max_z = -4.0f;
        s.base.player_bones = 24u;
        s.head_offset = 14.25f;
        s.tier_count = 2u;
        s.tiers[0] = (HGWorldFloorTier){-26.0f, -20.0f, -20.0f, -4.0f, 5.25f};
        s.tiers[1] = (HGWorldFloorTier){-20.0f, -6.0f, -20.0f, -4.0f, 6.0f};
        s.root_actors = 2u;
        s.prop_count = 3u;
        s.props[0] = (HGWorldPropSpec){6u, 0u};
        s.props[1] = (HGWorldPropSpec){3u, 1u};
        s.props[2] = (HGWorldPropSpec){9u, 0u};
        s.player_dir = 0x41u; s.area_dir = 0x105u;
        s.player_arena = 0x00e80000u; s.area_arena = 0x01180000u;
    }
    return s;
}

static bool finite_all(const float *v, size_t n) {
    for (size_t i = 0; i < n; ++i)
        if (!isfinite(v[i]))
            return false;
    return true;
}

HGWorldInitError hg_world_scene_validate(const HGWorldSceneSpec *s) {
    if (!s)
        return HG_WORLD_ERR_NULL_ARG;
    const HGSyntheticSceneConfig *b = &s->base;
    const float f[] = {b->spawn_x, b->spawn_y, b->spawn_z, b->spawn_yaw, b->floor_y,
                       b->wall_min_x, b->wall_max_x, b->wall_min_z, b->wall_max_z, s->head_offset};
    if (!finite_all(f, sizeof(f) / sizeof(f[0])) || s->name[0] == '\0' ||
        memchr(s->name, '\0', sizeof(s->name)) == NULL)
        return HG_WORLD_ERR_INVALID_CONFIG;
    if (!(b->wall_min_x < b->wall_max_x && b->wall_min_z < b->wall_max_z) ||
        !(b->spawn_x > b->wall_min_x && b->spawn_x < b->wall_max_x &&
          b->spawn_z > b->wall_min_z && b->spawn_z < b->wall_max_z) ||
        fabsf(b->spawn_y - b->floor_y) > 0.5f || fabsf(b->spawn_yaw) > 6.2831855f)
        return HG_WORLD_ERR_INVALID_CONFIG;
    if (b->player_bones < HG_WORLD_MIN_PLAYER_BONES || b->player_bones > HG_WORLD_MAX_PLAYER_BONES ||
        !(s->head_offset > 0.0f && s->head_offset < 64.0f))
        return HG_WORLD_ERR_INVALID_CONFIG;
    if (s->tier_count > HG_WORLD_MAX_TIERS || s->root_actors < 1u ||
        s->root_actors > HG_WORLD_MAX_ROOTS || s->prop_count > HG_WORLD_MAX_PROPS)
        return HG_WORLD_ERR_INVALID_CONFIG;
    for (uint32_t i = 0; i < s->tier_count; ++i) {
        const HGWorldFloorTier *t = &s->tiers[i];
        const float tf[] = {t->min_x, t->max_x, t->min_z, t->max_z, t->height};
        /* A step must stay inside the C floor probe (y - 2.0 .. y + 4.0). */
        if (!finite_all(tf, 5u) || !(t->min_x < t->max_x && t->min_z < t->max_z) ||
            t->height <= b->floor_y || t->height - b->floor_y > 1.75f ||
            t->min_x < b->wall_min_x || t->max_x > b->wall_max_x ||
            t->min_z < b->wall_min_z || t->max_z > b->wall_max_z)
            return HG_WORLD_ERR_INVALID_CONFIG;
    }
    for (uint32_t i = 0; i < s->prop_count; ++i)
        if (s->props[i].nodes > HG_WORLD_MAX_PROP_NODES || s->props[i].priority > 1u)
            return HG_WORLD_ERR_INVALID_CONFIG;
    if (s->player_dir < 0x3fu || s->player_dir > 0x4du || s->area_dir < 0x102u ||
        s->area_dir > 0x110u)
        return HG_WORLD_ERR_INVALID_CONFIG;
    const uint32_t lo = 0x00d00000u, hi = 0x01e00000u, span = 0x00010000u;
    if (s->player_arena < lo || s->player_arena > hi - span || (s->player_arena & 0x7ffu) ||
        s->area_arena < lo || s->area_arena > hi - span || (s->area_arena & 0x7ffu) ||
        (s->player_arena < s->area_arena + span && s->area_arena < s->player_arena + span))
        return HG_WORLD_ERR_INVALID_CONFIG;
    if (s->entry_event_flag > 1u || s->player_event_4a0 > 1u)
        return HG_WORLD_ERR_INVALID_CONFIG;
    return HG_WORLD_OK;
}

HGWorldInitHarness *hg_world_session_create(const HGWorldSceneSpec *spec,
                                            const HGWorldSessionFaults *faults) {
    if (hg_world_scene_validate(spec) != HG_WORLD_OK)
        return NULL;
    HGWorldInitHarness *h = hg_world_harness_create(&spec->base, NULL);
    if (!h)
        return NULL;
    h->m25 = true;
    h->spec = *spec;
    if (faults)
        h->sfaults = *faults;
    return h;
}

HGWorldInitError hg_world_session_select_scene(HGWorldInitHarness *h,
                                               const HGWorldSceneSpec *spec) {
    if (!h || !spec)
        return HG_WORLD_ERR_NULL_ARG;
    if (!h->m25 || (h->readiness != HG_READY_BOOTED && h->readiness != HG_READY_TORN_DOWN))
        return HG_WORLD_ERR_LIFECYCLE_STATE;
    HGWorldInitError err = hg_world_scene_validate(spec);
    if (err != HG_WORLD_OK)
        return err;
    h->spec = *spec;
    h->cfg = spec->base;
    m25_write_scene_inputs(h);
    return HG_WORLD_OK;
}

HGWorldInitError hg_world_session_snapshot(const HGWorldInitHarness *h,
                                           HGWorldSessionSnapshot *out) {
    if (!h || !out)
        return HG_WORLD_ERR_NULL_ARG;
    memset(out, 0, sizeof(*out));
    hg_world_harness_snapshot(h, &out->base);
    out->readiness = (uint32_t)h->readiness;
    out->post_ready_updates = h->post_ready_updates;
    out->coordinator_ticks = h->coordinator_ticks;
    for (uint32_t i = 0; i < 3u; ++i)
        out->pool_free[i] = r32(h, ACTOR_CTRL_BASE + 0x0cu + i * 4u);
    out->prio_actor_count = count_actor_list(h, ACTOR_LIST_PRIO);
    out->type2_prio_actor_count = count_actor_list(h, ACTOR_LIST_TYPE2_PRIO);
    out->input_block_3f9f6e = r8(h, 0x003f9f6eu);
    out->call_log_size = (uint32_t)h->call_count;
    out->ownership_log_size = (uint32_t)h->ownership_count;
    return HG_WORLD_OK;
}

HGWorldReadiness hg_world_session_readiness(const HGWorldInitHarness *h) {
    return h ? h->readiness : HG_READY_NONE;
}

bool hg_world_session_is_m25(const HGWorldInitHarness *h) { return h && h->m25; }

size_t hg_world_session_history_count(const HGWorldInitHarness *h) {
    return h ? h->history_count : 0u;
}

const HGWorldStateEvent *hg_world_session_history_at(const HGWorldInitHarness *h, size_t i) {
    return (h && i < h->history_count) ? &h->history[i] : NULL;
}

size_t hg_world_session_ownership_count(const HGWorldInitHarness *h) {
    return h ? h->ownership_count : 0u;
}

const HGOwnershipEvent *hg_world_session_ownership_at(const HGWorldInitHarness *h, size_t i) {
    return (h && i < h->ownership_count) ? &h->ownership[i] : NULL;
}

size_t hg_world_session_effect_count(const HGWorldInitHarness *h) {
    return h ? h->effect_count : 0u;
}

const HGEffectEvent *hg_world_session_effect_at(const HGWorldInitHarness *h, size_t i) {
    return (h && i < h->effect_count) ? &h->effects[i] : NULL;
}

void hg_world_session_reset_call_log(HGWorldInitHarness *h) {
    if (h)
        h->call_count = 0u;
}

size_t hg_world_session_coordinator_order(const uint32_t **out) {
    if (out)
        *out = M25_COORDINATOR_ORDER;
    return sizeof(M25_COORDINATOR_ORDER) / sizeof(M25_COORDINATOR_ORDER[0]);
}

const char *hg_world_readiness_name(HGWorldReadiness r) {
    switch (r) {
    case HG_READY_NONE: return "none";
    case HG_READY_BOOTED: return "booted";
    case HG_READY_PUBLISHED: return "published";
    case HG_READY_WORLD_SETUP: return "world-setup";
    case HG_READY_FIRST_TICK: return "first-tick";
    case HG_READY_FADING: return "fading";
    case HG_READY_ACTIVE: return "active";
    case HG_READY_TORN_DOWN: return "torn-down";
    case HG_READY_FAILED: return "failed";
    default: return "invalid";
    }
}

const char *hg_world_ownership_op_name(HGOwnershipOp op) {
    switch (op) {
    case HG_OWN_ACTOR_ALLOC: return "actor-alloc";
    case HG_OWN_ACTOR_RELEASE: return "actor-release";
    case HG_OWN_NODE_ALLOC: return "node-alloc";
    case HG_OWN_NODE_RELEASE: return "node-release";
    case HG_OWN_HANDLE_PUBLISH: return "handle-publish";
    case HG_OWN_HANDLE_REVOKE: return "handle-revoke";
    default: return "invalid";
    }
}
