/* Hungry Ghosts audio subsystem - reconstructed original EE functions.
 *
 * Each function is a faithful C translation of the verified original
 * instruction bodies, with the original entry address and body ranges
 * documented. The guest bus (HGAudioBus) supplies memory and external
 * calls; Sony kernel syscalls and EI/DI remain explicit SDK boundaries.
 *
 * Verification: tools/hg_audio checks every reconstructed body against
 * the authenticated ELF bytes and compares calculated memory effects
 * with the original instructions interpreted by the repository's EE
 * subset (tests/test_hg_audio_*.py).
 */
#include "audio_engine.h"

#define U32(v) ((uint32_t)(v))

/* ------------------------------------------------------------------ */
/* 0x00156a50 hg_audio_register_entry - 156a50..156ac4                  */
/* Gate byte at HG_AUDIO_GATE_STATE: values 0/1/2 advance to 2 and run */
/* the worker; values above 2 clear gate+phase and return 0.           */
/* ------------------------------------------------------------------ */
uint32_t hg_audio_register_entry(HGAudioBus *bus, uint32_t resource) {
    uint32_t state = bus->r8(bus->ctx, HG_AUDIO_GATE_STATE);
    if (state == 2) {
        return hg_audio_register_worker(bus, resource);
    }
    if (state == 1) {
        bus->w8(bus->ctx, HG_AUDIO_GATE_STATE, (uint8_t)(state + 1));
        return hg_audio_register_worker(bus, resource);
    }
    if (state == 0) {
        bus->w8(bus->ctx, HG_AUDIO_GATE_STATE, (uint8_t)(state + 1));
        uint32_t next = bus->r8(bus->ctx, HG_AUDIO_GATE_STATE);
        bus->w8(bus->ctx, HG_AUDIO_GATE_STATE, (uint8_t)(next + 1));
        return hg_audio_register_worker(bus, resource);
    }
    bus->w8(bus->ctx, HG_AUDIO_GATE_STATE, 0);
    bus->w8(bus->ctx, HG_AUDIO_PHASE, 0);
    return 0;
}

