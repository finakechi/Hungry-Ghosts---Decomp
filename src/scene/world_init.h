#ifndef HG_WORLD_INIT_H
#define HG_WORLD_INIT_H

#include "../front_end.h"
#include "../game_lifecycle.h"
#include "../resource_loader.h"

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* M23 reusable state-provenance harness and original synthetic world-
 * initialization model.
 *
 * Explicitly separates:
 *   - Synthetic scene/table fixtures (HG_PROV_FIXTURE)
 *   - State produced by existing reconstructed C interfaces (HG_PROV_C_PRODUCED)
 *   - State produced by this original M23 synthetic world-init/scene model
 *     (HG_PROV_SYNTHETIC_MODEL), kept strictly distinct from retail behavior
 *   - Absent/unreconstructed dependencies (HG_PROV_UNKNOWN)
 *
 * Reuses HGResourceEnv, HGReadState/HGReadEnv, the 32 MiB EE RAM + 16 KiB
 * scratchpad guest memory model, and existing M1-M21 C interfaces without
 * modifying audio (M24) or substituting recorded retail coordinates. */

typedef enum {
    HG_PROV_UNKNOWN = 0,
    HG_PROV_FIXTURE = 1,
    HG_PROV_C_PRODUCED = 2,
    HG_PROV_SYNTHETIC_MODEL = 3,
    /* M25 ledger classes (never produced by the M23 path). */
    HG_PROV_ORIGINAL_BYTES = 4,      /* rule characterized from authenticated ELF bytes */
    HG_PROV_RUNTIME_OBSERVATION = 5, /* value observed in an authenticated capture */
    HG_PROV_INFERENCE = 6            /* inferred meaning, or a recorded conflict */
} HGValueProvenance;

typedef enum {
    HG_WORLD_OK = 0,
    HG_WORLD_ERR_NULL_ARG = 1,
    HG_WORLD_ERR_MISSING_RESOURCE = 2,
    HG_WORLD_ERR_UNINITIALIZED_POOLS = 3,
    HG_WORLD_ERR_MISSING_SPAWN = 4,
    HG_WORLD_ERR_PLAYER_NOT_INITIALIZED = 5,
    HG_WORLD_ERR_INVALID_PLAYER_BINDING = 6,
    HG_WORLD_ERR_CAMERA_UNINITIALIZED = 7,
    HG_WORLD_ERR_CALLBACK_ORDER = 8,
    HG_WORLD_ERR_STALE_HANDLE = 9,
    HG_WORLD_ERR_STALE_ACTORS = 10,
    HG_WORLD_ERR_NODE_LEAK = 11,
    HG_WORLD_ERR_PROVENANCE_VIOLATION = 12,
    HG_WORLD_ERR_DOMAIN_FAULT = 13,
    HG_WORLD_ERR_UNKNOWN_CALL = 14,
    /* M25 session errors. */
    HG_WORLD_ERR_INVALID_CONFIG = 15,
    HG_WORLD_ERR_CAPACITY = 16,
    HG_WORLD_ERR_UNSUPPORTED_ENTRY = 17,
    HG_WORLD_ERR_LIFECYCLE_STATE = 18
} HGWorldInitError;

typedef struct {
    /* Independently authored original synthetic courtyard parameters. */
    float spawn_x;
    float spawn_y;
    float spawn_z;
    float spawn_yaw;
    float floor_y;
    float wall_min_x;
    float wall_max_x;
    float wall_min_z;
    float wall_max_z;
    uint32_t player_bones;
    uint8_t area_triple[4];
    bool seed_stale_title_handle;
} HGSyntheticSceneConfig;

typedef struct {
    /* Raw PS2-style digital buttons (active-high) and 8-bit DualShock2 sticks
     * (0x80 = neutral center). Delivered through real hg_pad_service/read. */
    uint16_t buttons;
    uint8_t lx;
    uint8_t ly;
    uint8_t rx;
    uint8_t ry;
} HGSyntheticPadInput;

typedef struct {
    /* Explicit negative-control defect switches (all false in positive runs). */
    bool omit_resource_publish;
    bool omit_pool_init;
    bool omit_spawn_init;
    bool omit_state0_init;
    bool omit_camera_init;
    bool swap_spawn_before_pool_clear;
    bool swap_camera_before_player;
    bool retain_stale_handle;
    bool skip_teardown_pool_reset;
    bool inject_final_position;
    float injected_pos[3];
} HGWorldFaultInject;

typedef struct {
    uint32_t frame;
    uint32_t entry_pc;
    uint32_t a0;
    uint32_t a1;
    HGValueProvenance provenance;
    uint32_t stage;
} HGCallRecord;

