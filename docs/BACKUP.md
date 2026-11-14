# Private recovery and repeat procedures

The public repository contains reconstructed C, tools, synthetic fixtures and
bounded instruction/measurement evidence. Raw Ghidra exports, databases, emulator
captures and research logs belong in ignored `local/` and private recovery storage.
Never send these through GitHub Actions. Original media and bulk extracted inputs
are inventory-only; an owner must recover those separately from lawful originals.

## Prepare and inspect

Run from the repository root with Python 3.10+ and Git. Linux `/proc` is required
for database-open checks. Close Ghidra normally, including headless jobs. Do not
copy an active project or stop an unrelated process to make backup proceed.

Copy `config/backup.example.json` to `local/backup-config.json`. Select existing
source directories and the verified historical recovery directory; remove an
optional source only after recording its omission. Add original image/BIOS paths
and extraction directories to `inventory_only`. This hashes them without adding
their contents to the archive. Keep private IDs, paths and authentication out of
tracked configuration. Configuration paths are resolved relative to `repo`.

The legacy M3 recovery validator checks all declared files, group counts, sizes
and hashes, and verifies its Git bundle in an empty repository. Historical bundle
and source-archive hashes were not in that old manifest; the new snapshot hashes
both. Existing recovery evidence is preserved rather than regenerated.

```sh
python -B tools/hg_backup.py create --config local/backup-config.json --dry-run
python -B tools/hg_backup.py create --config local/backup-config.json
```

Dry-run reads and hashes sources but creates no snapshot or remote object. Check
its exclusions before creating. A missing source, legacy mismatch, lock, open
database, unreadable possible JVM or changing source fails the operation.

Snapshots have a UTC timestamp plus random suffix and exclusively reserved paths.
A failed snapshot retains `.incomplete`; do not upload it. A completed snapshot has
`manifest.json`, `snapshot.tar.gz` and an inspectable `data/` staging copy. Nothing
is overwritten, synchronized, pruned or scheduled. Space must accommodate staging,
archive and downloaded verification copies. Creating another snapshot leaves all
previous snapshots intact.

The manifest records regular-file sizes/SHA-256, exclusions, inventory-only media,
source consistency checks, Git HEAD/refs/status, totals and archive size/SHA-256.
The archive contains the full `git bundle --all` history and a public working-tree
copy, including dirty/new files. Thus the manifest distinguishes committed HEAD
from uncommitted source. Commit first when a backup of an exact release is needed.

## Upload to the existing private Drive folder

Use one existing recovery root, verified by ID and parent. Confirm owner-only
permissions before uploading. Create a **new snapshot child** named by the local
snapshot ID; upload only `snapshot.tar.gz` and `manifest.json`. Never replace a
previous snapshot or recreate the recovery root.

Two supported transport procedures:

1. With the connected Google Drive app, read root metadata and a complete listing;
   record earlier snapshot IDs. Create the unique child with `create_folder`, then
   `upload_file` each raw file using that child as `parent_folder_id`. Read back
   file metadata including parent, size, MIME type and checksum. `fetch` with
   `download_raw_file=true, include_base64=false` downloads each file without
   conversion. Materialize those authenticated file references into a fresh local
   directory. Never treat upload success or a folder listing as hash verification.
2. With an owner-configured native rclone Drive remote, set `rclone.remote` and
   `rclone.folder_id` in the ignored config. If authentication is needed, use the
   owner's normal browser login via `rclone config`; never paste tokens into chat.
   The tool refuses other backend types and existing snapshot directories, uses
   additive `copyto --immutable`, then downloads both remote files into a fresh
   retained directory. It compares the downloaded manifest with the trusted local
   manifest and verifies every archived file. Earlier directory IDs/names must
   remain present. Duplicate remote filenames fail verification.

```sh
python -B tools/hg_backup.py upload --config local/backup-config.json --snapshot local/backups/SNAPSHOT_ID
python -B tools/hg_backup.py verify-remote --config local/backup-config.json --snapshot local/backups/SNAPSHOT_ID
```

The connector procedure was used for this integration; rclone was absent on the
host. Its optional transport requires an owner-configured installation and has no
live-service test in CI. See [official Drive setup](https://rclone.org/drive/)
and [copyto behavior](https://rclone.org/commands/rclone_copyto/).

Google Drive raw-file metadata and a new download through its API establish
storage independent of the local staging copy. A local/mounted path alone does
not establish this. Record remote IDs/URLs, parents, sizes/checksums, downloaded
hashes and verification results privately. Compare earlier remote snapshots by ID
and checksum; do not change sharing or perform retention cleanup.

## Verify and restore

Keep a trusted manifest SHA-256 outside the snapshot (the owner's verification
receipt). Compare it against the newly downloaded manifest before verification.
The tool's hashes detect accidental corruption; they are not signatures and do
not defend against replacement of both the archive and trusted manifest.

```sh
sha256sum local/download-SNAPSHOT/manifest.json
python -B tools/hg_backup.py verify --manifest local/download-SNAPSHOT/manifest.json --archive local/download-SNAPSHOT/snapshot.tar.gz
python -B tools/hg_backup.py restore --manifest local/download-SNAPSHOT/manifest.json --archive local/download-SNAPSHOT/snapshot.tar.gz --destination local/restore-SNAPSHOT
```

The restore destination must not exist. Verification checks the entire compressed
archive and every member before creating it. Restore rejects path traversal,
links, special files, duplicate/extra/missing members and mismatched sizes/hashes.
It creates regular private files exclusively. Add repeated `--member PATH` options
for representative restoration; omitted options restore all included files.
Restore a `.gpr` **and its complete matching `.rep` directory** before opening a
Ghidra project; one database file alone is only a representative byte check.

Verify recovered Git history independently:

```sh
git clone local/restore-SNAPSHOT/git/history.bundle local/recovered-git-SNAPSHOT
git -C local/recovered-git-SNAPSHOT fsck --full
git -C local/recovered-git-SNAPSHOT log --all --oneline
```

Do not point a recovered database at the active project or overwrite a current
checkout. BIOS links are excluded, never followed or restored automatically.
Reconnect a lawful owner-local BIOS only in a separate emulator profile if needed.

## Publication audit and limitations

```sh
python -B tools/hg_publication_audit.py --base origin/main --head HEAD --output local/publication-audit-UNIQUE.json
python -B -m unittest discover -s tests -v
git diff --check
```

The audit examines every unpublished commit tree and object, including files
removed before HEAD. It flags private/artifact paths, binary content, known secret
patterns and personal absolute paths without printing secret values. Review
source, provenance, bounded instruction evidence and summarized captures as well;
heuristics alone cannot establish redistributability or detect every secret.
An unsafe historical commit blocks pushing the branch. Do not erase it or rewrite
history to bypass review.

Database safety combines lock/process/open-file checks and stable pre/post source
hashes. Some privilege-separated non-Java desktop processes can deny file-handle
inspection; these are recorded. This is a closed-project copy, not a filesystem
freeze or application-level transaction. Unreadable possible JVMs fail closed.
Disk-full, permission and source-change failures preserve partial evidence and
report failure. No successful return is claimed for missing/mismatched files.

Snapshots are unencrypted within the owner's private Drive account and inherit
its access. Account loss, malicious replacement, legal provenance and long-term
retention remain owner concerns. Full Ghidra application opening and a gameplay
replay are separate from archive/database byte recovery; neither is implied by a
hash check. No backup frequency has been selected and no scheduler is installed.
