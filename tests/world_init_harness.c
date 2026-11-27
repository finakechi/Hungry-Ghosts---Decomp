/* M23 standalone C connected synthetic lifecycle & negative-control harness.
 *
 * Exercises src/scene/world_init.c alongside real disc_read.c,
 * resource_loader.c, game_lifecycle.c, front_end.c on one shared 32 MiB EE RAM
 * + 16 KiB scratchpad guest bus.
 *
 * Pass `--json` to emit a structured machine-readable provenance & trajectory
 * report on stdout (consumed by tools/verify_world_init.py). */
#include "scene/world_init.h"

#include <assert.h>
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

static bool near_f(float a, float b, float eps) {
    return fabsf(a - b) <= eps;
}

typedef struct {
    HGWorldFrameSnapshot after_enter;
    HGWorldFrameSnapshot after_neutral;
    HGWorldFrameSnapshot after_walk;
    HGWorldFrameSnapshot after_turn;
    HGWorldFrameSnapshot after_wall;
    HGWorldFrameSnapshot after_release;
    HGWorldFrameSnapshot after_teardown;
    uint32_t fade_frames;
    uint32_t walk_frames;
    uint32_t turn_frames;
    uint32_t wall_frames;
    uint32_t release_frames;
    size_t call_count;
    size_t provenance_count;
    uint32_t actor_ticks_main;
    uint32_t actor_ticks_type2;
} CycleSummary;