typedef struct {
    char label[48];
    uint32_t guest_addr;
    uint32_t size;
    uint32_t value_u32;
    HGValueProvenance provenance;
    uint32_t producer_pc;
    uint32_t frame;
} HGProvenanceRecord;

typedef struct {
    uint32_t frame;
    uint32_t cycle;
    uint32_t mode_handler;
    uint8_t mode_major;
    uint8_t mode_minor;
    uint8_t mode_sub;
    uint8_t fade_phase;
    uint8_t player_active;
    uint8_t player_phase;
    uint8_t player_subphase;
    uint8_t player_substate;
    uint8_t player_grounded_flag;
    uint16_t player_wall_result;
    float player_pos[4];
    float player_prev_pos[4];
    float player_rot[4];
    float player_eye[4];
    float player_speed;
    float player_turn_rate;
    float camera_eye[4];
    float camera_target[4];
    float camera_pitch;
    float camera_yaw;
    uint32_t main_actor_count;
    uint32_t type2_actor_count;
    uint32_t free_node_count;
    uint32_t player_node_count;
    uint32_t handle_player;
    uint32_t handle_area;
    uint32_t handle_title;
    uint32_t counter_3fa190;
    uint32_t counter_46e118;
    uint32_t counter_46d930;
    uint32_t frame_tick_3eaef0;
    uint32_t wall_contact_events;
    uint32_t floor_query_events;
    uint32_t domain_fault_count;
    uint32_t last_domain_fault;
    HGValueProvenance player_pos_provenance;
    HGValueProvenance camera_provenance;
} HGWorldFrameSnapshot;

enum {
    HG_WORLD_MAX_CALLS = 16384u,
    HG_WORLD_MAX_PROVENANCE = 512u,
    HG_WORLD_SYNTH_DISC_SECTORS = 256u
};

typedef struct HGWorldInitHarness HGWorldInitHarness;

/* Default independently authored synthetic scene (distinct from retail AREA1). */
HGSyntheticSceneConfig hg_synthetic_scene_default(void);
HGSyntheticPadInput hg_synthetic_pad_neutral(void);

HGWorldInitHarness *hg_world_harness_create(const HGSyntheticSceneConfig *cfg,
                                            const HGWorldFaultInject *faults);
void hg_world_harness_destroy(HGWorldInitHarness *h);

/* Lifecycle stages on one shared 32 MiB EE RAM + 16 KiB scratchpad bus. */
HGWorldInitError hg_world_harness_cold_boot(HGWorldInitHarness *h);
HGWorldInitError hg_world_harness_publish_resources(HGWorldInitHarness *h);
HGWorldInitError hg_world_harness_enter_world(HGWorldInitHarness *h);
HGWorldInitError hg_world_harness_step_frame(HGWorldInitHarness *h,
                                             const HGSyntheticPadInput *pad,
                                             HGWorldFrameSnapshot *out);
HGWorldInitError hg_world_harness_teardown(HGWorldInitHarness *h);

/* Explicit prerequisite and consistency checks (fail closed, never crash). */
HGWorldInitError hg_world_harness_validate_prereqs(const HGWorldInitHarness *h,
                                                   bool require_active_player);
HGWorldInitError hg_world_harness_snapshot(const HGWorldInitHarness *h,
                                           HGWorldFrameSnapshot *out);

/* Provenance and call-log inspection helpers. */
size_t hg_world_harness_call_count(const HGWorldInitHarness *h);
const HGCallRecord *hg_world_harness_call_at(const HGWorldInitHarness *h, size_t index);
size_t hg_world_harness_provenance_count(const HGWorldInitHarness *h);
const HGProvenanceRecord *hg_world_harness_provenance_at(const HGWorldInitHarness *h,
                                                         size_t index);
uint32_t hg_world_harness_guest_load(const HGWorldInitHarness *h,
                                     uint32_t address, uint32_t width);
/* M23-M24 integration: the harness's single 32 MiB EE RAM, so a co-resident
 * service (the M22/M24 audio bus) shares this guest memory rather than a
 * second copy. Returns NULL unless [address, address + size) lies in EE RAM
 * (the scratchpad is not exposed). The pointer stays valid until destroy. */
uint8_t *hg_world_harness_guest_ram(HGWorldInitHarness *h, uint32_t address, uint32_t size);
uint32_t hg_world_harness_last_domain_fault(const HGWorldInitHarness *h);
uint32_t hg_world_harness_unknown_call_count(const HGWorldInitHarness *h);
uint32_t hg_world_harness_actor_ticks_main(const HGWorldInitHarness *h);
uint32_t hg_world_harness_actor_ticks_type2(const HGWorldInitHarness *h);

