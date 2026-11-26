#ifndef HG_SCRIPT_TITLE_H
#define HG_SCRIPT_TITLE_H

#include "hgscript.h"
#include "../resource_loader.h"

#define HG_TITLE_SCRIPT_STATE 0x00a6bcc0u
#define HG_TITLE_SCRIPT_UNAVAILABLE (-3)

/* Zero-initialize before first use. The normal resource worker owns the
 * published parent. The title scene owns this borrow and the singleton state.
 * Serialize loading and frame calls on the same guest bus. Release the borrow
 * before leaving the title scene or scheduling a resource replacement. */
typedef struct {
    uint32_t parent;
} HGTitleScript;

/* The dialogue branch of the AREA0 title actor. scene is its guest object.
 * Call after worker completion. Returns 1 when record 0 starts, 0 otherwise. */
int hg_title_script_start(HGResourceEnv *, HGScriptCallFP, HGTitleScript *,
                          uint32_t scene);

/* Run the dialogue portion of 0025c170 once per frame. scratch_top is a
 * 16-byte aligned EE RAM stack top with at least 0x30 reserved bytes below it.
 * Use the same synchronous glyph and voice request services on each call. */
int32_t hg_title_script_frame(HGResourceEnv *, HGScriptCallFP, HGTitleScript *,
                              uint32_t scratch_top);

/* Title scene service after guest scene setup has initialized the singleton.
 * The resident timeline can also start records in the same title publication.
 * Keep using the same binding, including between those records. */
int32_t hg_title_script_scene_service(HGResourceEnv *, HGScriptCallFP,
                                      HGTitleScript *, uint32_t scratch_top);

/* Reset the timed renderer and stop its voice while the scene still owns it.
 * A revoked handle is detached without invoking the next scene's services. */
void hg_title_script_release(HGResourceEnv *, HGTitleScript *);

#endif