static void run_cycle(HGWorldInitHarness *h, CycleSummary *out, bool verify_wall) {
    memset(out, 0, sizeof(*out));
    HGWorldInitError err = hg_world_harness_publish_resources(h);
    assert(err == HG_WORLD_OK);

    err = hg_world_harness_enter_world(h);
    assert(err == HG_WORLD_OK);
    assert(hg_world_harness_snapshot(h, &out->after_enter) == HG_WORLD_OK);

    /* Verify cold-entry world initialization state. */
    assert(out->after_enter.mode_handler == 0x0012d1c0u);
    assert(out->after_enter.mode_major == 5u && out->after_enter.mode_minor == 1u &&
           out->after_enter.mode_sub == 1u);
    assert(out->after_enter.handle_player != 0u);
    assert(out->after_enter.handle_area != 0u);
    assert(out->after_enter.handle_title == 0u);
    assert(out->after_enter.player_active == 1u);
    assert(out->after_enter.player_phase == 1u);
    assert(out->after_enter.player_node_count == 20u);
    assert(out->after_enter.free_node_count == 2028u);
    assert(out->after_enter.main_actor_count == 1u);
    assert(out->after_enter.type2_actor_count == 3u);
    assert(near_f(out->after_enter.player_pos[0], 12.0f, 1e-4f));
    assert(near_f(out->after_enter.player_pos[1], 10.0f, 1e-4f));
    assert(near_f(out->after_enter.player_pos[2], 20.0f, 1e-4f));
    assert(near_f(out->after_enter.camera_eye[0], 12.0f, 1e-3f));
    assert(near_f(out->after_enter.camera_eye[1], 26.5f, 1e-3f));
    assert(near_f(out->after_enter.camera_eye[2], 20.0f, 1e-3f));
    assert(out->after_enter.player_pos_provenance == HG_PROV_C_PRODUCED);
    assert(out->after_enter.camera_provenance == HG_PROV_C_PRODUCED);

    /* Stage A: Neutral updates through multi-frame fade-in completion + 4 neutral
     * frames. Also probe forward stick mid-fade to verify fade gates movement. */
    HGSyntheticPadInput neutral = hg_synthetic_pad_neutral();
    HGSyntheticPadInput walk_fwd = neutral;
    walk_fwd.ly = 0x00u;
    HGWorldFrameSnapshot snap;
    uint32_t fade_steps = 0u;
    do {
        const HGSyntheticPadInput *in = (fade_steps == 10u) ? &walk_fwd : &neutral;
        err = hg_world_harness_step_frame(h, in, &snap);
        assert(err == HG_WORLD_OK);
        if (fade_steps == 10u) {
            /* While fade_phase != 0, movement remains gated at spawn. */
            assert(snap.fade_phase != 0u);
            assert(near_f(snap.player_pos[2], 20.0f, 1e-4f));
            assert(near_f(snap.player_speed, 0.0f, 1e-5f));
        }
        fade_steps++;
        assert(fade_steps < 128u);
    } while (snap.fade_phase != 0u);
    assert(fade_steps >= 60u);
    out->fade_frames = fade_steps;

    for (unsigned i = 0u; i < 4u; ++i) {
        err = hg_world_harness_step_frame(h, &neutral, &snap);
        assert(err == HG_WORLD_OK);
    }
    out->after_neutral = snap;
    /* Must hold spawn position under neutral stick, with substate active (1,1). */
    assert(snap.fade_phase == 0u);
    assert(snap.player_subphase == 1u && snap.player_substate == 1u);
    assert(snap.player_grounded_flag == 1u);
    assert(near_f(snap.player_pos[0], 12.0f, 1e-4f));
    assert(near_f(snap.player_pos[1], 10.0f, 1e-4f));
    assert(near_f(snap.player_pos[2], 20.0f, 1e-4f));
    assert(near_f(snap.player_speed, 0.0f, 1e-5f));
    assert(near_f(snap.camera_eye[2], 20.0f, 1e-3f));
    /* Default 00141210 pitch bias 0x3d567750 (~3 deg) tilts target +20*cos(3deg). */
    assert(near_f(snap.camera_target[2], 20.0f + 20.0f * cosf(0.05235988f), 1e-2f));

    /* Stage B: 14 consecutive forward-walk frames (left stick Y full up = 0x00). */
    float prev_z = snap.player_pos[2];
    float prev_speed = 0.0f;
    for (unsigned i = 0u; i < 14u; ++i) {
        err = hg_world_harness_step_frame(h, &walk_fwd, &snap);
        assert(err == HG_WORLD_OK);
        assert(snap.player_speed >= prev_speed - 1e-6f);
        assert(snap.player_pos[2] > prev_z);
        assert(near_f(snap.player_pos[1], 10.0f, 1e-4f));
        assert(snap.player_grounded_flag == 1u);
        assert(near_f(snap.camera_eye[2], snap.player_pos[2], 1e-3f));
        assert(snap.player_pos_provenance == HG_PROV_C_PRODUCED);
        prev_z = snap.player_pos[2];
        prev_speed = snap.player_speed;
    }
    out->walk_frames = 14u;
    out->after_walk = snap;
    assert(snap.player_pos[2] > 22.0f);
    assert(snap.player_speed > 0.25f);

    /* Stage C: 12 consecutive turn + walk frames (left stick left-up: lx=0x10, ly=0x00). */
    HGSyntheticPadInput turn_left = neutral;
    turn_left.lx = 0x10u;
    turn_left.ly = 0x00u;
    float prev_yaw = snap.player_rot[1];
    float start_x = snap.player_pos[0];
    for (unsigned i = 0u; i < 12u; ++i) {
        err = hg_world_harness_step_frame(h, &turn_left, &snap);
        assert(err == HG_WORLD_OK);
        assert(snap.player_rot[1] > prev_yaw);
        assert(near_f(snap.camera_yaw, snap.player_rot[1], 2e-2f));
        assert(near_f(snap.player_pos[1], 10.0f, 1e-4f));
        prev_yaw = snap.player_rot[1];
    }
    out->turn_frames = 12u;
    out->after_turn = snap;
    assert(snap.player_rot[1] > 0.35f);
    assert(snap.player_pos[0] > start_x + 0.5f);

    /* Stage D: Sustained forward walk toward synthetic wall boundary (z <= 26.0
     * or x <= 22.0) to exercise real hg_player_static_constraint (0015ebc0). */
    if (verify_wall) {
        uint32_t wall_before = snap.wall_contact_events;
        for (unsigned i = 0u; i < 20u; ++i) {
            err = hg_world_harness_step_frame(h, &walk_fwd, &snap);
            assert(err == HG_WORLD_OK);
            /* Player must stay constrained inside synthetic courtyard walls. */
            assert(snap.player_pos[0] <= 22.05f && snap.player_pos[0] >= 1.95f);
            assert(snap.player_pos[2] <= 32.05f && snap.player_pos[2] >= 9.95f);
        }
        out->wall_frames = 20u;
        out->after_wall = snap;
        assert(snap.wall_contact_events > wall_before);
    } else {
        out->after_wall = snap;
    }

    /* Stage E: 14 consecutive neutral / release frames (braking & settle). */
    float speed_before_release = snap.player_speed;
    assert(speed_before_release > 0.1f);
    for (unsigned i = 0u; i < 14u; ++i) {
        err = hg_world_harness_step_frame(h, &neutral, &snap);
        assert(err == HG_WORLD_OK);
        assert(near_f(snap.player_pos[1], 10.0f, 1e-4f));
    }
    out->release_frames = 14u;
    out->after_release = snap;
    assert(near_f(snap.player_speed, 0.0f, 1e-5f));
    assert(near_f(snap.player_turn_rate, 0.0f, 1e-5f));
    assert(near_f(snap.camera_eye[0], snap.player_pos[0], 1e-3f));
    assert(near_f(snap.camera_eye[2], snap.player_pos[2], 1e-3f));

    out->call_count = hg_world_harness_call_count(h);
    out->provenance_count = hg_world_harness_provenance_count(h);
    out->actor_ticks_main = hg_world_harness_actor_ticks_main(h);
    out->actor_ticks_type2 = hg_world_harness_actor_ticks_type2(h);

    /* Stage F: Reset / teardown via real hg_gameplay_teardown + dynamic handle clear. */
    err = hg_world_harness_teardown(h);
    assert(err == HG_WORLD_OK);
    assert(hg_world_harness_snapshot(h, &out->after_teardown) == HG_WORLD_OK);
    assert(out->after_teardown.mode_handler == 0x00121be0u);
    assert(hg_world_harness_guest_load(h, 0x700000f8u, 4u) == 1u);
    assert(out->after_teardown.handle_area == 0u);
    assert(out->after_teardown.handle_title == 0u);
    assert(out->after_teardown.main_actor_count == 0u);
    assert(out->after_teardown.type2_actor_count == 0u);
    assert(out->after_teardown.free_node_count == 2048u);
    assert(hg_world_harness_unknown_call_count(h) == 0u);
}

