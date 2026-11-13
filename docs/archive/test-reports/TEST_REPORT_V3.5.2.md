# Test report — ZX Code v3.5.2

## Environment

- VM Python: 3.11.16 (project `.venv`)
- Docker CLI: 29.9.0
- Docker daemon API: inaccessible to `agentuser` (`permission denied` on `/var/run/docker.sock`)
- Podman: not installed

## Automated verification

| Check | Command / method | Result |
|---|---|---|
| Targeted persistence + checkpoint + journal tests | `.venv/bin/python -m pytest -q tests/test_persistence_durability.py tests/test_checkpoint.py tests/test_mutation_journal.py --disable-warnings --maxfail=1` | 44 passed |
| Full regression suite | `.venv/bin/python -m pytest -q --disable-warnings --maxfail=1` | 358 passed, 11 warnings |
| Bytecode compilation | `.venv/bin/python -m compileall -q .` | Passed |
| Core import smoke | import 9 core/entrypoint/persistence modules | Passed |
| CLI version smoke | `.venv/bin/python main.py --version` | `ZX Code v3.5.2` |

## New regression coverage

- Injected archive write failure: no final `.zip` checkpoint is published and the temporary file is removed.
- Successful checkpoint publication: resulting ZIP is readable and contains expected content.
- Checkpoint publication: containing directory receives `fsync` after atomic rename.
- Journal blob publication: blob directory receives `fsync` after content-addressed rename.
- Journal directory-sync failure: error is surfaced; any already-renamed blob is treated as uncertain and is not silently reported as durable.

## Not verified / limitations

- Real Docker/Podman end-to-end verification could not be run because the VM user cannot access the Docker daemon socket and Podman is absent. No container integration pass is claimed.
- Fault injection covers the newly hardened checkpoint and journal-blob publication boundaries, not every `fsync`/rename in manifest, task-state, rollback, or all storage layers.
- Tests inject exceptions; they do not simulate physical power loss, kernel panic, storage-controller cache loss, or hardware failure.
- Temporary checkpoint files can remain after an uncatchable process kill; they are not published as trusted `.zip` snapshots.
- Directory `fsync` behavior depends on filesystem/platform support. The implementation uses POSIX `O_DIRECTORY` where available.
- External processes that ignore ZX Code's advisory workspace lock can still modify files concurrently.
- No exactly-once semantics are claimed for arbitrary external side effects.

## Conclusion

The full available Python suite and local smoke checks passed. The release improves atomic checkpoint publication and journal blob directory-entry durability. Production readiness is not established; container E2E and broader crash/power-loss testing remain open.

## Release artifact verification

- ZIP integrity: `ZipFile.testzip()` returned no corrupt members.
- Patch application: `git apply --check` and `git apply` succeeded against a clean v3.5.1 source copy.
- Source equivalence: patched tree and ZIP-extracted tree each matched the v3.5.2 workspace by SHA-256 for all 172 release files.
- The v3.5.1 ZIP and patch checksums remained unchanged during this release build.
