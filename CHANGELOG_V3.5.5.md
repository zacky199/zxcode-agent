# ZX Code v3.5.5 — Fault Injection & Crash Consistency

- Added subprocess process-kill tests at journal, rollback, task-state and checkpoint publication boundaries.
- Added durable write-ahead rollback intent before restoring any workspace path.
- Rollback now refuses to modify workspace files if its intent cannot be persisted.
- Recovery now treats interrupted rollback as unresolved and only recognizes explicit terminal operation states.
- Verified task record remains available if process termination occurs before active-task pointer replacement.
- Verified checkpoint atomic publication behavior immediately before and after os.replace.
- Previous v3.5.4 release artifacts and source directory remain untouched.