/* ------------------------------------------------------------------ */
/* 0x00157060 hg_audio_register_worker - 157060..15741c                 */
/* Descriptor phase machine. resource layout (verified against the two */
/* observed members 6b/51 and 85/94):                                  */
/*   +0x00 total size; +0x04 0x30; +0x08 descriptor-table shift;       */
/*   +0x0c descriptor count; +0x10 retained size; +0x14 payload start; */
/*   +0x18 payload size; +0x1c 0.                                      */
/*   descriptor[n] at resource+0x20 + word(+0x08)*0x10 + n*0x10:       */
/*     +0x00 payload size; +0x04 bank offset; +0x08 bank id; +0x0c 0x7f */
/* Phases 0..5 process one descriptor; phase>=6 computes the endpoint. */
/* ------------------------------------------------------------------ */
uint32_t hg_audio_register_worker(HGAudioBus *bus, uint32_t resource) {
    /* Phase jump table (verified from 0x003db520):
     *   0 -> 1570c0 init
     *   1 -> 1570f8 descriptor selection
     *   2 -> 157198 SIF allocation
     *   3 -> 1571c8 payload transfer setup
     *   4 -> 1571f8 transfer poll
     *   5 -> 157228 bank registration + second transfer
     *   6 -> 1572d0 second poll + bind + advance
     */
    uint32_t phase = bus->r8(bus->ctx, HG_AUDIO_PHASE);
    uint32_t desc = bus->r32(bus->ctx, HG_AUDIO_DESC_CURSOR);
    uint32_t dma_ptr = bus->r32(bus->ctx, HG_AUDIO_DESC_DMA);
    uint32_t alloc_ptr = bus->r32(bus->ctx, HG_AUDIO_DESC_ALLOC);
    uint32_t desc_id = bus->r32(bus->ctx, HG_AUDIO_DESC_ID);
    uint32_t desc_src = bus->r32(bus->ctx, HG_AUDIO_DESC_SOURCE);
    uint32_t desc_saved = bus->r32(bus->ctx, HG_AUDIO_DESC_SAVED);

    if (phase >= 7) goto endpoint;

    switch (phase) {
    case 0: /* 1570c0: init cursors, then fall into descriptor selection */
        bus->w8(bus->ctx, HG_AUDIO_PHASE, 1);
        bus->w8(bus->ctx, HG_AUDIO_DESC_COUNT, 0);
        bus->w32(bus->ctx, HG_AUDIO_DESC_ID, U32(-1));
        desc = U32(resource + 0x20 + (bus->r32(bus->ctx, resource + 8) << 4));
        bus->w32(bus->ctx, HG_AUDIO_DESC_CURSOR, desc);
        desc_src = U32(resource + bus->r32(bus->ctx, resource + 0x18));
        bus->w32(bus->ctx, HG_AUDIO_DESC_SOURCE, desc_src);
        desc_id = U32(-1);
        /* fall through */
    case 1: /* 1570f8: descriptor selection + slot table publish */
        bus->w8(bus->ctx, HG_AUDIO_PHASE, 2);
        if (bus->r32(bus->ctx, desc + 8) != desc_id) {
            uint32_t count;
            desc_id = bus->r32(bus->ctx, desc + 8);
            bus->w32(bus->ctx, HG_AUDIO_DESC_ID, desc_id);
            desc_saved = bus->r32(bus->ctx, HG_AUDIO_CTX_SAVE + desc_id * 4);
            bus->w32(bus->ctx, HG_AUDIO_DESC_SAVED, desc_saved);
            count = bus->r32(bus->ctx, HG_AUDIO_DESC_IDX_BASE + desc_id * 4);
            if ((int32_t)count > 0) {
                uint32_t i = 0;
                do {
                    bus->call(bus->ctx, 0x002b54e8,
                              bus->r32(bus->ctx, U32(0x003f2740 + desc_id * 0x78 + i * 4)),
                              0, 0, 0, 0);
                    i++;
                } while ((int32_t)i < (int32_t)bus->r32(
                             bus->ctx, HG_AUDIO_DESC_IDX_BASE + desc_id * 4));
            }
            bus->w32(bus->ctx, HG_AUDIO_DESC_IDX_BASE + desc_id * 4, 0);
        }
        /* fall through */
    case 2: /* 157198: allocate IOP-side payload buffer */
        alloc_ptr = bus->call(bus->ctx, HG_X_SIF_ALLOC,
                              U32(bus->r32(bus->ctx, desc + 0) + 0x10),
                              0, 0, 0, 0);
        bus->w32(bus->ctx, HG_AUDIO_DESC_ALLOC, alloc_ptr);
        if (alloc_ptr == 0) goto endpoint;
        bus->w8(bus->ctx, HG_AUDIO_PHASE, 3);
        goto endpoint;
    case 3: /* 1571c8: payload transfer to the IOP-side buffer */
        dma_ptr = hg_audio_transfer_setup(bus, desc_src,
                                          U32(alloc_ptr & 0xfffffff0),
                                          bus->r32(bus->ctx, desc + 0), 0);
        bus->w32(bus->ctx, HG_AUDIO_DESC_DMA, dma_ptr);
        bus->w8(bus->ctx, HG_AUDIO_PHASE, 4);
        goto endpoint;
    case 4: /* 1571f8: poll the payload transfer */
        if ((int32_t)bus->call(bus->ctx, HG_X_SIF_DMA_STAT, dma_ptr,
                               0, 0, 0, 0) >= 0) {
            goto endpoint; /* still busy: park, retry next call */
        }
        bus->w8(bus->ctx, HG_AUDIO_PHASE, 5);
        bus->call(bus->ctx, HG_X_FLUSHCACHE, 0, 0, 0, 0, 0);
        goto endpoint;
    case 5: { /* 157228: bank registration + second transfer */
        uint32_t id = bus->r32(bus->ctx, desc + 8);
        uint32_t slot = hg_audio_bank_register_wrapper(
            bus, U32(resource + bus->r32(bus->ctx, desc + 4)));
        bus->w32(bus->ctx, U32(0x003f2740 + id * 0x78 +
                 (bus->r32(bus->ctx, HG_AUDIO_DESC_IDX_BASE + id * 4) << 2)),
                 slot);
        if ((int32_t)bus->r32(bus->ctx, U32(0x003f2740 + id * 0x78 +
                (bus->r32(bus->ctx, HG_AUDIO_DESC_IDX_BASE + id * 4) << 2))) < 0) {
            goto endpoint;
        }
        bus->w8(bus->ctx, HG_AUDIO_PHASE, 6);
        dma_ptr = bus->call(bus->ctx, 0x002a7560,
                            U32(alloc_ptr & 0xfffffff0), desc_saved,
                            bus->r32(bus->ctx, desc + 0), 0, 0);
        bus->w32(bus->ctx, HG_AUDIO_DESC_DMA, dma_ptr);
        goto endpoint;
    }
    case 6: { /* 1572d0: second poll + bind + MSCOM + advance */
        uint32_t id;
        if (bus->call(bus->ctx, 0x002a7590, dma_ptr, 0, 0, 0, 0) != 0) {
            goto endpoint; /* still busy */
        }
        id = bus->r32(bus->ctx, desc + 8);
        {
            uint32_t entry = bus->r32(bus->ctx, U32(0x003f2740 + id * 0x78 +
                (bus->r32(bus->ctx, HG_AUDIO_DESC_IDX_BASE + id * 4) << 2)));
            bus->call(bus->ctx, 0x002b5428, entry, desc_saved, 0, 0, 0);
            hg_audio_bank_bind(bus, entry, bus->r32(bus->ctx, desc + 0x0c));
        }
        bus->w8(bus->ctx, HG_AUDIO_PHASE, 1);
        /* MSCOM reads the first byte of the DMA'd payload at the alloc
         * pointer (IOP-side memory, EE-visible through the SIF window) */
        bus->call(bus->ctx, HG_X_SIF_MSCOM, alloc_ptr, 0, 0, 0, 0);
        desc_saved = U32(desc_saved + bus->r32(bus->ctx, desc + 0));
        bus->w32(bus->ctx, HG_AUDIO_DESC_SAVED, desc_saved);
        desc_src = U32(desc_src + bus->r32(bus->ctx, desc + 0));
        bus->w32(bus->ctx, HG_AUDIO_DESC_SOURCE, desc_src);
        desc = U32(desc + 0x10);
        bus->w32(bus->ctx, HG_AUDIO_DESC_CURSOR, desc);
        bus->w32(bus->ctx, HG_AUDIO_DESC_IDX_BASE + id * 4,
                 bus->r32(bus->ctx, HG_AUDIO_DESC_IDX_BASE + id * 4) + 1);
        bus->w8(bus->ctx, HG_AUDIO_DESC_COUNT,
                (uint8_t)(bus->r8(bus->ctx, HG_AUDIO_DESC_COUNT) + 1));
        goto endpoint;
    }
    default:
        goto endpoint;
    }

endpoint: /* 1573c0..1573f4: endpoint calculation, M7-verified contract */
    if (bus->r8(bus->ctx, HG_AUDIO_DESC_COUNT) <
        bus->r32(bus->ctx, resource + 0x0c)) {
        return 0; /* more descriptors pending */
    }
    bus->w8(bus->ctx, HG_AUDIO_GATE_STATE, 0);
    bus->w8(bus->ctx, HG_AUDIO_PHASE, 0);
    return U32(U32(resource + bus->r32(bus->ctx, resource + 0x10) + 0x40)
               & 0xffffffc0);
}

