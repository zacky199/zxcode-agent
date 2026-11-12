# ZX Code v3.5.2 — Crash consistency hardening

## Changed

- Checkpoint ZIPs are now built under a private temporary name in the checkpoint directory and published only after archive completion, file `fsync`, and atomic rename.
- The checkpoint directory is `fsync`ed after publication so the final directory entry is included in the durability sequence on supported POSIX filesystems.
- Journal preimage blobs now `fsync` the blob directory after content-addressed rename.
- Added deterministic regression tests for interrupted ZIP creation, checkpoint directory sync, journal blob directory sync, and directory-sync failure handling.

## Safety notes

- A failure after journal blob rename but before directory sync is surfaced as an error; the blob may exist, and callers must treat the operation as uncertain rather than retrying blindly.
- Temporary checkpoint files may remain after an uncatchable process kill; they are not trusted snapshots and are not published with the final `.zip` name.
- No exactly-once guarantee is made for external side effects.
