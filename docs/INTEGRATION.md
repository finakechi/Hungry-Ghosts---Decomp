# M1–M3 GitHub and private recovery integration — 2026-11-14

This integration preserves the existing repository and milestone history. Original
media, active databases, unrelated applications and the historical recovery
snapshot are unchanged. Publication was explicitly authorized for the reviewed
development branch and a PR to `main`; merging remains outside this task.

## Milestone history and research scope

| Milestone | Existing commit | Established result |
|---|---|---|
| M1 | `d67d8ef1aa06b347e101c5ce3af71b811f368351` | Verified retail identities; four connected disc-read functions; bounded byte/model checks |
| M2 implementation | `3ec079fc3df953f67462682f4a1e25565464475a` | Bootstrap scheduling/worker/PACK loading, handle publication and AREA helper reconstruction |
| M2 handoff | `f59d3659948eedc067ba1c33cf994c366b24a212` | Completion evidence and unsaved-work shutdown gate |
| M3 | `b33cc7978c643b04457c4e0dc4be06cf89faf87e` | Evidence completion/freshness safeguards and connected resource rebasing |

Combined coverage remains 16 functions / 1,020 instructions / 4,080 bytes.
Original-instruction evidence is bounded research evidence, not a distributed
executable. No original compiler match, complete asset fixup, general PACK loader,
AREA execution trace, gameplay equivalence or final port objective is claimed.

## Verification performed for integration

| Command / check | Result |
|---|---|
| `python -B -m unittest discover -s tests -v` | 80 passed, no failures/skips; includes 16 new backup/audit tests |
| C11 synthetic integration with ASan/UBSan (DEVELOPMENT command) | Passed |
| Shared byte verifier on M1 / M2 records | 283 / 684 instruction rows verified, no unknown rows |
| Shared structure validator on both records | Passed; structure is separate from byte/semantic verification |
| `verify_disc_read.py --elf "$HG_ELF" --output FRESH` | 1,448 comparisons passed |
| `verify_resource_loader.py --elf "$HG_ELF" --ps2dinfo "$HG_PS2DINFO" --output FRESH` | 970 randomized invocations and 12 sequences passed |
| `verify_resource_rebase.py --elf "$HG_ELF" --exports SELECTED --ps2dinfo "$HG_PS2DINFO" --output FRESH` | 256 comparisons and 53 original-byte rows passed |
| Legacy recovery validation | Five groups / 318 declared regular files validated; bundle verified independently in an empty repository |
| Media inventory | Three original images, original BIOS and three extracted ELFs separately hashed; contents excluded |

Fresh receipts/logs are in ignored `local/integration/`; existing `evidence/m1*`,
`evidence/m2/` and `evidence/m3/` are preserved historical records. Adding backup
tools changes the broad source binding, so historical bindings must not be called
current without rechecking. Research C/evidence semantics are unchanged.
Historical negative-control detections and runtime captures were inspected;
no fresh runtime or gameplay observation is claimed for this integration.

## Privacy and recovery boundaries

The initial audit scanned all four unpublished commits, 164 objects / 123 blobs,
including historical versions. It found no binary payload, known credential,
private absolute path or prohibited private file. Content review covers the
reconstructed source, bounded assembly/byte evidence, summary captures, manifests,
provenance and CI. The reusable audit is rerun against the final proposed commit
before push. Findings block publication; no history is discarded or rewritten.

Excluded from GitHub: original/patched images, BIOS, extracted executables/assets,
Ghidra databases and raw pseudocode, raw captures, private logs/configuration,
Drive identifiers/authentication and private recovery archives. CI uses only
synthetic fixtures and does not upload artifacts or use Google credentials.
No license selection is made on the owner's behalf; external source-reuse
boundaries remain documented in [sources.md](sources.md).

Private recovery packages include all Git refs/unpublished history, public
working-tree files, validated historical recovery data, closed current Ghidra
projects, exports, raw evidence/captures and research logs. Symlinks, BIOS paths,
media and extracted inputs are excluded and explicitly recorded. Closed-project
safety uses process/lock/open-file checks plus stable pre/post hashes; inaccessible
non-Java desktop processes are recorded as a limitation. No active database was
modified. Hash/restore checks do not claim a Ghidra application-opening test.

[BACKUP.md](BACKUP.md) documents dry-run, repeat upload, full verification and
isolated restore. Private manifests include sizes, SHA-256, omitted paths and
source identity. No scheduler, destructive synchronization or retention cleanup
is installed. Remote verification requires metadata and downloading the actual
Drive bytes, followed by complete archive/member hashing and representative
restoration. Transfer exit status alone is not accepted as proof.

## Continuation

Continue with real fixup `00137ed0`, general PACK loading `00130900` / `00131420`,
and an observed AREA transition with PC-correlated traces. External kernel/CDVD,
remaining codecs and overlay activation remain dependencies. The unrelated
unsaved-editor gate is historical context and no shutdown is attempted here.

## Independently verified private recovery receipt

The first snapshot was uploaded to a new child of the existing owner-only Drive
root using the connected app; no replacement root or authentication was needed.
Drive readback confirms raw stored files with the expected parent and sizes.
Fresh API downloads matched the trusted local manifest and the following hashes:

| Item | Verified value |
|---|---|
| Archived regular files | 815 |
| Uncompressed bytes | 359,144,294 |
| Compressed archive bytes | 85,118,303 |
| Archive SHA-256 | `00a063224257ccc2e8306df251a1f98142169cba987b6300965b3d2f0c7b7c6d` |
| Manifest SHA-256 | `32435cea3f369cfa0e7009abd1df324b9a1185d9ff79bce5109904742764da10` |
| Isolated restoration | All 815 files restored and rehashed |
| Git recovery | Bundle cloned, `git fsck --full` passed; HEAD equals M3 `b33cc79` and M1/M2 branches are present |

This first snapshot includes the uncommitted integration working tree as it
existed at packaging time. A later snapshot captures the committed integration;
its private transport and verification receipt remains in ignored
`local/integration/`. Private Drive object IDs/URLs and absolute source paths are
kept there, never in public configuration or documentation.

The historical recovery snapshot's generated BIOS state/cache files and symlinks
are validated in place but omitted from the new payload. Its old manifest is
retained as provenance; use the **new** manifest to verify the filtered recovery
copy. Current and historical Ghidra `.gpr`/`.rep` regular files are included whole.
No database-open/application or gameplay-replay test is claimed.