/* ------------------------------------------------------------------ */
/* 0x00156f20 hg_audio_transfer_setup - 156f20..156fb4                   */
/* sceSifSetDma({src,dest,size,attr=0}, id=1); when wait!=0 poll        */
/* sceSifDmaStat until it reports non-negative.                        */
/* ------------------------------------------------------------------ */
uint32_t hg_audio_transfer_setup(HGAudioBus *bus, uint32_t src, uint32_t dest,
                                 uint32_t size, uint32_t wait) {
    uint32_t handle;
    /* Host-model adaptation: the original builds the {src,dest,size,attr}
     * struct on its stack and passes its address to sceSifSetDma. The
     * reconstructed C writes the identical four words to a fixed guest
     * scratch slot so the modeled kernel boundary can read them. */
    bus->w32(bus->ctx, HG_AUDIO_DMA_SCRATCH + 0x00, src);
    bus->w32(bus->ctx, HG_AUDIO_DMA_SCRATCH + 0x04, dest);
    bus->w32(bus->ctx, HG_AUDIO_DMA_SCRATCH + 0x08, size);
    bus->w32(bus->ctx, HG_AUDIO_DMA_SCRATCH + 0x0c, 0);
    handle = bus->call(bus->ctx, HG_X_SIF_SET_DMA, HG_AUDIO_DMA_SCRATCH,
                       1, 0, 0, 0);
    if (handle == 0) return U32(-1);
    if (wait != 0) {
        while ((int32_t)bus->call(bus->ctx, HG_X_SIF_DMA_STAT, handle,
                                  0, 0, 0, 0) < 0) {
        }
        bus->call(bus->ctx, HG_X_FLUSHCACHE, 0, 0, 0, 0, 0);
    }
    return handle;
}

/* ------------------------------------------------------------------ */
/* 0x00156fc0 hg_audio_align16 - 156fc0..156ff4                          */
/* Allocate size+0x10 bytes, then round the result up to 16.           */
/* ------------------------------------------------------------------ */
uint32_t hg_audio_align16(HGAudioBus *bus, uint32_t size) {
    uint32_t v = bus->call(bus->ctx, HG_X_SIF_ALLOC, U32(size + 0x10),
                           0, 0, 0, 0);
    if ((v & 0xf) != 0) {
        v = U32((v & 0xfffffff0) + 0x10);
    }
    return v;
}

/* ------------------------------------------------------------------ */
/* 0x002b4db0 hg_audio_slot_find - 2b4db0..2b4df0                        */
/* First free slot in the 8-entry slot table; -1 when full.            */
/* ------------------------------------------------------------------ */
uint32_t hg_audio_slot_find(HGAudioBus *bus) {
    uint32_t index = 0;
    uint32_t cursor = HG_AUDIO_SLOT_TABLE;
    while (1) {
        if (bus->r32(bus->ctx, cursor) == 0) {
            return index;
        }
        index++;
        if ((int32_t)index >= 8) {
            return U32(-1);
        }
        cursor = U32(cursor + HG_SLOT_STRIDE);
    }
}

/* ------------------------------------------------------------------ */
/* 0x002b4df0 hg_audio_bank_register - 2b4df0..2b4f68                    */
/* slot = pointer into the slot table; bank = SShd header pointer.     */
/* Validates magic at bank+0x0c, requires slot state 0, stores slot    */
/* state 3/4 (per bank+0x7c), rebases the six offsets at bank+0x10..   */
/* +0x24 into pointers at bank+0x30..+0x44, and publishes table        */
/* headers into the slot. Returns 0 on success, -1 on failure.         */
/* ------------------------------------------------------------------ */
uint32_t hg_audio_bank_register(HGAudioBus *bus, uint32_t slot, uint32_t bank) {
    uint32_t i;
    if (bus->r32(bus->ctx, bank + HG_BANK_MAGIC_OFF) != HG_BANK_MAGIC) {
        return U32(-1);
    }
    if (bus->r32(bus->ctx, slot + HG_SLOT_STATE) != 0) {
        return U32(-1);
    }
    bus->w32(bus->ctx, slot + HG_SLOT_STATE,
             bus->r32(bus->ctx, bank + HG_BANK_STATE) == U32(-1) ? 3 : 4);
    bus->w32(bus->ctx, slot + HG_SLOT_BANK, bank);
    for (i = 0; i < 6; i++) {
        uint32_t offset = bus->r32(bus->ctx, bank + HG_BANK_OFF0 + i * 4);
        bus->w32(bus->ctx, bank + HG_BANK_PTR0 + i * 4,
                 offset == U32(-1) ? 0 : U32(bank + offset));
    }
    bus->w32(bus->ctx, slot + HG_SLOT_BANK2, bank); /* 2b4ef4 delay slot */
    {
        uint32_t t2 = bus->r32(bus->ctx, bank + 0x34);
        if (t2 != 0) {
            bus->w32(bus->ctx, slot + HG_SLOT_T2HDR, bus->r16(bus->ctx, t2));
            bus->w32(bus->ctx, slot + HG_SLOT_T2PTR, U32(t2 + 2));
        } else {
            bus->w32(bus->ctx, slot + HG_SLOT_T2HDR, U32(-1));
            bus->w32(bus->ctx, slot + HG_SLOT_T2PTR, 0);
        }
    }
    {
        uint32_t t3 = bus->r32(bus->ctx, bank + 0x38);
        if (t3 != 0) {
            bus->w32(bus->ctx, slot + HG_SLOT_T3HDR, bus->r16(bus->ctx, t3));
            bus->w32(bus->ctx, slot + HG_SLOT_T3PTR, t3);
            bus->w32(bus->ctx, slot + HG_SLOT_T3PTR2, U32(t3 + 2));
        } else {
            bus->w32(bus->ctx, slot + HG_SLOT_T3HDR, U32(-1));
            bus->w32(bus->ctx, slot + HG_SLOT_T3PTR2, 0);
        }
    }
    bus->w32(bus->ctx, slot + HG_SLOT_VOLUME, 0x7f);
    return 0;
}

/* ------------------------------------------------------------------ */
/* 0x002b4f68 hg_audio_bank_index - 2b4f68..2b4f88                       */
/* table1 (bank+0x30) + u16(table1 + index*2 + 2).                     */
/* ------------------------------------------------------------------ */
uint32_t hg_audio_bank_index(HGAudioBus *bus, uint32_t slot, uint32_t index) {
    uint32_t bank = bus->r32(bus->ctx, slot + HG_SLOT_BANK2);
    uint32_t table = bus->r32(bus->ctx, bank + HG_BANK_PTR0);
    return U32(table + bus->r16(bus->ctx, U32(table + index * 2 + 2)));
}

/* ------------------------------------------------------------------ */
/* 0x002b4f88 hg_audio_slot_active - 2b4f88..2b4fc4                      */
/* 1 when 0 <= slot < 8 and slot state word is nonzero.                */
/* ------------------------------------------------------------------ */
uint32_t hg_audio_slot_active(HGAudioBus *bus, uint32_t slot) {
    if ((int32_t)slot < 0 || (int32_t)slot >= 8) {
        return 0;
    }
    return bus->r32(bus->ctx, HG_AUDIO_SLOT_TABLE + slot * HG_SLOT_STRIDE) > 0;
}

