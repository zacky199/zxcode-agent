# ZX Code Agent — Gap Analysis (v3.3.2)

Tanggal audit: 2026-11-11

## Ringkasan

v3.3.1 sudah memiliki fondasi agent loop, planning, sandbox, mutation journal, checkpoint, indexing, memory, dan role orchestration. Audit kode menunjukkan sebagian fitur recovery dan planning belum tersambung sebagai workflow end-to-end. v3.3.2 menutup celah awal pada instruksi self-repair, tetapi belum mengimplementasikan task resume atau eksekusi DAG.

## Temuan terverifikasi

| Area | Implementasi saat ini | Gap | Prioritas |
|---|---|---|---|
| Self-repair | Satu repair pass setelah kegagalan `run_command` pasca-mutasi | Belum ada diagnosis terstruktur independen; belum memperbaiki temuan Reviewer/Security Auditor | P1 |
| Task persistence | `TaskStateStore` menyimpan record per task secara atomik, active pointer, dan daftar task | Belum menyimpan execution cursor atau conversation state; belum ada resume controller | P0 |
| Planner DAG | `TaskGraph` menyimpan node dan dependency | `chat_stream` sekarang menyusun deskripsi berdasarkan topological order dan memberi instruksi sequential; node belum dieksekusi sebagai unit scheduler terpisah | P1 |
| Context/indexing | Workspace snapshot dan symbol overview digunakan dalam prompt | Belum ada retrieval berbasis dependency graph yang dibuktikan pada repository besar | P1 |
| Verification | Tester runner dan review gates tersedia | Belum ada benchmark kualitas hasil lintas proyek dan bahasa | P0 |
| Rollback | Mutation journal dan checkpoint tersedia | Perlu uji crash/restart dan konflik file yang berubah dari luar agent | P1 |
| Security | Container sandbox, approval, sensitive path checks | Docker daemon/group tetap trust boundary berprivilege tinggi; plugin/tool supply chain perlu audit terpisah | P0 |
| Observability | Logger dan event stream tersedia | Belum ada trace terstruktur per task/step/tool dengan correlation ID yang konsisten | P2 |

## Temuan arsitektur penting

1. `TaskRepairer` sebelumnya belum dipanggil dari ReAct loop. v3.3.2 mengintegrasikannya untuk menyusun repair instruction yang dibatasi dan berbasis bukti.
2. `TaskStateStore.save()` dipanggil pada beberapa tahap `chat_stream`; `load(task_id)` dan `list_tasks()` tersedia untuk inspeksi, tetapi belum ada pemanggil yang memulihkan eksekusi. Ini persistence metadata, bukan resume.
3. Planner menghasilkan `TaskGraph`; `chat_stream` kini memakai topological order untuk menyusun prompt eksekusi. Dependency belum mengendalikan dispatch node sebagai unit eksekusi terpisah.
4. ReAct loop masih menjadi eksekutor utama. Sub-agent delegation berjalan melalui tool terpisah, bukan scheduler DAG yang persisten.
5. Test suite mengukur unit/integration behavior, tetapi belum menjadi benchmark task-completion berbasis repository nyata.

## Batas audit

Audit ini berdasarkan source tree dan test suite pada VM. Belum dilakukan benchmark multi-repository, fuzzing, threat modeling menyeluruh, atau pengujian crash/reboot di tengah mutasi. Karena itu, status production-grade belum dapat diberikan.