typedef struct {
    const char *name;
    HGWorldInitError expected;
    HGWorldInitError actual;
    uint32_t domain_fault;
    bool passed;
} ControlResult;

static ControlResult run_single_control(const char *name,
                                        const HGWorldFaultInject *faults,
                                        HGWorldInitError expected,
                                        bool two_cycle) {
    HGSyntheticSceneConfig cfg = hg_synthetic_scene_default();
    HGWorldInitHarness *h = hg_world_harness_create(&cfg, faults);
    assert(h != NULL);
    HGWorldInitError err = hg_world_harness_cold_boot(h);
    assert(err == HG_WORLD_OK);

    err = hg_world_harness_publish_resources(h);
    if (err == HG_WORLD_OK)
        err = hg_world_harness_enter_world(h);

    if (two_cycle && err == HG_WORLD_OK) {
        HGSyntheticPadInput neutral = hg_synthetic_pad_neutral();
        HGWorldFrameSnapshot snap;
        err = hg_world_harness_step_frame(h, &neutral, &snap);
        if (err == HG_WORLD_OK)
            err = hg_world_harness_teardown(h);
        if (err == HG_WORLD_OK)
            err = hg_world_harness_publish_resources(h);
        if (err == HG_WORLD_OK)
            err = hg_world_harness_enter_world(h);
    }

    ControlResult res = {
        .name = name,
        .expected = expected,
        .actual = err,
        .domain_fault = hg_world_harness_last_domain_fault(h),
        .passed = (err == expected)
    };
    hg_world_harness_destroy(h);
    if (!res.passed) {
        fprintf(stderr, "Control '%s' FAILED: expected %s (%d), got %s (%d) (domain_fault=0x%x)\n",
                name, hg_world_harness_error_name(expected), (int)expected,
                hg_world_harness_error_name(err), (int)err, res.domain_fault);
    }
    assert(res.passed);
    return res;
}