/* ------------------------------------------------------------------ */
/* 0x002b4fc8 hg_audio_slot_state - 2b4fc8..2b5004                       */
/* *out_auxc = slot+0x0c; *out_state = slot+0x00.                      */
/* ------------------------------------------------------------------ */
void hg_audio_slot_state(HGAudioBus *bus, uint32_t slot, uint32_t out_auxc,
                         uint32_t out_state) {
    /* original: writes guest memory at the passed addresses */
    uint32_t base = HG_AUDIO_SLOT_TABLE + slot * HG_SLOT_STRIDE;
    bus->w32(bus->ctx, out_auxc, bus->r32(bus->ctx, base + HG_SLOT_AUXC));
    bus->w32(bus->ctx, out_state, bus->r32(bus->ctx, base + HG_SLOT_STATE));
}

/* ------------------------------------------------------------------ */
/* 0x002b5008 hg_audio_table_lookup4 - 2b5008..2b50e0                    */
/* Walks bank+0x3c table with 0xffff sentinel entries.                 */
/* ------------------------------------------------------------------ */
uint32_t hg_audio_table_lookup4(HGAudioBus *bus, uint32_t slot,
                                uint32_t index, uint32_t extra) {
    uint32_t base = HG_AUDIO_SLOT_TABLE + slot * HG_SLOT_STRIDE;
    uint32_t bank = bus->r32(bus->ctx, base + HG_SLOT_BANK2);
    uint32_t table;
    if (bank == 0) return 0;
    table = bus->r32(bus->ctx, bank + 0x3c);
    if (table == 0) return 0;
    if (bus->r16(bus->ctx, table) < index) return 0;
    {
        uint32_t entry = bus->r16(bus->ctx, U32(table + index * 2 + 2));
        uint32_t value;
        if (entry == 0xffff) return 0;
        value = entry >> 1;
        value = U32(value + extra);
        value = U32(value * 2 + table);
        if (bus->r16(bus->ctx, value) < extra) return 0;
        return U32(table + bus->r16(bus->ctx, U32(value + extra * 2 + 2)));
    }
}

/* ------------------------------------------------------------------ */
/* 0x002b50e0 hg_audio_table_lookup6 - 2b50e0..2b5178                    */
/* Walks bank+0x44 table (pointer must be even).                       */
/* ------------------------------------------------------------------ */
uint32_t hg_audio_table_lookup6(HGAudioBus *bus, uint32_t slot,
                                uint32_t index) {
    uint32_t base = HG_AUDIO_SLOT_TABLE + slot * HG_SLOT_STRIDE;
    uint32_t bank = bus->r32(bus->ctx, base + HG_SLOT_BANK2);
    uint32_t table;
    if (bank == 0) return 0;
    table = bus->r32(bus->ctx, bank + 0x44);
    if (table == 0) return 0;
    if (table & 1) return 0;
    if (bus->r16(bus->ctx, table) < index) return 0;
    return U32(table + bus->r16(bus->ctx, U32(table + index * 2 + 2)));
}

/* ------------------------------------------------------------------ */
/* 0x002b5178 hg_audio_table_lookup1 - 2b5178..2b5224                    */
/* Walks bank+0x30 table with 0xffff sentinel entries.                 */
/* ------------------------------------------------------------------ */
uint32_t hg_audio_table_lookup1(HGAudioBus *bus, uint32_t slot,
                                uint32_t index) {
    uint32_t base = HG_AUDIO_SLOT_TABLE + slot * HG_SLOT_STRIDE;
    uint32_t bank = bus->r32(bus->ctx, base + HG_SLOT_BANK2);
    uint32_t table;
    if (bank == 0) return 0;
    table = bus->r32(bus->ctx, bank + HG_BANK_PTR0);
    if (table == 0) return 0;
    if (table & 1) return 0;
    if (bus->r16(bus->ctx, table) < index) return 0;
    {
        uint32_t entry = bus->r16(bus->ctx, U32(table + index * 2 + 2));
        if (entry == 0xffff) return 0;
        return U32(table + entry);
    }
}

/* ------------------------------------------------------------------ */
/* 0x002b5228 hg_audio_null8 - 2b5228..2b523c                            */
/* ------------------------------------------------------------------ */
uint32_t hg_audio_null8(uint32_t value) {
    if (value == 0) return 0;
    return U32(value + 8);
}

/* ------------------------------------------------------------------ */
/* 0x002b5240 hg_audio_slot_range18 - 2b5240..2b5278                     */
/* *out_18 = slot+0x18; returns slot+0x1c.                             */
/* ------------------------------------------------------------------ */
void hg_audio_slot_range18(HGAudioBus *bus, uint32_t slot, uint32_t out_18,
                           uint32_t out_1c) {
    uint32_t base = U32(HG_AUDIO_SLOT_TABLE + slot * HG_SLOT_STRIDE + 0x10);
    bus->w32(bus->ctx, out_18, bus->r32(bus->ctx, base + 8));
    bus->w32(bus->ctx, out_1c, bus->r32(bus->ctx, base + 0x0c));
}

/* ------------------------------------------------------------------ */
/* 0x002b5278 hg_audio_slot_range20 - 2b5278..2b52bc                     */
/* *out_20 = slot+0x20; *out_24 = slot+0x24; returns slot+0x28.        */
/* ------------------------------------------------------------------ */
uint32_t hg_audio_slot_range20(HGAudioBus *bus, uint32_t slot,
                               uint32_t out_20, uint32_t out_24) {
    uint32_t base = U32(HG_AUDIO_SLOT_TABLE + slot * HG_SLOT_STRIDE + 0x20);
    bus->w32(bus->ctx, out_20, bus->r32(bus->ctx, base + 0));
    bus->w32(bus->ctx, out_24, bus->r32(bus->ctx, base + 4));
    return bus->r32(bus->ctx, base + 8);
}

