#include "title_scene.h"

enum {
    MODE_POINTER = 0x700000c0,
    FADE_PHASE = 0x003eaeb0,
    PAD_EDGE = 0x0095e568,
    SCENE_TITLE_FLAG = 0x003f9fdf,
    TITLE_PRESENTATION = 0x00a6bf00,
    CAMERA_SCALE = 0x003eb050,
    TITLE_RENDER_PARAMS = 0x003f97f0,
    TITLE_SEQUENCE = 0x00a8a580,
    TITLE_SEQUENCE_ALTERNATE = 0x00a8a7f0
};

static uint32_t load(HGResourceEnv *env, uint32_t address, uint32_t width) {
    return env->load(env->user, address, width);
}

static void store(HGResourceEnv *env, uint32_t address, uint32_t width, uint32_t value) {
    env->store(env->user, address, width, value);
}

static uint32_t call(HGResourceEnv *env, uint32_t entry, uint32_t a0, uint32_t a1,
                     uint32_t a2) {
    return env->call(env->user, entry, a0, a1, a2, 0);
}

/* 00a88660: the front end submits the root actor and initializes its variant. */
static void create_title(HGResourceEnv *env, uint32_t variant) {
    uint32_t actor = call(env, 0x1429b0, 0, 5, 0xa88a20);
    if (actor) store(env, actor + 8, 4, variant);
}

/* 00a88a20: root actor, driven by list 001430a0. The timeline is independently
 * serviced by 0025ddd0 later in 00141470. */
static void title_actor(HGResourceEnv *env, uint32_t actor) {
    uint32_t phase = load(env, actor + 0x18, 1);
    if (phase == 2 || phase == 3) {
        call(env, 0x142e90, actor, 0, 0);
        return;
    }
    if (phase == 0) {
        store(env, actor + 0x18, 1, 1);
        if (!load(env, actor + 8, 4)) store(env, SCENE_TITLE_FLAG, 1, 1);
        phase = 1;
    }
    if (phase != 1) return;

    uint32_t step = load(env, actor + 0x19, 1);
    switch (step) {
    case 0:
        if (!call(env, 0x25cd90, 0, 0, 0)) return;
        call(env, 0x25e6c0, 0, 0, 0);
        call(env, 0x25e930, load(env, actor + 8, 4) ?
             TITLE_SEQUENCE_ALTERNATE : TITLE_SEQUENCE, 0xff, 0);
        break;
    case 1:
        if (!call(env, 0x25ce90, 0, 0, 0)) return;
        break;
    case 2:
        store(env, actor + 0x18, 1, 3);
        return;
    default:
        return;
    }
    store(env, actor + 0x19, 1, step + 1);
}

/* 00a88b40: timeline opcode 0x14 calls this with its copied 0x30-byte slot.
 * The adjacent bytes +5 and +6 are the callback's own phase and subphase. */
static uint32_t title_dialogue(HGResourceEnv *env, HGTitleScene *scene, uint32_t task) {
    switch (load(env, task + 5, 1)) {
    case 0:
        call(env, 0x166770, 0, 0, 0);
        call(env, 0x1390b0, 4, 0, 0);
        store(env, task + 5, 1, 1);
        store(env, task + 6, 1, 0);
        break;
    case 1: {
        if (load(env, PAD_EDGE, 2) & 0x920) store(env, task + 6, 1, 2);
        store(env, 0x70000ba0, 4, 0x42800000);
        store(env, 0x70000ba4, 4, 0x41800000);
        store(env, 0x70000ba8, 4, 0x41800000);
        store(env, 0x70000bac, 4, 0x43000000);
        call(env, 0x166910, 2, 0x70000ba0, 0);
        uint32_t step = load(env, task + 6, 1);
        if (step == 0) {
            hg_title_script_start(env, scene->call_fp, &scene->dialogue, task);
        } else if (step == 1 && load(env, HG_TITLE_SCRIPT_STATE, 4) == UINT32_MAX) {
            store(env, task + 6, 1, 2);
        } else if (step == 2) {
            store(env, task + 5, 1, 2);
            store(env, task + 6, 1, 0);
            call(env, 0x139040, 4, 0, 0);
        }
        break;
    }
    case 2:
        if (load(env, FADE_PHASE, 1) != 2) break;
        if (scene->dialogue.parent) {
            hg_title_script_release(env, &scene->dialogue);
        } else {
            /* The player can skip before record zero starts. */
            HGScriptEnv script = {env->user, env->load, env->store, env->call, scene->call_fp};
            hg_script_timed_reset(&script, HG_TITLE_SCRIPT_STATE, 0);
        }
        call(env, 0x139010, 0, 0, 0);
        store(env, SCENE_TITLE_FLAG, 1, 0);
        store(env, TITLE_PRESENTATION, 4, 0x10000);
        store(env, CAMERA_SCALE, 4, 0x3f800000);
        call(env, 0x1404f0, 0, 1, 0);
        return 0;
    default:
        break;
    }
    return 1;
}

static void dialogue_service(HGResourceEnv *env, HGTitleScene *scene) {
    call(env, 0x139690, 0, 0, 0);
    call(env, 0x167740, 0, 0, 0);
    call(env, 0x265460, 0, 0, 0);
    call(env, 0x25ddd0, 0, 0, 0);
    call(env, 0x25ca90, 0, 0, 0);
    hg_title_script_scene_service(env, scene->call_fp, &scene->dialogue, scene->scratch_top);
}

/* 00141470: front-end scene service, with the original update/render order. */
static void title_frame(HGResourceEnv *env) {
    call(env, 0x198f60, 0, 0, 0);
    call(env, 0x12c980, 0, 0, 0);
    call(env, 0x13b6e0, TITLE_RENDER_PARAMS, 0, 0);
    call(env, 0x1430a0, 0, 0, 0);
    call(env, 0x143100, 0, 0, 0);
    call(env, 0x18c520, 0, 0, 0);
    call(env, 0x143150, 0, 0, 0);
    call(env, 0x1431a0, 0, 0, 0);
    call(env, 0x25c170, 0, 0, 0);
    call(env, 0x182480, 0, 0, 0);
    call(env, 0x13ce80, TITLE_RENDER_PARAMS, 0, 0);
    call(env, 0x137ac0, 0, 0, 0);
}

int hg_title_scene_call(HGResourceEnv *env, HGTitleScene *scene, uint32_t entry,
                        uint32_t a0, uint32_t *result) {
    *result = 0;
    switch (entry) {
    case 0xa88660: create_title(env, a0); return 1;
    case 0xa88a20: title_actor(env, a0); return 1;
    case 0xa88b40: *result = title_dialogue(env, scene, a0); return 1;
    case 0xa886e0: store(env, load(env, MODE_POINTER, 4) + 0x1a, 2, 0); return 1;
    case 0x141470: title_frame(env); return 1;
    case 0x25c170: dialogue_service(env, scene); return 1;
    default: return 0;
    }
}