static void print_vec3(const float v[4]) {
    printf("[%.6f, %.6f, %.6f]", (double)v[0], (double)v[1], (double)v[2]);
}

int main(int argc, char **argv) {
    bool emit_json = (argc > 1 && strcmp(argv[1], "--json") == 0);

    /* 1. Full connected synthetic lifecycle: Cycle 1 -> Teardown -> Cycle 2. */
    HGSyntheticSceneConfig cfg = hg_synthetic_scene_default();
    HGWorldInitHarness *h = hg_world_harness_create(&cfg, NULL);
    assert(h != NULL);
    assert(hg_world_harness_cold_boot(h) == HG_WORLD_OK);

    CycleSummary c1, c2;
    run_cycle(h, &c1, true);
    run_cycle(h, &c2, true);

    /* Bit-for-bit deterministic parity between Cycle 1 and Cycle 2! */
    assert(memcmp(c1.after_enter.player_pos, c2.after_enter.player_pos, sizeof(float) * 4u) == 0);
    assert(memcmp(c1.after_walk.player_pos, c2.after_walk.player_pos, sizeof(float) * 4u) == 0);
    assert(memcmp(c1.after_turn.player_pos, c2.after_turn.player_pos, sizeof(float) * 4u) == 0);
    assert(memcmp(c1.after_turn.player_rot, c2.after_turn.player_rot, sizeof(float) * 4u) == 0);
    assert(memcmp(c1.after_wall.player_pos, c2.after_wall.player_pos, sizeof(float) * 4u) == 0);
    assert(memcmp(c1.after_release.player_pos, c2.after_release.player_pos, sizeof(float) * 4u) == 0);
    assert(memcmp(c1.after_release.camera_eye, c2.after_release.camera_eye, sizeof(float) * 4u) == 0);
    assert(memcmp(c1.after_release.camera_target, c2.after_release.camera_target, sizeof(float) * 4u) == 0);
    assert(c1.after_enter.free_node_count == c2.after_enter.free_node_count);
    assert(c1.after_enter.main_actor_count == c2.after_enter.main_actor_count);
    assert(c1.after_enter.type2_actor_count == c2.after_enter.type2_actor_count);
    assert(c2.after_release.counter_3fa190 > c1.after_release.counter_3fa190);

    /* Collect provenance summary before destroying h. */
    size_t prov_total = hg_world_harness_provenance_count(h);
    size_t prov_fixture = 0u, prov_c = 0u, prov_synth = 0u, prov_unk = 0u;
    for (size_t i = 0u; i < prov_total; ++i) {
        const HGProvenanceRecord *r = hg_world_harness_provenance_at(h, i);
        if (r->provenance == HG_PROV_FIXTURE) prov_fixture++;
        else if (r->provenance == HG_PROV_C_PRODUCED) prov_c++;
        else if (r->provenance == HG_PROV_SYNTHETIC_MODEL) prov_synth++;
        else prov_unk++;
    }
    assert(prov_fixture > 0u && prov_c > 0u && prov_synth > 0u && prov_unk == 0u);
    hg_world_harness_destroy(h);

    /* 2. Ten targeted negative controls (all must detect exact violations). */
    ControlResult controls[10];
    {
        HGWorldFaultInject f = {0}; f.omit_resource_publish = true;
        controls[0] = run_single_control("omit-resource-publish", &f,
                                         HG_WORLD_ERR_MISSING_RESOURCE, false);
    }
    {
        HGWorldFaultInject f = {0}; f.omit_pool_init = true;
        controls[1] = run_single_control("omit-pool-init", &f,
                                         HG_WORLD_ERR_UNINITIALIZED_POOLS, false);
    }
    {
        HGWorldFaultInject f = {0}; f.omit_spawn_init = true;
        controls[2] = run_single_control("omit-spawn-init", &f,
                                         HG_WORLD_ERR_MISSING_SPAWN, false);
    }
    {
        HGWorldFaultInject f = {0}; f.omit_state0_init = true;
        controls[3] = run_single_control("omit-state0-player-init", &f,
                                         HG_WORLD_ERR_PLAYER_NOT_INITIALIZED, false);
        assert(controls[3].domain_fault == 0x30u);
    }
    {
        HGWorldFaultInject f = {0}; f.omit_camera_init = true;
        controls[4] = run_single_control("omit-camera-init", &f,
                                         HG_WORLD_ERR_CAMERA_UNINITIALIZED, false);
    }
    {
        HGWorldFaultInject f = {0}; f.swap_spawn_before_pool_clear = true;
        controls[5] = run_single_control("swap-spawn-before-pool-clear", &f,
                                         HG_WORLD_ERR_CALLBACK_ORDER, false);
    }
    {
        HGWorldFaultInject f = {0}; f.swap_camera_before_player = true;
        controls[6] = run_single_control("swap-camera-before-player", &f,
                                         HG_WORLD_ERR_CALLBACK_ORDER, false);
    }
    {
        HGWorldFaultInject f = {0}; f.retain_stale_handle = true;
        controls[7] = run_single_control("retain-stale-title-handle", &f,
                                         HG_WORLD_ERR_STALE_HANDLE, false);
    }
    {
        HGWorldFaultInject f = {0}; f.skip_teardown_pool_reset = true;
        controls[8] = run_single_control("skip-teardown-pool-reset", &f,
                                         HG_WORLD_ERR_STALE_ACTORS, true);
    }
    {
        HGWorldFaultInject f = {0};
        f.inject_final_position = true;
        f.injected_pos[0] = 48.5f;
        f.injected_pos[1] = 38.0f;
        f.injected_pos[2] = 265.0f;
        controls[9] = run_single_control("inject-final-position-fixture", &f,
                                         HG_WORLD_ERR_PROVENANCE_VIOLATION, false);
    }

    if (emit_json) {
        printf("{\n");
        printf("  \"schema\": \"hg-m23-synthetic-lifecycle/v1\",\n");
        printf("  \"synthetic_only\": true,\n");
        printf("  \"retail_equivalence_claimed\": false,\n");
        printf("  \"scene\": {\"spawn\": [%.1f, %.1f, %.1f], \"yaw\": %.1f, \"floor_y\": %.1f, \"walls_x\": [%.1f, %.1f], \"walls_z\": [%.1f, %.1f], \"player_bones\": %u},\n",
               (double)cfg.spawn_x, (double)cfg.spawn_y, (double)cfg.spawn_z,
               (double)cfg.spawn_yaw, (double)cfg.floor_y,
               (double)cfg.wall_min_x, (double)cfg.wall_max_x,
               (double)cfg.wall_min_z, (double)cfg.wall_max_z, cfg.player_bones);
        printf("  \"cycle1\": {\n");
        printf("    \"fade_frames\": %u, \"walk_frames\": %u, \"turn_frames\": %u, \"wall_frames\": %u, \"release_frames\": %u,\n",
               c1.fade_frames, c1.walk_frames, c1.turn_frames, c1.wall_frames, c1.release_frames);
        printf("    \"enter_pos\": "); print_vec3(c1.after_enter.player_pos); printf(",\n");
        printf("    \"neutral_pos\": "); print_vec3(c1.after_neutral.player_pos); printf(",\n");
        printf("    \"walk_pos\": "); print_vec3(c1.after_walk.player_pos); printf(",\n");
        printf("    \"turn_pos\": "); print_vec3(c1.after_turn.player_pos); printf(",\n");
        printf("    \"turn_yaw\": %.6f,\n", (double)c1.after_turn.player_rot[1]);
        printf("    \"wall_pos\": "); print_vec3(c1.after_wall.player_pos); printf(",\n");
        printf("    \"wall_contacts\": %u, \"floor_queries\": %u,\n",
               c1.after_wall.wall_contact_events, c1.after_release.floor_query_events);
        printf("    \"release_pos\": "); print_vec3(c1.after_release.player_pos); printf(",\n");
        printf("    \"release_camera_eye\": "); print_vec3(c1.after_release.camera_eye); printf(",\n");
        printf("    \"release_camera_target\": "); print_vec3(c1.after_release.camera_target); printf(",\n");
        printf("    \"release_camera_yaw\": %.6f,\n", (double)c1.after_release.camera_yaw);
        printf("    \"free_nodes_active\": %u, \"free_nodes_after_teardown\": %u,\n",
               c1.after_enter.free_node_count, c1.after_teardown.free_node_count);
        printf("    \"main_actors_active\": %u, \"type2_actors_active\": %u,\n",
               c1.after_enter.main_actor_count, c1.after_enter.type2_actor_count);
        printf("    \"actor_ticks_main\": %u, \"actor_ticks_type2\": %u,\n",
               c1.actor_ticks_main, c1.actor_ticks_type2);
        printf("    \"counters_release\": {\"3fa190\": %u, \"46e118\": %u, \"46d930\": %u, \"3eaef0\": %u}\n",
               c1.after_release.counter_3fa190, c1.after_release.counter_46e118,
               c1.after_release.counter_46d930, c1.after_release.frame_tick_3eaef0);
        printf("  },\n");
        printf("  \"cycle2_parity\": {\n");
        printf("    \"bit_exact_trajectory_match\": true,\n");
        printf("    \"free_nodes_active\": %u, \"free_nodes_after_teardown\": %u,\n",
               c2.after_enter.free_node_count, c2.after_teardown.free_node_count);
        printf("    \"main_actors_active\": %u, \"type2_actors_active\": %u,\n",
               c2.after_enter.main_actor_count, c2.after_enter.type2_actor_count);
        printf("    \"counters_release\": {\"3fa190\": %u, \"46e118\": %u, \"46d930\": %u, \"3eaef0\": %u}\n",
               c2.after_release.counter_3fa190, c2.after_release.counter_46e118,
               c2.after_release.counter_46d930, c2.after_release.frame_tick_3eaef0);
        printf("  },\n");
        printf("  \"provenance_ledger\": {\"total\": %zu, \"fixture\": %zu, \"c_produced\": %zu, \"synthetic_model\": %zu, \"unknown\": %zu},\n",
               prov_total, prov_fixture, prov_c, prov_synth, prov_unk);
        printf("  \"negative_controls\": [\n");
        for (size_t i = 0u; i < 10u; ++i) {
            printf("    {\"name\": \"%s\", \"expected\": \"%s\", \"actual\": \"%s\", \"domain_fault\": %u, \"passed\": %s}%s\n",
                   controls[i].name,
                   hg_world_harness_error_name(controls[i].expected),
                   hg_world_harness_error_name(controls[i].actual),
                   controls[i].domain_fault,
                   controls[i].passed ? "true" : "false",
                   (i + 1u < 10u) ? "," : "");
        }
        printf("  ]\n");
        printf("}\n");
    } else {
        printf("M23 synthetic world-init harness PASSED: 2 full cycles (bit-exact parity), "
               "%zu provenance records, 10/10 negative controls rejected.\n", prov_total);
    }
    return 0;
}
