#include "title.h"

enum {
    EE_RAM_END = 0x02000000,
    TITLE_HANDLE = HG_SCRIPT_HANDLES + 0x22f * 4,
    LOADER_BUSY = 0x003eacec, NORMAL_ACTIVE = 0x003f8e30,
    FADE_ACTIVE = 0x003eaeb0, TITLE_PRESENTATION = 0x00a6bf00,
    CHILD_INDEX = 18, CHILD_OFFSET = 0xc9a00, CHILD_SIZE = 1376,
    FIRST_COLOR = 0x0039b3e0
};

static uint32_t read(HGResourceEnv *e, uint32_t address, uint32_t width) {
    return e->load(e->user, address, width);
}

static HGScriptEnv script_bus(HGResourceEnv *e, HGScriptCallFP call_fp) {
    return (HGScriptEnv){ e->user, e->load, e->store, e->call, call_fp };
}

static uint32_t completed_parent(HGResourceEnv *e) {
    if (read(e, LOADER_BUSY, 1) || read(e, NORMAL_ACTIVE, 2)) return 0;
    return read(e, TITLE_HANDLE, 4);
}

static void set_dialogue_color(HGResourceEnv *e) {
    for (uint32_t offset = 0; offset < 16; offset += 4)
        e->store(e->user, FIRST_COLOR + offset, 4, 0x43000000);
}

/* In a fixed FLK5, word +8 is a relocation value. The authenticated title
 * layout bounds this child with the next entry instead of using word +8. */
static uint32_t title_collection(HGResourceEnv *e, uint32_t parent) {
    if (!parent || (parent & 15) || parent > EE_RAM_END - CHILD_OFFSET - CHILD_SIZE)
        return 0;
    uint32_t count = hg_flk5_count(e, parent);
    if (count <= CHILD_INDEX + 1 || count > (CHILD_OFFSET - 16) / 4) return 0;
    uint32_t first_type12 = 0;
    while (first_type12 <= CHILD_INDEX &&
           hg_flk5_type(e, parent, first_type12) != 0x0c000000u)
        ++first_type12;
    if (first_type12 != CHILD_INDEX) return 0;

    uint32_t child = hg_flk5_entry(e, parent, CHILD_INDEX);
    if (child != parent + CHILD_OFFSET ||
        hg_flk5_entry(e, parent, CHILD_INDEX + 1) != child + CHILD_SIZE) return 0;
    uint32_t records = read(e, child, 4);
    uint32_t pool = read(e, child + 4, 4);
    if (records == 0 || records > (CHILD_SIZE - 12) / 4 ||
        (pool & 3) || pool > CHILD_SIZE) return 0;
    uint32_t first = 8 + records * 4;
    if (first >= pool || read(e, child + 8, 4) != first) return 0;
    return child;
}

int hg_title_script_start(HGResourceEnv *e, HGScriptCallFP call_fp,
                          HGTitleScript *title, uint32_t scene) {
    if (title->parent || !call_fp || !e->call ||
        !scene || scene > EE_RAM_END - 7 ||
        read(e, scene + 6, 1) || read(e, FADE_ACTIVE, 1)) return 0;
    uint32_t parent = completed_parent(e);
    uint32_t child = title_collection(e, parent);
    if (!child) return 0;

    HGScriptEnv script = script_bus(e, call_fp);
    e->store(e->user, TITLE_PRESENTATION, 4, 0x10000);
    hg_script_timed_init(&script, HG_TITLE_SCRIPT_STATE, child, 0, 0, 0xc2480000);
    e->store(e->user, HG_TITLE_SCRIPT_STATE, 4, 0);
    e->store(e->user, scene + 6, 1, 1);
    title->parent = parent;
    return 1;
}

static int32_t service(HGResourceEnv *e, HGScriptCallFP call_fp, uint32_t scratch_top) {
    if (!call_fp || (scratch_top & 15) || scratch_top < 0x30 || scratch_top > EE_RAM_END)
        return HG_TITLE_SCRIPT_UNAVAILABLE;

    HGScriptEnv script = script_bus(e, call_fp);
    set_dialogue_color(e);
    int32_t phase = hg_script_timed_tick(&script, HG_TITLE_SCRIPT_STATE);
    hg_script_timed_draw(&script, HG_TITLE_SCRIPT_STATE, scratch_top);
    set_dialogue_color(e);
    return phase;
}

int32_t hg_title_script_frame(HGResourceEnv *e, HGScriptCallFP call_fp,
                              HGTitleScript *title, uint32_t scratch_top) {
    if (!title->parent) return HG_TITLE_SCRIPT_UNAVAILABLE;
    if (completed_parent(e) != title->parent) {
        hg_title_script_release(e, title);
        return HG_TITLE_SCRIPT_UNAVAILABLE;
    }
    return service(e, call_fp, scratch_top);
}

int32_t hg_title_script_scene_service(HGResourceEnv *e, HGScriptCallFP call_fp,
                                      HGTitleScript *title, uint32_t scratch_top) {
    if (title->parent) return hg_title_script_frame(e, call_fp, title, scratch_top);
    if (!completed_parent(e)) return HG_TITLE_SCRIPT_UNAVAILABLE;
    return service(e, call_fp, scratch_top);
}

void hg_title_script_release(HGResourceEnv *e, HGTitleScript *title) {
    if (!title->parent) return;
    uint32_t keep_voice = completed_parent(e) != title->parent;
    title->parent = 0;
    HGScriptEnv script = script_bus(e, 0);
    hg_script_timed_reset(&script, HG_TITLE_SCRIPT_STATE, keep_voice);
}
