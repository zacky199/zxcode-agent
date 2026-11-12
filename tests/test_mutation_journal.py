"""TDD coverage for task-owned mutation journals and guarded rollback."""
import stat
from pathlib import Path

import pytest

from execution.journal import JournalError, MutationCoordinator, MutationJournal
from utils.filesystem import FileSystemUtils


def test_failed_file_mutation_restores_only_its_owned_path(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    target = workspace / "owned.txt"
    target.write_text("before", encoding="utf-8")
    journal = MutationJournal(workspace, owner_id="task-a")

    ticket = journal.begin_operation("write_file", {
        "file_path": "owned.txt", "content": "partial"})
    FileSystemUtils.atomic_write(target, "partial")
    unrelated = workspace / "external.txt"
    unrelated.write_text("leave me", encoding="utf-8")
    operation = journal.finish_operation(ticket)
    report = journal.rollback_failed_operation(operation)

    assert target.read_text(encoding="utf-8") == "before"
    assert unrelated.read_text(encoding="utf-8") == "leave me"
    assert report.restored == ["owned.txt"]
    journal.close()


def test_failed_creation_is_removed_only_while_post_state_is_unchanged(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    journal = MutationJournal(workspace, owner_id="task-create")

    ticket = journal.begin_operation("write_file", {
        "file_path": "new.txt", "content": "partial"})
    target = workspace / "new.txt"
    target.write_text("partial", encoding="utf-8")
    operation = journal.finish_operation(ticket)
    report = journal.rollback_failed_operation(operation)

    assert not target.exists()
    assert report.restored == ["new.txt"]
    journal.close()


def test_rollback_preserves_file_changed_after_operation(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    target = workspace / "shared.txt"
    target.write_text("before", encoding="utf-8")
    journal = MutationJournal(workspace, owner_id="task-external")

    ticket = journal.begin_operation("edit_file", {
        "file_path": "shared.txt", "search": "before", "replace": "agent output"})
    FileSystemUtils.atomic_write(target, "agent output")
    operation = journal.finish_operation(ticket)
    target.write_text("external update", encoding="utf-8")
    report = journal.rollback_failed_operation(operation)

    assert target.read_text(encoding="utf-8") == "external update"
    assert report.restored == []
    assert report.skipped and "shared.txt" in report.skipped[0]
    journal.close()


def test_journal_is_private_and_exposes_manual_undo_material(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    target = workspace / "note.txt"
    target.write_text("before", encoding="utf-8")
    journal = MutationJournal(workspace, owner_id="task-private")

    ticket = journal.begin_operation("write_file", {
        "file_path": "note.txt", "content": "after"})
    FileSystemUtils.atomic_write(target, "after")
    journal.finish_operation(ticket)
    summary = journal.close()

    journal_root = workspace / ".zx_checkpoints" / "journals" / "task-private"
    assert journal_root.is_dir()
    assert stat.S_IMODE(journal_root.stat().st_mode) == 0o700
    assert (journal_root / "manifest.json").is_file()
    assert "note.txt" in summary
    assert str(journal_root.relative_to(workspace)) in summary


def test_sensitive_or_escaping_targets_fail_before_mutation(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    journal = MutationJournal(workspace, owner_id="task-paths")

    with pytest.raises(JournalError):
        journal.begin_operation("write_file", {"file_path": "../outside.txt"})
    with pytest.raises(JournalError):
        journal.begin_operation("write_file", {"file_path": ".env"})

    assert not (workspace.parent / "outside.txt").exists()
    journal.close()


def test_coordinator_rejects_overlapping_owners(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    coordinator = MutationCoordinator()
    first = MutationJournal(workspace, owner_id="first", coordinator=coordinator)
    second = MutationJournal(workspace, owner_id="second", coordinator=coordinator)

    first_ticket = first.begin_operation("write_file", {"file_path": "same.txt"})
    with pytest.raises(JournalError, match="owned"):
        second.begin_operation("write_file", {"file_path": "same.txt"})
    first.abort_operation(first_ticket)
    second_ticket = second.begin_operation("write_file", {"file_path": "same.txt"})
    second.abort_operation(second_ticket)
    first.close()
    second.close()


def test_unverified_native_delete_is_preserved_as_ambiguous(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    target = workspace / "deleted.txt"
    target.write_text("keep this", encoding="utf-8")
    journal = MutationJournal(workspace, owner_id="task-delete")

    ticket = journal.begin_operation("edit_file", {
        "file_path": "deleted.txt", "search": "keep this", "replace": "change"})
    target.unlink()
    operation = journal.finish_operation(ticket)
    report = journal.rollback_failed_operation(operation)

    assert report.restored == []
    assert not target.exists()
    assert any("ambiguous writer provenance" in item.lower() for item in report.skipped)
    journal.close()


def test_rollback_preserves_external_inode_replacement_even_with_same_content(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    target = workspace / "replace.txt"
    target.write_text("before", encoding="utf-8")
    journal = MutationJournal(workspace, owner_id="task-replace")

    ticket = journal.begin_operation("write_file", {
        "file_path": "replace.txt", "content": "agent state"})
    FileSystemUtils.atomic_write(target, "agent state")
    operation = journal.finish_operation(ticket)
    replacement = workspace / "external.tmp"
    replacement.write_text("agent state", encoding="utf-8")
    replacement.replace(target)

    report = journal.rollback_failed_operation(operation)

    assert target.read_text(encoding="utf-8") == "agent state"
    assert report.restored == []
    assert any("changed since operation" in item for item in report.skipped)
    journal.close()


def test_rollback_rechecks_fingerprint_at_restore_boundary(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    target = workspace / "race.txt"
    target.write_text("before", encoding="utf-8")
    journal = MutationJournal(workspace, owner_id="task-race")

    ticket = journal.begin_operation("write_file", {
        "file_path": "race.txt", "content": "agent"})
    FileSystemUtils.atomic_write(target, "agent")
    operation = journal.finish_operation(ticket)
    original_capture = journal._capture_one_at
    calls = 0

    def inject_external_change(anchor, name, rel):
        nonlocal calls
        calls += 1
        if calls == 2 and rel == "race.txt" and name == "race.txt":
            target.write_text("external edit", encoding="utf-8")
        return original_capture(anchor, name, rel)

    monkeypatch.setattr(journal, "_capture_one_at", inject_external_change)
    report = journal.rollback_failed_operation(operation)

    assert target.read_text(encoding="utf-8") == "external edit"
    assert report.restored == []
    assert any("changed immediately before restore" in item for item in report.skipped)
    journal.close()


def test_shell_journal_covers_all_workspace_subtrees_but_preserves_ambiguous_deltas(tmp_path):
    workspace = tmp_path / "project"
    for relative in (".git/config", "dist/output.js", "node_modules/pkg/index.js"):
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("before", encoding="utf-8")
    journal = MutationJournal(workspace, owner_id="task-shell-all")

    ticket = journal.begin_operation("run_command", {"command": "arbitrary shell"})
    before_paths = set(journal._record_for(ticket.operation_id)["before"])
    assert {".git/config", "dist/output.js", "node_modules/pkg/index.js"} <= before_paths
    for relative in (".git/config", "dist/output.js", "node_modules/pkg/index.js"):
        (workspace / relative).write_text("after", encoding="utf-8")
    operation = journal.finish_operation(ticket)
    report = journal.rollback_failed_operation(operation)

    assert report.restored == []
    assert len(report.skipped) == 3
    for relative in (".git/config", "dist/output.js", "node_modules/pkg/index.js"):
        assert (workspace / relative).read_text(encoding="utf-8") == "after"
    assert all("ambiguous writer provenance" in item.lower() for item in report.skipped)
    journal.close()


def test_overlapping_path_claims_are_rejected_for_same_and_different_owners(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    coordinator = MutationCoordinator()
    first = MutationJournal(workspace, owner_id="task-parent", coordinator=coordinator)
    second = MutationJournal(workspace, owner_id="task-child", coordinator=coordinator)
    ticket = first.begin_operation("write_file", {"file_path": "parent/child.txt"})

    with pytest.raises(JournalError, match="overlapping path"):
        first.begin_operation("write_file", {"file_path": "parent/child.txt"})
    with pytest.raises(JournalError, match="overlapping path"):
        second.begin_operation("write_file", {"file_path": "parent"})

    first.abort_operation(ticket)
    first.close()
    second.close()


def test_external_edit_during_restore_staging_is_not_overwritten(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    target = workspace / "staged-race.txt"
    target.write_text("before", encoding="utf-8")
    journal = MutationJournal(workspace, owner_id="task-stage-race")

    ticket = journal.begin_operation("write_file", {
        "file_path": "staged-race.txt", "content": "agent"})
    FileSystemUtils.atomic_write(target, "agent")
    operation = journal.finish_operation(ticket)
    original_capture = journal._capture_one_at
    calls = 0

    def inject_at_commit_boundary(anchor, name, rel):
        nonlocal calls
        calls += 1
        if calls == 3 and rel == "staged-race.txt" and name == "staged-race.txt":
            target.write_text("concurrent external", encoding="utf-8")
        return original_capture(anchor, name, rel)

    monkeypatch.setattr(journal, "_capture_one_at", inject_at_commit_boundary)
    report = journal.rollback_failed_operation(operation)

    assert target.read_text(encoding="utf-8") == "concurrent external"
    assert report.restored == []
    assert any("before restore commit" in item for item in report.skipped)
    journal.close()


def test_external_change_during_quarantine_is_restored_without_overwrite(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    target = workspace / "quarantine-race.txt"
    target.write_text("before", encoding="utf-8")
    journal = MutationJournal(workspace, owner_id="task-quarantine-race")

    ticket = journal.begin_operation("write_file", {
        "file_path": "quarantine-race.txt", "content": "agent"})
    FileSystemUtils.atomic_write(target, "agent")
    operation = journal.finish_operation(ticket)
    original_capture = journal._capture_one_at

    def edit_displaced_entry(anchor, name, rel):
        if name.startswith(".zx-rollback-current-"):
            (anchor.workspace / Path(rel).parent / name).write_text(
                "external race", encoding="utf-8")
        return original_capture(anchor, name, rel)

    monkeypatch.setattr(journal, "_capture_one_at", edit_displaced_entry)
    report = journal.rollback_failed_operation(operation)

    assert target.read_text(encoding="utf-8") == "external race"
    assert report.restored == []
    assert any("external state restored to its path" in item for item in report.skipped)
    journal.close()


def test_shell_external_write_between_begin_and_finish_is_preserved_as_ambiguous(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    target = workspace / "external-during-shell.txt"
    target.write_text("before", encoding="utf-8")
    journal = MutationJournal(workspace, owner_id="shell-race")

    ticket = journal.begin_operation("run_command", {"command": "true"})
    # Simulate an independent process changing the file while the shell command
    # itself is a no-op; the workspace before/after delta cannot prove authorship.
    target.write_text("external update", encoding="utf-8")
    operation = journal.finish_operation(ticket)

    report = journal.rollback_failed_operation(operation)

    assert target.read_text(encoding="utf-8") == "external update"
    assert report.restored == []
    assert "external-during-shell.txt" in operation.ambiguous_paths
    assert any("ambiguous writer provenance" in item.lower() for item in report.skipped)
    journal.close()


def test_post_scan_failure_is_reported_as_uncertain_in_final_summary(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    target = workspace / "unknown-after-scan.txt"
    target.write_text("before", encoding="utf-8")
    journal = MutationJournal(workspace, owner_id="post-scan-failure")
    ticket = journal.begin_operation("run_command", {"command": "true"})
    target.write_text("possibly external", encoding="utf-8")

    def fail_post_scan(*args, **kwargs):
        raise JournalError("injected post-scan failure")

    monkeypatch.setattr(journal, "_capture_ticket_scope", fail_post_scan)
    operation = journal.finish_operation(ticket)
    journal.commit_operation(operation)
    summary = journal.close()

    assert operation.capture_error
    assert target.read_text(encoding="utf-8") == "possibly external"
    assert "CONFLICT/UNCERTAIN" in summary
    assert "post-scan failed" in summary
    assert "delta could not be determined" in summary
    assert "0 file/path change(s)" not in summary


def test_parent_swap_during_restore_preserves_external_file(tmp_path, monkeypatch):
    from tools import ToolEngine
    from utils.filesystem import FileSystemUtils, ParentDirectoryAnchor

    workspace = tmp_path / "project"
    parent = workspace / "parent"
    parent.mkdir(parents=True)
    target = parent / "item.txt"
    target.write_bytes(b"before")
    engine = ToolEngine(workspace)
    journal = engine.create_mutation_journal(owner_id="restore-parent-swap")
    ticket = journal.begin_operation("write_file", {
        "file_path": "parent/item.txt", "content": "task output"})
    anchor = journal.open_parent_anchor(ticket)
    FileSystemUtils.atomic_write(
        target, "task output",
        before_replace=lambda candidate, current_anchor: journal.assert_precommit_owned(
            ticket, candidate, current_anchor),
        retain_displaced=lambda candidate, current_anchor: journal.retain_displaced_preimage(
            ticket, candidate, current_anchor),
        parent_anchor=anchor)
    operation = journal.finish_operation(ticket)
    anchor.close()
    ticket.parent_anchor = None
    journal.commit_operation(operation)

    moved_parent = workspace / "parent-moved"
    external_bytes = b"external sentinel\x00\xff"
    original_link = ParentDirectoryAnchor.link
    swapped = {}

    def swap_parent_before_restore_install(anchor, source, destination):
        if (not swapped and Path(source).name.startswith(".zx-restore-")
                and Path(destination).name == target.name):
            parent.rename(moved_parent)
            parent.mkdir()
            external = parent / target.name
            external.write_bytes(external_bytes)
            info = external.stat()
            swapped.update(path=external, device=info.st_dev, inode=info.st_ino)
        return original_link(anchor, source, destination)

    monkeypatch.setattr(ParentDirectoryAnchor, "link", swap_parent_before_restore_install)
    report = journal.rollback_failed_operation(operation)

    assert swapped, report
    external = swapped["path"]
    info = external.stat()
    assert report.restored == []
    assert any("parent directory identity" in item.lower() for item in report.skipped)
    assert external.read_bytes() == external_bytes
    assert (info.st_dev, info.st_ino) == (swapped["device"], swapped["inode"])
    journal.close()


def test_review_context_contains_bounded_textual_diff_and_omits_sensitive_paths(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    target = workspace / "reviewed.py"
    target.write_text("value = 1\n", encoding="utf-8")
    journal = MutationJournal(workspace, owner_id="task-review-context")

    ticket = journal.begin_operation("edit_file", {
        "file_path": "reviewed.py", "search": "value = 1", "replace": "value = 2"})
    FileSystemUtils.atomic_write(target, "value = 2\n")
    journal.finish_operation(ticket)
    review = journal.review_context(max_chars=4000)
    journal.close()

    assert "value = 1" in review and "value = 2" in review
    assert len(review) <= 4000


def test_tool_invocation_idempotency_claim_is_durable_and_rejects_duplicates(tmp_path):
    journal = MutationJournal(tmp_path, owner_id="idempotency-test")
    first = journal.claim_tool_invocation("provider-call-1", "write_file", {
        "file_path": "src/a.py", "content": "print('a')"
    })
    assert first == {"claimed": True, "status": "in_flight"}
    duplicate_in_flight = journal.claim_tool_invocation("provider-call-1", "write_file", {
        "file_path": "src/a.py", "content": "print('a')"
    })
    assert duplicate_in_flight == {"claimed": False, "status": "in_flight"}
    journal.finish_tool_invocation("provider-call-1", "ok")
    duplicate_completed = journal.claim_tool_invocation("provider-call-1", "write_file", {
        "file_path": "src/a.py", "content": "print('a')"
    })
    assert duplicate_completed == {"claimed": False, "status": "completed"}
    journal.close()


def test_tool_invocation_id_reuse_with_different_arguments_is_rejected(tmp_path):
    journal = MutationJournal(tmp_path, owner_id="idempotency-conflict")
    journal.claim_tool_invocation("provider-call-2", "write_file", {
        "file_path": "a.txt", "content": "one"
    })
    with pytest.raises(JournalError, match="reused with different arguments"):
        journal.claim_tool_invocation("provider-call-2", "write_file", {
            "file_path": "a.txt", "content": "two"
        })
    journal.close()


def test_rollback_does_not_touch_workspace_when_intent_cannot_be_persisted(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    target = workspace / "owned.txt"
    target.write_text("before", encoding="utf-8")
    journal = MutationJournal(workspace, owner_id="rollback-intent-fail")

    ticket = journal.begin_operation("write_file", {
        "file_path": "owned.txt", "content": "after"})
    FileSystemUtils.atomic_write(target, "after")
    operation = journal.finish_operation(ticket)

    def fail_persist():
        raise JournalError("simulated storage failure")

    journal._persist_manifest = fail_persist
    report = journal.rollback_failed_operation(operation)

    assert target.read_text(encoding="utf-8") == "after"
    assert report.restored == []
    assert report.skipped
    assert any("rollback intent could not be persisted" in item for item in report.skipped)


def test_completed_rollback_is_terminal_for_recovery_assessment(tmp_path):
    from execution.task_state import TaskStateStore

    workspace = tmp_path / "project"
    workspace.mkdir()
    target = workspace / "owned.txt"
    target.write_text("before", encoding="utf-8")
    journal = MutationJournal(workspace, owner_id="rolledback-task-run1")

    ticket = journal.begin_operation("write_file", {
        "file_path": "owned.txt", "content": "after"})
    FileSystemUtils.atomic_write(target, "after")
    operation = journal.finish_operation(ticket)
    report = journal.rollback_failed_operation(operation)
    journal.close()

    assert report.ok
    assert target.read_text(encoding="utf-8") == "before"
    result = TaskStateStore(workspace)._inspect_task_journals("rolledback-task")
    assert result["status"] == "clear"
