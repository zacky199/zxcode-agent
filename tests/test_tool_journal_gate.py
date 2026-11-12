"""Public file mutations fail closed; journal primitives retain recovery regressions."""
import asyncio
from pathlib import Path

import pytest

from tools import ToolEngine
from utils.filesystem import FileSystemUtils, ParentDirectoryAnchor


def _begin_native_operation(journal, tool_name, args):
    ticket = journal.begin_operation(tool_name, args)
    anchor = journal.open_parent_anchor(ticket)
    before_replace = lambda candidate, current_anchor: journal.assert_precommit_owned(
        ticket, candidate, current_anchor)
    retain_displaced = lambda candidate, current_anchor: journal.retain_displaced_preimage(
        ticket, candidate, current_anchor)
    return ticket, anchor, before_replace, retain_displaced


def _finish_native_operation(journal, ticket, anchor):
    operation = journal.finish_operation(ticket)
    anchor.close()
    ticket.parent_anchor = None
    return operation


def test_native_write_gate_precedes_journal_and_handler(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    engine = ToolEngine(workspace)
    invoked = []

    def fail_to_create(owner_id=None):
        raise OSError("checkpoint storage unavailable")

    def write_spy(file_path, content):
        invoked.append((file_path, content))
        (workspace / file_path).write_text(content, encoding="utf-8")
        return "Success: wrote file"

    monkeypatch.setattr(engine, "create_mutation_journal", fail_to_create)
    monkeypatch.setitem(engine._registry, "write_file", write_spy)

    result = asyncio.run(engine.execute_tool("write_file", {
        "file_path": "blocked.txt", "content": "must not be written"}))

    assert result["status"] == "error"
    assert "native file mutation is disabled" in result["output"].lower()
    assert invoked == []
    assert not (workspace / "blocked.txt").exists()
    assert not (workspace / ".zx_checkpoints").exists()


def test_failed_native_mutation_restores_only_its_recorded_target(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    target = workspace / "owned.txt"
    target.write_text("before", encoding="utf-8")
    engine = ToolEngine(workspace)

    journal = engine.create_mutation_journal(owner_id="failed-native-low-level")
    ticket, anchor, before_replace, retain_displaced = _begin_native_operation(
        journal, "write_file", {"file_path": "owned.txt", "content": "partial"})
    FileSystemUtils.atomic_write(
        target, "partial", before_replace=before_replace,
        retain_displaced=retain_displaced, parent_anchor=anchor)
    (workspace / "unrelated.txt").write_text("keep", encoding="utf-8")
    operation = _finish_native_operation(journal, ticket, anchor)
    report = journal.rollback_failed_operation(operation)

    assert target.read_text(encoding="utf-8") == "before"
    assert (workspace / "unrelated.txt").read_text(encoding="utf-8") == "keep"
    assert "owned.txt" in report.restored
    journal.close()


def test_markdown_file_action_is_blocked_before_creating_journal(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    engine = ToolEngine(workspace)

    result = asyncio.run(engine.execute_markdown_actions(
        '```file notes.md\n# Safe edit\n```'))

    assert "native file mutation is disabled" in result.lower()
    assert not (workspace / "notes.md").exists()
    assert not (workspace / ".zx_checkpoints").exists()


def test_markdown_checkpoint_failure_blocks_before_builtin_handler(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    engine = ToolEngine(workspace)
    called = []

    def fail_to_create(owner_id=None):
        raise OSError("journal disk error")

    monkeypatch.setattr(engine, "create_mutation_journal", fail_to_create)
    monkeypatch.setattr(engine.builtin, "write_file", lambda *args: called.append(args) or "ok")

    result = asyncio.run(engine.execute_markdown_actions(
        '```file blocked.md\nno write\n```'))

    assert "native file mutation is disabled" in result.lower()
    assert called == []
    assert not (workspace / "blocked.md").exists()
    assert not (workspace / ".zx_checkpoints").exists()


def test_disabled_checkpoint_policy_blocks_mutation(tmp_path):
    from execution.journal import JournalError

    workspace = tmp_path / "project"
    workspace.mkdir()
    engine = ToolEngine(workspace)
    journal = engine.create_mutation_journal(checkpoint_enabled=False)

    with pytest.raises(JournalError, match="checkpointing is disabled"):
        journal.begin_operation(
            "write_file", {"file_path": "disabled.txt", "content": "no"})
    assert not (workspace / "disabled.txt").exists()
    journal.close()


def test_second_task_failure_restores_its_prestate_not_another_owners_edit(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    engine = ToolEngine(workspace)
    first = engine.create_mutation_journal(owner_id="owner-first")
    first_ticket, first_anchor, first_before, first_retain = _begin_native_operation(
        first, "write_file", {"file_path": "shared.txt", "content": "first state"})
    FileSystemUtils.atomic_write(
        workspace / "shared.txt", "first state", before_replace=first_before,
        retain_displaced=first_retain, parent_anchor=first_anchor)
    first_operation = _finish_native_operation(first, first_ticket, first_anchor)
    first.commit_operation(first_operation)
    first.close()

    second = engine.create_mutation_journal(owner_id="owner-second")
    target = workspace / "shared.txt"

    second_ticket, second_anchor, second_before, second_retain = _begin_native_operation(
        second, "write_file", {"file_path": "shared.txt", "content": "second state"})
    FileSystemUtils.atomic_write(
        target, "second state", before_replace=second_before,
        retain_displaced=second_retain, parent_anchor=second_anchor)
    second_operation = _finish_native_operation(second, second_ticket, second_anchor)
    second.rollback_failed_operation(second_operation)

    assert target.read_text(encoding="utf-8") == "first state"
    second.close()


def test_native_write_gate_cannot_be_overridden_by_registry_handler(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    target = workspace / "native-conflict.txt"
    target.write_text("before", encoding="utf-8")
    engine = ToolEngine(workspace)
    invoked = []

    def external_write_with_success(file_path, content):
        invoked.append((file_path, content))
        target.write_text("external writer won", encoding="utf-8")
        return "Success: wrote file"

    monkeypatch.setitem(engine._registry, "write_file", external_write_with_success)
    result = asyncio.run(engine.execute_tool("write_file", {
        "file_path": "native-conflict.txt", "content": "requested task output"}))

    assert result["status"] == "error"
    assert "native file mutation is disabled" in result["output"].lower()
    assert invoked == []
    assert target.read_text(encoding="utf-8") == "before"
    assert "requested task output" not in result["output"]


def test_shell_checkpoint_failure_blocks_before_approval_or_execution(tmp_path, monkeypatch):
    import shlex
    import sys

    workspace = tmp_path / "project"
    workspace.mkdir()
    marker = workspace / "must-not-run.txt"
    code = f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')"
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"
    approvals = []
    engine = ToolEngine(workspace, confirm_callback=lambda candidate: approvals.append(candidate) or True)

    def fail_checkpoint(*args, **kwargs):
        raise OSError("checkpoint storage unavailable")

    monkeypatch.setattr(engine, "create_mutation_journal", fail_checkpoint)
    result = asyncio.run(engine.execute_tool("run_command", {"command": command}))

    assert result["status"] == "error"
    assert approvals == []
    assert not marker.exists()
    assert "checkpoint" in result["output"].lower() or "journal" in result["output"].lower()


def test_native_external_edit_before_commit_aborts_without_overwrite(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    target = workspace / "commit-race.txt"
    target.write_text("before", encoding="utf-8")
    engine = ToolEngine(workspace)
    journal = engine.create_mutation_journal(owner_id="native-write-commit-race")
    ticket, anchor, before_replace, retain_displaced = _begin_native_operation(
        journal, "write_file", {"file_path": "commit-race.txt", "content": "task output"})
    original_rename = ParentDirectoryAnchor.rename

    def inject_before_target_quarantine(anchor, source, destination):
        if source == target.name and destination.startswith(".zx-native-current-"):
            target.write_text("external edit before commit", encoding="utf-8")
        return original_rename(anchor, source, destination)

    monkeypatch.setattr(ParentDirectoryAnchor, "rename", inject_before_target_quarantine)
    with pytest.raises(Exception):
        FileSystemUtils.atomic_write(
            target, "task output", before_replace=before_replace,
            retain_displaced=retain_displaced, parent_anchor=anchor)

    assert target.read_text(encoding="utf-8") == "external edit before commit"
    assert ticket.precommit_conflicts
    journal.abort_operation(ticket)
    journal.close()


def test_successful_shell_workspace_delta_is_reported_ambiguous(tmp_path):
    import shlex
    import sys

    workspace = tmp_path / "project"
    workspace.mkdir()
    target = workspace / "shell-success.txt"
    code = f"from pathlib import Path; Path({str(target)!r}).write_text('shell output')"
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"
    engine = ToolEngine(workspace, confirm_callback=lambda candidate: True,
                        sandbox_mode="host", allow_host_execution=True)

    result = asyncio.run(engine.execute_tool("run_command", {"command": command}))

    assert result["status"] == "ok"
    assert target.read_text(encoding="utf-8") == "shell output"
    assert "conflict/ambiguous provenance" in result["output"].lower()


def test_native_edit_external_change_before_commit_is_preserved(tmp_path, monkeypatch):
    from execution.diff_engine import DiffEngine

    workspace = tmp_path / "project"
    workspace.mkdir()
    target = workspace / "edit-race.txt"
    target.write_text("before", encoding="utf-8")
    engine = ToolEngine(workspace)
    journal = engine.create_mutation_journal(owner_id="native-edit-commit-race")
    ticket, anchor, before_replace, retain_displaced = _begin_native_operation(
        journal, "edit_file", {
            "file_path": "edit-race.txt", "search": "before", "replace": "task output"})
    original_rename = ParentDirectoryAnchor.rename

    def inject_before_target_quarantine(anchor, source, destination):
        if source == target.name and destination.startswith(".zx-native-current-"):
            target.write_text("external edit", encoding="utf-8")
        return original_rename(anchor, source, destination)

    monkeypatch.setattr(ParentDirectoryAnchor, "rename", inject_before_target_quarantine)
    result = DiffEngine(workspace).apply_patch(
        "edit-race.txt", "before", "task output",
        before_replace=before_replace, retain_displaced=retain_displaced,
        parent_anchor=anchor)
    operation = _finish_native_operation(journal, ticket, anchor)
    report = journal.rollback_failed_operation(operation)

    assert result.lower().startswith("error ")
    assert target.read_text(encoding="utf-8") == "external edit"
    assert report.restored == []
    assert operation.ambiguous_paths
    journal.close()


def test_native_write_retains_displaced_inode_for_late_open_handle(tmp_path):
    import os

    workspace = tmp_path / "project"
    workspace.mkdir()
    target = workspace / "open-handle-race.txt"
    target.write_text("before", encoding="utf-8")
    old_handle = os.open(target, os.O_WRONLY | os.O_APPEND)
    engine = ToolEngine(workspace)
    journal = engine.create_mutation_journal(owner_id="late-open-handle")
    ticket, anchor, before_replace, retain_displaced = _begin_native_operation(
        journal, "write_file", {
            "file_path": "open-handle-race.txt", "content": "task output"})
    FileSystemUtils.atomic_write(
        target, "task output", before_replace=before_replace,
        retain_displaced=retain_displaced, parent_anchor=anchor)
    operation = _finish_native_operation(journal, ticket, anchor)
    journal.commit_operation(operation)

    assert target.read_text(encoding="utf-8") == "task output"
    retained_rel = journal._record_for("op-00001")["retained_preimages"][0]["path"]

    os.write(old_handle, b"-late external write")
    os.close(old_handle)
    summary = journal.close()

    retained_path = workspace / retained_rel
    assert retained_path.read_text(encoding="utf-8") == "before-late external write"
    assert "late write changed the retained original inode" in summary
    assert retained_rel in summary
    assert target.read_text(encoding="utf-8") == "task output"


def test_parent_swap_during_native_commit_preserves_replacement_file(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    parent = workspace / "parent"
    parent.mkdir(parents=True)
    target = parent / "item.txt"
    target.write_bytes(b"before")
    moved_parent = workspace / "parent-moved"
    external_bytes = b"external sentinel\x00\xff"
    engine = ToolEngine(workspace)
    journal = engine.create_mutation_journal(owner_id="parent-swap-commit")
    ticket, anchor, before_replace, retain_displaced = _begin_native_operation(
        journal, "write_file", {
            "file_path": "parent/item.txt", "content": "task output"})
    original_link = ParentDirectoryAnchor.link
    swapped = {}

    def swap_parent_before_install(anchor, source, destination):
        if not swapped and Path(destination).name == target.name:
            parent.rename(moved_parent)
            parent.mkdir()
            external = parent / target.name
            external.write_bytes(external_bytes)
            info = external.stat()
            swapped.update(path=external, device=info.st_dev, inode=info.st_ino)
        return original_link(anchor, source, destination)

    monkeypatch.setattr(ParentDirectoryAnchor, "link", swap_parent_before_install)
    try:
        FileSystemUtils.atomic_write(
            target, "task output", before_replace=before_replace,
            retain_displaced=retain_displaced, parent_anchor=anchor)
    except Exception as exc:
        error = str(exc)
    else:
        error = ""

    assert swapped, error
    external = swapped["path"]
    info = external.stat()
    assert error
    assert external.read_bytes() == external_bytes
    assert (info.st_dev, info.st_ino) == (swapped["device"], swapped["inode"])
    assert "parent directory identity" in error.lower()
    journal.abort_operation(ticket)
    journal.close()


def test_external_directory_appearing_after_checkpoint_is_not_adopted(tmp_path):
    from execution.journal import JournalError

    workspace = tmp_path / "project"
    workspace.mkdir()
    engine = ToolEngine(workspace)
    journal = engine.create_mutation_journal(owner_id="missing-parent-swap")
    ticket = journal.begin_operation("write_file", {
        "file_path": "new-parent/item.txt", "content": "task output"})
    external_parent = workspace / "new-parent"
    external_parent.mkdir()
    sentinel = external_parent / "sentinel.txt"
    external_bytes = b"external directory sentinel"
    sentinel.write_bytes(external_bytes)
    info = sentinel.stat()
    try:
        journal.open_parent_anchor(ticket)
    except JournalError as exc:
        assert "parent directory identity" in str(exc).lower()
    else:
        raise AssertionError("a parent created after checkpoint must not be adopted")

    assert sentinel.read_bytes() == external_bytes
    current = sentinel.stat()
    assert (current.st_dev, current.st_ino) == (info.st_dev, info.st_ino)
    assert not (external_parent / "item.txt").exists()
    assert ticket.precommit_conflicts
    journal.abort_operation(ticket)
    assert "parent directory identity" in journal.close().lower()
