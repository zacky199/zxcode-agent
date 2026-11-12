# Technical Debt — ZX Code Agent

## P0 — harus diselesaikan sebelum klaim production-ready

- [ ] Implementasikan resume controller yang memvalidasi workspace identity, state version, status task, dan checkpoint sebelum melanjutkan pekerjaan. Jangan otomatis melanjutkan task yang state-nya korup atau workspace-nya berubah.
- [x] Simpan metadata per task secara atomik dengan record terpisah, active pointer, validasi task_id, dan daftar task.
- [ ] Simpan execution cursor dan hasil step secara atomik; desain retensi beberapa task sudah dimulai, tetapi belum ada resume controller.
- [ ] Tambahkan benchmark task nyata: bug fix lintas file, implementasi fitur, refactor, test repair, dan recovery setelah proses dihentikan. Laporkan pass rate dan regresi.
- [ ] Audit trust boundary Docker daemon, grup `docker`, mount, plugin, MCP, dan command approval. Docker group setara akses root pada host.
- [ ] Tambahkan crash-consistency tests untuk task state, mutation journal, dan checkpoint.

## P1 — reliability dan kualitas hasil

- [x] Validasi dependency DAG, deteksi cycle, topological ordering, ready-node discovery, dan blocked-descendant propagation.
- [ ] Dispatch setiap node DAG sebagai unit eksekusi tersendiri dengan persistence per node; saat ini hanya prompt plan sequential yang mengikuti dependency order.
- [ ] Tambahkan repair diagnosis yang terstruktur: error classification, root-cause hypothesis, proposed patch, dan verification evidence.
- [ ] Izinkan repair berdasarkan finding Reviewer/Security Auditor hanya setelah scope dan policy dicek; jangan auto-fix temuan yang membutuhkan keputusan produk/security.
- [ ] Tambahkan diff-aware context retrieval dan batas token per file/symbol.
- [ ] Tambahkan integration tests untuk beberapa bahasa/runtime, termasuk proyek tanpa test suite.
- [ ] Uji rollback saat file diubah eksternal bersamaan dengan agent; konflik harus fail-safe, bukan menimpa perubahan pengguna.

## P2 — maintainability dan operasional

- [ ] Structured tracing dengan task_id, step_id, tool_call_id, provider, latency, token usage, dan outcome. Jangan log secret atau full environment.
- [ ] Provider routing dengan timeout, circuit breaker, retry classification, dan cost budget yang terukur.
- [ ] Pin digest image dasar dan buat SBOM serta proses vulnerability scanning.
- [ ] Tambahkan benchmark context quality dan semantic retrieval.
- [ ] Dokumentasikan kontrak event UI dan schema persistence dengan versioning/migration.

## Aturan penerimaan

Satu item hanya boleh ditandai selesai jika ada implementasi, test yang gagal sebelum perbaikan (bila relevan), test lulus setelah perbaikan, dokumentasi, dan hasil verifikasi yang dapat diulang.

## v3.5.2 follow-up

- [x] Publish checkpoint ZIPs atomically and sync the containing directory on supported POSIX filesystems.
- [x] Sync the journal blob directory after content-addressed rename.
- [ ] Extend deterministic fault injection to every task-state, journal-manifest, rollback, and checkpoint `fsync`/rename boundary.
- [ ] Run real container verification E2E with a failing command and read-only mount once VM Docker daemon access is granted (or a supported rootless Podman runtime is installed).
- [ ] Add safe stale temporary-checkpoint discovery/cleanup with ownership and age checks; do not delete uncertain files automatically.