const char *hg_world_harness_error_name(HGWorldInitError err);
const char *hg_world_provenance_name(HGValueProvenance prov);

/* ======================================================================
 * M25 world session: connected initialization and state continuity.
 *
 * Opt-in through hg_world_session_create(); harnesses made by
 * hg_world_harness_create() keep the exact M23 behavior. The same lifecycle
 * calls (cold_boot, publish_resources, enter_world, step_frame, teardown)
 * drive a session. A session additionally:
 *   - takes an original scene description (spawn, floor tiers, walls, player
 *     bones and head offset, world actors and their bone nodes, PACK
 *     directories and arenas) and can switch scenes after teardown;
 *   - runs 0012f850 in the 18-service order read from the authenticated ELF,
 *     executing existing C where it exists and recording every other service
 *     as an explicit external seam;
 *   - applies the original-bytes characterization of the 00158560 state-0
 *     selector, the 001e3440(+8.0, -3.0) floor-snap arguments and the common
 *     tail (including the 003f9f6e rule) instead of inserted end states;
 *   - records an ordered state history, an effect trace and an ownership
 *     ledger (actors, bone nodes, handles), and retires everything through
 *     release operations at teardown or after a failed initialization.
 * No accessor here reports an injected fault; checkers must detect faults
 * from guest memory, ledgers and traces.
 * ====================================================================== */

enum {
    HG_WORLD_MAX_TIERS = 4u,
    HG_WORLD_MAX_PROPS = 6u,
    HG_WORLD_MAX_ROOTS = 4u,
    HG_WORLD_MAX_PROP_NODES = 32u,
    HG_WORLD_MIN_PLAYER_BONES = 18u, /* camera reads bone 0x11 */
    HG_WORLD_MAX_PLAYER_BONES = 36u, /* synthetic FLK5 pose table capacity */
    HG_WORLD_MAX_HISTORY = 256u,
    HG_WORLD_MAX_OWNERSHIP = 8192u,
    HG_WORLD_MAX_EFFECTS = 1024u
};

typedef struct {
    float min_x;
    float max_x;
    float min_z;
    float max_z;
    float height;
} HGWorldFloorTier;

typedef struct {
    uint32_t nodes;    /* bone nodes bound through C 00141e40 (0..32) */
    uint32_t priority; /* 0 = main list 00539448, 1 = priority list 00539450 */
} HGWorldPropSpec;

typedef struct {
    char name[24];
    HGSyntheticSceneConfig base; /* spawn, base floor, walls, bones, triple */
    float head_offset;           /* bone 2 local Y offset in the player FLK5 */
    uint32_t tier_count;
    HGWorldFloorTier tiers[HG_WORLD_MAX_TIERS]; /* raised floors; highest wins */
    uint32_t root_actors;        /* world root actors spawned by 00189aa0 (1..4) */
    uint32_t prop_count;
    HGWorldPropSpec props[HG_WORLD_MAX_PROPS];
    uint32_t player_dir;         /* PACK directory publishing handle 0x3f (0x3f..0x4d) */
    uint32_t area_dir;           /* PACK directory publishing handle 0x96 (0x102..0x110) */
    uint32_t player_arena;       /* 003f4d48 arena for the player FLK5 */
    uint32_t area_arena;         /* 003f4d50 arena for the area FLK5 */
    uint8_t entry_event_flag;    /* 0046e106: state-0 selector input */
    uint8_t player_event_4a0;    /* PLAYER+0x4a0: state-0 selector input */
} HGWorldSceneSpec;

typedef struct {
    /* Each switch perturbs an operation; none is visible through the API. */
    bool omit_area_publish;          /* prerequisite */
    bool swap_pool_and_spawn;        /* order: spawn effect before pool reset */
    bool exhaust_nodes_before_player;/* allocation: a prop takes the node pool */
    bool exhaust_actor_pool0;        /* allocation: pool 0 drained before roots */
    bool bind_player_to_area;        /* binding: handle 0x3f aliases 0x96 */
    uint32_t drop_update_at;         /* dropped update: post-readiness index (1-based) */
    uint32_t disable_walls_from;     /* collision: wall query misses from index */
    uint32_t teleport_at;            /* position: displacement injected at index */
    bool ignore_floor_tiers;         /* collision: floor probe ignores tiers */
    uint32_t skip_camera_from;       /* camera: follow skipped from index */
    bool leak_player_node;           /* teardown: one player node not released */
    bool leave_prop_linked;          /* teardown: one prop actor not retired */
    bool skip_area_handle_revoke;    /* teardown: dynamic handles not revoked */
} HGWorldSessionFaults;