/* ------------------------------------------------------------------ */
/* 0x002b52c0 hg_audio_slot_swap - 2b52c0..2b52ec                        */
/* Exchange the aux word; returns the previous value.                  */
/* ------------------------------------------------------------------ */
uint32_t hg_audio_slot_swap(HGAudioBus *bus, uint32_t slot, uint32_t value) {
    uint32_t address = HG_AUDIO_SLOT_AUX + slot * HG_SLOT_STRIDE;
    uint32_t previous = bus->r32(bus->ctx, address);
    bus->w32(bus->ctx, address, value);
    return previous;
}

/* ------------------------------------------------------------------ */
/* 0x002b52f0 hg_audio_slot_get - 2b52f0..2b5318                          */
/* ------------------------------------------------------------------ */
uint32_t hg_audio_slot_get(HGAudioBus *bus, uint32_t slot) {
    return bus->r32(bus->ctx, HG_AUDIO_SLOT_AUX + slot * HG_SLOT_STRIDE);
}

/* ------------------------------------------------------------------ */
/* 0x002b5318 hg_audio_bank_register_wrapper - 2b5318..2b53a0            */
/* Interrupts off; find free slot; register bank; interrupts on.       */
/* Returns the slot index or -1.                                       */
/* ------------------------------------------------------------------ */
uint32_t hg_audio_bank_register_wrapper(HGAudioBus *bus, uint32_t bank) {
    uint32_t lock = bus->call(bus->ctx, HG_X_INTS_OFF, 0, 0, 0, 0, 0);
    uint32_t slot = hg_audio_slot_find(bus);
    uint32_t result = hg_audio_bank_register(
        bus, HG_AUDIO_SLOT_TABLE + slot * HG_SLOT_STRIDE, bank);
    uint32_t slot_out = U32(-1);
    if (lock == 0) {
        if (result != U32(-1)) {
            slot_out = slot;
        }
    }
    if (lock != 0) {
        bus->call(bus->ctx, HG_X_INTS_ON, 0, 0, 0, 0, 0);
    }
    return slot_out;
}

/* ------------------------------------------------------------------ */
/* 0x002b53a0 hg_audio_slot_bank - 2b53a0..2b5424                         */
/* word(bank+0x04) of the slot's bank, or -1.                          */
/* ------------------------------------------------------------------ */
uint32_t hg_audio_slot_bank(HGAudioBus *bus, uint32_t slot) {
    if (!hg_audio_slot_active(bus, slot)) {
        return U32(-1);
    }
    {
        uint32_t base = HG_AUDIO_SLOT_TABLE + slot * HG_SLOT_STRIDE;
        uint32_t state = bus->r32(bus->ctx, base + HG_SLOT_STATE);
        if (state == 0) return U32(-1);
        return bus->r32(bus->ctx,
                        bus->r32(bus->ctx, base + HG_SLOT_BANK2) + 4);
    }
}

/* ------------------------------------------------------------------ */
/* 0x002b54e8 hg_audio_slot_clear - 2b54e8..2b55a8                       */
/* Zero the 11 words of a slot.                                        */
/* ------------------------------------------------------------------ */
void hg_audio_slot_clear(HGAudioBus *bus, uint32_t slot) {
    uint32_t base;
    uint32_t i;
    if (!hg_audio_slot_active(bus, slot)) {
        return;
    }
    base = HG_AUDIO_SLOT_TABLE + slot * HG_SLOT_STRIDE;
    for (i = 0; i < 11; i++) {
        bus->w32(bus->ctx, base + i * 4, 0);
    }
}

/* ------------------------------------------------------------------ */
/* 0x002b55a8 hg_audio_slots_init - 2b55a8..2b55e8                       */
/* ------------------------------------------------------------------ */
void hg_audio_slots_init(HGAudioBus *bus) {
    uint32_t slot;
    for (slot = 0; slot < 8; slot++) {
        hg_audio_slot_clear(bus, slot);
    }
}

/* ------------------------------------------------------------------ */
/* 0x002ae998 hg_audio_next_sequence - 2ae998..2ae9b8                    */
/* ------------------------------------------------------------------ */
uint32_t hg_audio_next_sequence(HGAudioBus *bus) {
    uint32_t value = bus->r32(bus->ctx, HG_AUDIO_SEQUENCE);
    bus->w32(bus->ctx, HG_AUDIO_SEQUENCE, U32(value + 1));
    return value;
}

/* ------------------------------------------------------------------ */
/* 0x002a9050 hg_audio_channel_init_a - 2a9050..2a9180                   */
/* ------------------------------------------------------------------ */
uint32_t hg_audio_channel_init_a(HGAudioBus *bus, uint32_t channel) {
    uint32_t base = HG_AUDIO_CHANNEL_BASE + channel * HG_CH_STRIDE;
    uint32_t i;
    bus->w8(bus->ctx, base + 0x58, 0x80);
    bus->w32(bus->ctx, base + 0x14, 1);
    bus->w32(bus->ctx, base + 0x30, 0x40);
    bus->w32(bus->ctx, base + 0x38, 0x1000);
    bus->w32(bus->ctx, base + 0x28, 0);
    bus->w32(bus->ctx, base + 0x2a0, 1);
    bus->w32(bus->ctx, base + 0x24, 0);
    bus->w32(bus->ctx, base + 0x20, 0);
    bus->w32(bus->ctx, base + 0x1c, 0);
    bus->w32(bus->ctx, base + 0x34, 0);
    bus->w32(bus->ctx, base + 0x54, 0);
    bus->w32(bus->ctx, base + 0x10, 0);
    bus->w32(bus->ctx, base + 0x2c, 0x7f);
    bus->w32(bus->ctx, base + 0x00, U32(-1));
    for (i = 0; i < HG_VOICE_COUNT; i++) {
        uint32_t voice = U32(base + HG_CH_VOICES + i * HG_VOICE_STRIDE);
        bus->w32(bus->ctx, voice + 0x00, channel);
        bus->w32(bus->ctx, voice + 0x04, 0);
        bus->w32(bus->ctx, voice + 0x2c, i == 9 ? 0 : 1);
        bus->w32(bus->ctx, voice + 0x08, 0xff);
        bus->w32(bus->ctx, voice + 0x0c, 0x40);
        bus->w32(bus->ctx, voice + 0x10, 0);
        bus->w32(bus->ctx, voice + 0x14, 0x7f);
        bus->w32(bus->ctx, voice + 0x20, 0x7f);
        bus->w32(bus->ctx, voice + 0x24, 0);
        bus->w32(bus->ctx, voice + 0x30, 0);
        bus->w32(bus->ctx, voice + 0x34, 0x40);
        bus->w8(bus->ctx, voice + 0x68, 0);
        bus->w8(bus->ctx, voice + 0x69, 0);
        bus->w8(bus->ctx, voice + 0x6a, 0);
        bus->w8(bus->ctx, voice + 0x6b, 0);
        bus->w32(bus->ctx, voice + 0x4c, 0x1000);
        bus->w32(bus->ctx, voice + 0x6c, 0);
        bus->w32(bus->ctx, voice + 0x70, 0);
        bus->w16(bus->ctx, voice + 0x60, 0);
        bus->w16(bus->ctx, voice + 0x62, 0);
        bus->w16(bus->ctx, voice + 0x64, 0);
        bus->w16(bus->ctx, voice + 0x66, 0);
        bus->w32(bus->ctx, voice + 0x50, 0);
        bus->w32(bus->ctx, voice + 0x54, 0);
        bus->w32(bus->ctx, voice + 0x58, 0);
        bus->w32(bus->ctx, voice + 0x5c, 0);
        bus->w32(bus->ctx, voice + 0x38, 0);
        bus->w32(bus->ctx, voice + 0x3c, 0);
        bus->w32(bus->ctx, voice + 0x48, 1);
        bus->w32(bus->ctx, voice + 0x44, 1);
        bus->w32(bus->ctx, voice + 0x28, i == 9 ? 1 : 0);
    }
    return 1; /* last iteration computes v0 = 1 - v1(i=15) = 1 */
}

