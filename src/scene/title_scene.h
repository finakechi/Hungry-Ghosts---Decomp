#ifndef HG_TITLE_SCENE_H
#define HG_TITLE_SCENE_H

#include "../hgscript/title.h"

/* Host binding for the single title dialogue on this guest. Zero-initialize
 * when starting a guest. Keep one binding across lifecycle dispatches. */
typedef struct {
    HGTitleScript dialogue;
    HGScriptCallFP call_fp;
    uint32_t scratch_top;
} HGTitleScene;

/* Dispatch the selected title entries from the lifecycle/guest call router
 * while the title overlay is mounted and the resident scene is initialized.
 * Returns 1 for a recognized entry, including actor return 0 on completion.
 * Route all other entries through the rest of the engine or external services.
 * The sequence interpreter and task allocator use the same guest memory. */
int hg_title_scene_call(HGResourceEnv *, HGTitleScene *, uint32_t entry,
                        uint32_t a0, uint32_t *result);

#endif
