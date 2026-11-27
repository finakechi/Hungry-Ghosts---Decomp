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
    HG_PROV_SYNTHETIC_MODEL = 3
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
    HG_WORLD_ERR_UNKNOWN_CALL = 14
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
uint32_t hg_world_harness_last_domain_fault(const HGWorldInitHarness *h);
uint32_t hg_world_harness_unknown_call_count(const HGWorldInitHarness *h);
uint32_t hg_world_harness_actor_ticks_main(const HGWorldInitHarness *h);
uint32_t hg_world_harness_actor_ticks_type2(const HGWorldInitHarness *h);

const char *hg_world_harness_error_name(HGWorldInitError err);
const char *hg_world_provenance_name(HGValueProvenance prov);

#endif