typedef enum {
    HG_READY_NONE = 0,
    HG_READY_BOOTED = 1,
    HG_READY_PUBLISHED = 2,
    HG_READY_WORLD_SETUP = 3,
    HG_READY_FIRST_TICK = 4,
    HG_READY_FADING = 5,
    HG_READY_ACTIVE = 6,
    HG_READY_TORN_DOWN = 7,
    HG_READY_FAILED = 8
} HGWorldReadiness;

typedef struct {
    uint32_t seq;
    uint32_t cycle;
    uint32_t frame;
    uint32_t from;
    uint32_t to;
    uint32_t detail; /* HGWorldInitError for FAILED, else 0 */
} HGWorldStateEvent;

typedef enum {
    HG_OWN_ACTOR_ALLOC = 1,
    HG_OWN_ACTOR_RELEASE = 2,
    HG_OWN_NODE_ALLOC = 3,
    HG_OWN_NODE_RELEASE = 4,
    HG_OWN_HANDLE_PUBLISH = 5,
    HG_OWN_HANDLE_REVOKE = 6
} HGOwnershipOp;

typedef struct {
    uint32_t seq;
    uint32_t cycle;
    uint32_t frame;
    uint32_t op;          /* HGOwnershipOp */
    uint32_t object;      /* actor address, node address or handle id */
    uint32_t owner;       /* owning actor (player 003f92d0), or handle value */
    uint32_t producer_pc; /* original or model operation that acted */
} HGOwnershipEvent;

/* Model-only effect tags (no original routine is claimed for these). */
#define HG_EFFECT_RETIRE_ACTOR 0xfe250001u
#define HG_EFFECT_RELEASE_NODES 0xfe250002u
#define HG_EFFECT_REVOKE_PLAYER_HANDLE 0xfe250003u
#define HG_EFFECT_CLEAR_PLAYER_CAMERA 0xfe250004u
#define HG_EFFECT_PARTIAL_CLEANUP 0xfe250005u

typedef struct {
    uint32_t seq;
    uint32_t cycle;
    uint32_t frame;
    uint32_t pc;          /* original routine whose effect ran, or HG_EFFECT_* */
    uint32_t provenance;  /* HGValueProvenance of the effect's implementation */
} HGEffectEvent;

typedef struct {
    HGWorldFrameSnapshot base;
    uint32_t readiness;
    uint32_t post_ready_updates;
    uint32_t coordinator_ticks;
    uint32_t pool_free[3];
    uint32_t prio_actor_count;
    uint32_t type2_prio_actor_count;
    uint32_t input_block_3f9f6e;
    uint32_t call_log_size;
    uint32_t ownership_log_size;
} HGWorldSessionSnapshot;

uint32_t hg_world_scene_builtin_count(void);
/* Built-in original scenes: 0 = "lantern-court", 1 = "terrace-gallery". An
 * out-of-range index returns a spec that fails validation. */
HGWorldSceneSpec hg_world_scene_builtin(uint32_t index);
HGWorldInitError hg_world_scene_validate(const HGWorldSceneSpec *spec);

HGWorldInitHarness *hg_world_session_create(const HGWorldSceneSpec *spec,
                                            const HGWorldSessionFaults *faults);
/* Only after cold boot or teardown: rebuild scene inputs on the live bus. */
HGWorldInitError hg_world_session_select_scene(HGWorldInitHarness *h,
                                               const HGWorldSceneSpec *spec);
HGWorldInitError hg_world_session_snapshot(const HGWorldInitHarness *h,
                                           HGWorldSessionSnapshot *out);
HGWorldReadiness hg_world_session_readiness(const HGWorldInitHarness *h);
bool hg_world_session_is_m25(const HGWorldInitHarness *h);
size_t hg_world_session_history_count(const HGWorldInitHarness *h);
const HGWorldStateEvent *hg_world_session_history_at(const HGWorldInitHarness *h, size_t i);
size_t hg_world_session_ownership_count(const HGWorldInitHarness *h);
const HGOwnershipEvent *hg_world_session_ownership_at(const HGWorldInitHarness *h, size_t i);
size_t hg_world_session_effect_count(const HGWorldInitHarness *h);
const HGEffectEvent *hg_world_session_effect_at(const HGWorldInitHarness *h, size_t i);
/* Clears the call log (cold boot otherwise keeps growing it per cycle). */
void hg_world_session_reset_call_log(HGWorldInitHarness *h);
/* The 0012f850 service order a session executes (model side; checkers must
 * compare it against an independently derived order). */
size_t hg_world_session_coordinator_order(const uint32_t **out);
const char *hg_world_readiness_name(HGWorldReadiness r);
const char *hg_world_ownership_op_name(HGOwnershipOp op);

#endif