/* ------------------------------------------------------------------ */
/* 0x002a9180 hg_audio_channel_init_b - 2a9180..2a9298                   */
/* ------------------------------------------------------------------ */
void hg_audio_channel_init_b(HGAudioBus *bus, uint32_t channel) {
    uint32_t base = HG_AUDIO_CHANNEL_BASE + channel * HG_CH_STRIDE;
    int32_t i;
    bus->w32(bus->ctx, base + 0x2c, 0x7f);
    bus->w32(bus->ctx, base + 0x00, U32(-1));
    bus->w32(bus->ctx, base + 0x38, 0x1000);
    bus->w32(bus->ctx, base + 0x28, 0);
    bus->w32(bus->ctx, base + 0x24, 0);
    bus->w32(bus->ctx, base + 0x20, 0);
    bus->w32(bus->ctx, base + 0x1c, 0);
    bus->w32(bus->ctx, base + 0x34, 0);
    bus->w32(bus->ctx, base + 0x54, 0);
    bus->w32(bus->ctx, base + 0x10, 0);
    bus->w32(bus->ctx, base + 0x2a0, 1);
    bus->w8(bus->ctx, base + 0x58, 0x80);
    bus->w32(bus->ctx, base + 0x14, 2);
    bus->w32(bus->ctx, base + 0x30, 0x40);
    for (i = HG_VOICE_COUNT - 1; i >= 0; i--) {
        uint32_t voice = U32(base + HG_CH_VOICES + i * HG_VOICE_STRIDE);
        bus->w32(bus->ctx, voice + 0x00, channel);
        bus->w32(bus->ctx, voice + 0x04, 0);
        bus->w32(bus->ctx, voice + 0x08, 0xff);
        bus->w32(bus->ctx, voice + 0x0c, 0x40);
        bus->w32(bus->ctx, voice + 0x10, 0);
        bus->w32(bus->ctx, voice + 0x14, 0x64);
        bus->w32(bus->ctx, voice + 0x20, 0x7f);
        bus->w32(bus->ctx, voice + 0x24, 0);
        bus->w32(bus->ctx, voice + 0x30, 0);
        bus->w32(bus->ctx, voice + 0x34, 0x40);
        bus->w8(bus->ctx, voice + 0x68, 0);
        bus->w8(bus->ctx, voice + 0x69, 0);
        bus->w8(bus->ctx, voice + 0x6a, 0);
        bus->w8(bus->ctx, voice + 0x6b, 0);
        bus->w32(bus->ctx, voice + 0x4c, 0x1000);
        bus->w32(bus->ctx, voice + 0x6c, 0);
        bus->w32(bus->ctx, voice + 0x70, 0);
        bus->w16(bus->ctx, voice + 0x60, 0);
        bus->w16(bus->ctx, voice + 0x62, 0);
        bus->w16(bus->ctx, voice + 0x64, 0);
        bus->w16(bus->ctx, voice + 0x66, 0);
        bus->w32(bus->ctx, voice + 0x38, 0);
        bus->w32(bus->ctx, voice + 0x3c, 0);
        bus->w32(bus->ctx, voice + 0x50, 0);
        bus->w32(bus->ctx, voice + 0x54, 0);
        bus->w32(bus->ctx, voice + 0x58, 0);
        bus->w32(bus->ctx, voice + 0x5c, 0);
        bus->w32(bus->ctx, voice + 0x48, 1);
        bus->w32(bus->ctx, voice + 0x44, 1);
    }
}

/* ------------------------------------------------------------------ */
/* 0x002a94b8 hg_audio_channel_command - 2a94b8..2a9538                  */
/* Queue a command byte into the channel's 512-byte IOP command ring.  */
/* ------------------------------------------------------------------ */
void hg_audio_channel_command(HGAudioBus *bus, uint32_t channel,
                              uint32_t command) {
    uint32_t lock = bus->call(bus->ctx, HG_X_INTS_OFF, 0, 0, 0, 0, 0);
    uint32_t base = HG_AUDIO_CHANNEL_QUEUE + channel * HG_CH_STRIDE;
    uint32_t index = bus->r32(bus->ctx, base + HG_CH_CMD_RING);
    uint32_t next;
    /* original: the ring occupies base+0x00..+0x1ff; the write index
     * word sits at base+0x200 */
    bus->w8(bus->ctx, U32(base + index), (uint8_t)command);
    next = U32((index + 1) & 0x1ff);
    bus->w32(bus->ctx, base + HG_CH_CMD_RING, next);
    if (lock != 0) {
        bus->call(bus->ctx, HG_X_INTS_ON, 0, 0, 0, 0, 0);
    }
}

/* ------------------------------------------------------------------ */
/* 0x002a99b8 hg_audio_open_channel_a - 2a99b8..2a9b60                   */
/* ------------------------------------------------------------------ */
uint32_t hg_audio_open_channel_a(HGAudioBus *bus, uint32_t slot,
                                 uint32_t param) {
    uint32_t cursor;
    uint32_t channel = U32(-1);
    uint32_t i;
    if (!hg_audio_slot_active(bus, slot)) {
        return U32(-1);
    }
    {
        uint32_t lock = bus->call(bus->ctx, HG_X_INTS_OFF, 0, 0, 0, 0, 0);
        cursor = bus->r32(bus->ctx, HG_AUDIO_VOICE_CURSOR);
        for (i = 0; i < 32; i++) {
            uint32_t index = U32((cursor + i) & 31);
            uint32_t base = HG_AUDIO_CHANNEL_BASE + index * HG_CH_STRIDE;
            if (bus->r32(bus->ctx, base + 0) == U32(-1)) {
                uint32_t seq;
                bus->w32(bus->ctx, HG_AUDIO_VOICE_CURSOR, U32(cursor + i + 1));
                seq = hg_audio_next_sequence(bus);
                bus->w32(bus->ctx, base + 4, seq);
                channel = index;
                break;
            }
        }
        if (lock != 0) {
            bus->call(bus->ctx, HG_X_INTS_ON, 0, 0, 0, 0, 0);
        }
    }
    if (channel == U32(-1)) {
        return U32(-1);
    }
    {
        uint32_t base = HG_AUDIO_CHANNEL_BASE + channel * HG_CH_STRIDE;
        uint32_t t20, t24, t28;
        bus->call(bus->ctx, 0x002a9050, channel, 0, 0, 0, 0);
        hg_audio_slot_state(bus, slot, base + 0x0c, base + 0x08);
        bus->w32(bus->ctx, base + 0x10, param);
        t28 = hg_audio_slot_range20(bus, slot, base + 0x48, base + 0x4c);
        t20 = bus->r32(bus->ctx, base + 0x48);
        t24 = bus->r32(bus->ctx, base + 0x4c);
        t28 = hg_audio_slot_range20(bus, slot, base + 0x48, base + 0x4c);
        bus->w32(bus->ctx, base + 0x00, slot);
        bus->w32(bus->ctx, base + 0x48, t20);
        bus->w32(bus->ctx, base + 0x4c, t24);
        bus->w32(bus->ctx, base + 0x50, t28);
        if ((int32_t)t20 < 0) {
            bus->w32(bus->ctx, base + 0x4c, 0x003cce38);
        }
        hg_audio_slot_range18(bus, slot, base + 0x40, base + 0x44);
    }
    return channel;
}

/* ------------------------------------------------------------------ */
/* 0x002a9b68 hg_audio_open_channel_b - 2a9b68..2a9d18                   */
/* ------------------------------------------------------------------ */
uint32_t hg_audio_open_channel_b(HGAudioBus *bus, uint32_t slot,
                                 uint32_t param) {
    uint32_t cursor;
    uint32_t channel = U32(-1);
    uint32_t i;
    if (!hg_audio_slot_active(bus, slot)) {
        return U32(-1);
    }
    {
        uint32_t lock = bus->call(bus->ctx, HG_X_INTS_OFF, 0, 0, 0, 0, 0);
        cursor = bus->r32(bus->ctx, HG_AUDIO_VOICE_CURSOR);
        for (i = 0; i < 32; i++) {
            uint32_t index = U32((cursor + i) & 31);
            uint32_t base = HG_AUDIO_CHANNEL_BASE + index * HG_CH_STRIDE;
            if (bus->r32(bus->ctx, base + 0) == U32(-1)) {
                uint32_t seq;
                bus->w32(bus->ctx, HG_AUDIO_VOICE_CURSOR, U32(cursor + i + 1));
                seq = hg_audio_next_sequence(bus);
                bus->w32(bus->ctx, base + 4, seq);
                channel = index;
                break;
            }
        }
        if (lock != 0) {
            bus->call(bus->ctx, HG_X_INTS_ON, 0, 0, 0, 0, 0);
        }
    }
    if (channel == U32(-1)) {
        return U32(-1);
    }
    {
        uint32_t base = HG_AUDIO_CHANNEL_BASE + channel * HG_CH_STRIDE;
        uint32_t t20, t24, t28;
        bus->call(bus->ctx, 0x002a9180, channel, 0, 0, 0, 0);
        hg_audio_slot_state(bus, slot, base + 0x0c, base + 0x08);
        bus->w32(bus->ctx, base + 0x10, param);
        hg_audio_slot_range20(bus, slot, base + 0x48, base + 0x4c);
        t20 = bus->r32(bus->ctx, base + 0x48);
        t24 = bus->r32(bus->ctx, base + 0x4c);
        t28 = hg_audio_slot_range20(bus, slot, base + 0x48, base + 0x4c);
        bus->w32(bus->ctx, base + 0x00, slot);
        bus->w32(bus->ctx, base + 0x48, t20);
        bus->w32(bus->ctx, base + 0x4c, t24);
        bus->w32(bus->ctx, base + 0x50, t28);
        if ((int32_t)t20 < 0) {
            bus->w32(bus->ctx, base + 0x4c, 0x003cce38);
        }
        hg_audio_slot_range18(bus, slot, base + 0x40, base + 0x44);
    }
    return channel;
}

/* ------------------------------------------------------------------ */
/* 0x002ad478 hg_audio_play - 2ad478..2ad690                              */
/* ------------------------------------------------------------------ */
uint32_t hg_audio_play(HGAudioBus *bus, uint32_t slot, uint32_t param1,
                       uint32_t param2, uint32_t param3, uint32_t t0) {
    uint32_t lock = bus->call(bus->ctx, HG_X_INTS_OFF, 0, 0, 0, 0, 0);
    uint32_t cursor;
    uint32_t index = U32(-1);
    uint32_t i;
    if (!hg_audio_slot_active(bus, slot)) {
        if (lock != 0) {
            bus->call(bus->ctx, HG_X_INTS_ON, 0, 0, 0, 0, 0);
        }
        return U32(-4);
    }
    cursor = bus->r32(bus->ctx, HG_AUDIO_COMMAND_CURSOR);
    for (i = 0; i < 32; i++) {
        uint32_t ring = U32((cursor + i) & 31);
        uint32_t base = HG_AUDIO_COMMAND_BASE + ring * HG_CMD_STRIDE;
        if (bus->r32(bus->ctx, base + HG_CMD_STATE) == 0) {
            bus->w32(bus->ctx, HG_AUDIO_COMMAND_CURSOR, U32(cursor + i + 1));
            bus->w32(bus->ctx, base + HG_CMD_SEQ, cursor + i);
            index = ring;
            break;
        }
    }
    if (index == U32(-1)) {
        if (lock != 0) {
            bus->call(bus->ctx, HG_X_INTS_ON, 0, 0, 0, 0, 0);
        }
        return U32(-3);
    }
    {
        uint32_t base = HG_AUDIO_COMMAND_BASE + index * HG_CMD_STRIDE;
        uint32_t sample;
        uint32_t channel;
        uint32_t tick;
        uint64_t time64;
        if (param2 & 0x00010000) {
            bus->call(bus->ctx, HG_X_FLAG_SVC, index, 0x200, 1, 0, 0);
            param2 = U32(param2 & 0x0000ffff);
        }
        bus->w32(bus->ctx, base + HG_CMD_SLOT, slot);
        sample = hg_audio_table_lookup4(bus, slot, param1, param2);
        bus->w32(bus->ctx, base + HG_CMD_SAMPLE, sample);
        if (sample == 0) {
            if (lock != 0) {
                bus->call(bus->ctx, HG_X_INTS_ON, 0, 0, 0, 0, 0);
            }
            return U32(-1);
        }
        channel = hg_audio_open_channel_b(bus, slot, t0);
        if (channel == U32(-1)) {
            if (lock != 0) {
                bus->call(bus->ctx, HG_X_INTS_ON, 0, 0, 0, 0, 0);
            }
            return U32(-2);
        }
        bus->w32(bus->ctx, base + HG_CMD_CHANNEL, channel);
        bus->w32(bus->ctx, base + HG_CMD_FIELD28, 0);
        bus->w32(bus->ctx, base + HG_CMD_FIELD2C, 0);
        bus->w32(bus->ctx, base + HG_CMD_FIELD30, 0);
        bus->w32(bus->ctx, base + HG_CMD_FIELD34, 0);
        bus->w32(bus->ctx, base + HG_CMD_FIELD3C, sample);
        tick = bus->call(bus->ctx, HG_X_GLOBAL_TICK, 0, 0, 0, 0, 0);
        {
            /* 1165d0/116320 (external seam): time math with a 64-bit
             * constant (a0 = 0x80fc dsll32 15). The host model returns
             * the low word; the high word is the fixed original
             * constant's low half: 0x407e0000. */
            uint32_t t = bus->call(bus->ctx, HG_X_TIME_A, tick, 0, 0, 0, 0);
            uint32_t lo = bus->call(bus->ctx, HG_X_TIME_B, 0, 0x407e0000,
                                    t, 0, 0);
            time64 = ((uint64_t)0x407e0000u << 32) | lo;
        }
        bus->w32(bus->ctx, base + HG_CMD_TIME, (uint32_t)time64);
        bus->w32(bus->ctx, base + HG_CMD_TIME + 4, (uint32_t)(time64 >> 32));
        bus->w32(bus->ctx, base + HG_CMD_FIELD38, 0);
        bus->w32(bus->ctx, base + HG_CMD_STATE, 1);
        bus->w32(bus->ctx, base + HG_CMD_PARAM1, param1);
        bus->w32(bus->ctx, base + HG_CMD_PARAM2, param2);
        bus->w32(bus->ctx, base + HG_CMD_UNK4, 0);
        if (param3 != 0) {
            bus->call(bus->ctx, HG_X_PLAY_EXTRA, slot, param1, param2, param3, 0);
        }
    }
    if (lock != 0) {
        bus->call(bus->ctx, HG_X_INTS_ON, 0, 0, 0, 0, 0);
    }
    return index;
}

/* ------------------------------------------------------------------ */
/* 0x002ad748 hg_audio_cancel - 2ad748..2ad800                            */
/* ------------------------------------------------------------------ */
uint32_t hg_audio_cancel(HGAudioBus *bus, uint32_t index) {
    uint32_t base;
    uint32_t lock;
    if (index >= 32) {
        return 0;
    }
    base = HG_AUDIO_COMMAND_BASE + index * HG_CMD_STRIDE;
    if ((int32_t)bus->r32(bus->ctx, base + HG_CMD_STATE) <= 0) {
        return 0;
    }
    lock = bus->call(bus->ctx, HG_X_INTS_OFF, 0, 0, 0, 0, 0);
    bus->w32(bus->ctx, base + HG_CMD_SLOT, U32(-1));
    bus->w32(bus->ctx, base + HG_CMD_STATE, 2);
    {
        uint32_t channel = bus->r32(bus->ctx, base + HG_CMD_CHANNEL);
        hg_audio_channel_command(bus, channel, 0xff);
        hg_audio_channel_command(bus, channel, 0x41);
        hg_audio_channel_command(bus, channel, 0xf7);
    }
    if (lock != 0) {
        bus->call(bus->ctx, HG_X_INTS_ON, 0, 0, 0, 0, 0);
    }
    return 0;
}

/* ------------------------------------------------------------------ */
/* 0x002adcf0 hg_audio_bank_bind - 2adcf0..2add90                          */
/* ------------------------------------------------------------------ */
uint32_t hg_audio_bank_bind(HGAudioBus *bus, uint32_t entry,
                            uint32_t volume) {
    int32_t i;
    if (!hg_audio_slot_active(bus, entry)) {
        return U32(-1);
    }
    hg_audio_slot_swap(bus, entry, volume);
    for (i = 31; i >= 0; i--) {
        uint32_t base = HG_AUDIO_COMMAND_BASE + i * HG_CMD_STRIDE;
        if (bus->r32(bus->ctx, base + HG_CMD_SLOT) == entry) {
            bus->call(bus->ctx, 0x002a9d70,
                      bus->r32(bus->ctx, base + HG_CMD_CHANNEL),
                      2, volume, 0, 0);
        }
    }
    return volume;
}
