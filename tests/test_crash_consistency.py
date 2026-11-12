"""Subprocess crash tests for durable journal, task-state, rollback and snapshot boundaries."""
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

from execution.journal import MutationJournal
from execution.task_state import TaskStateStore


PROJECT = Path(__file__).resolve().parents[1]


def _crash(code: str, expected: int, cwd: Path):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT)
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=str(cwd), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20,
    )
    assert result.returncode == expected, (
        f"expected child exit {expected}, got {result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


def test_crash_after_audit_append_before_manifest_replace_fails_closed(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    code = r'''
import os
from pathlib import Path
from execution.journal import MutationJournal
root = Path(os.environ["TEST_WORKSPACE"])
j = MutationJournal(root, owner_id="crash-chain-run1")
j._ensure_ready()
j._tool_invocations["crash-marker"] = {"tool_name":"write_file","args_hash":"a"*64,"status":"in_flight"}
os.replace = lambda *a, **k: os._exit(71)
j._persist_manifest()
'''
    env = os.environ.copy()
    env["TEST_WORKSPACE"] = str(workspace)
    env["PYTHONPATH"] = str(PROJECT)
    result = subprocess.run([sys.executable, "-c", code], cwd=PROJECT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    assert result.returncode == 71

    store = TaskStateStore(workspace)
    inspected = store._inspect_task_journals("crash-chain")
    assert inspected["status"] == "unknown"
    assert "integrity" in inspected["reason"]


def test_crash_during_rollback_after_restore_is_not_marked_safe(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "note.txt"
    target.write_text("before", encoding="utf-8")
    code = r'''
import os
from pathlib import Path
from execution.journal import MutationJournal
from utils.filesystem import FileSystemUtils
root = Path(os.environ["TEST_WORKSPACE"])
j = MutationJournal(root, owner_id="crash-rollback-run1")
ticket = j.begin_operation("write_file", {"file_path":"note.txt","content":"after"})
FileSystemUtils.atomic_write(root / "note.txt", "after")
op = j.finish_operation(ticket)
original_persist = j._persist_manifest
calls = {"count": 0}
def die_after_restore(*a, **k):
    calls["count"] += 1
    if calls["count"] == 2:
        os._exit(72)
    return original_persist(*a, **k)
j._persist_manifest = die_after_restore
j.rollback_failed_operation(op)
'''
    env = os.environ.copy()
    env["TEST_WORKSPACE"] = str(workspace)
    env["PYTHONPATH"] = str(PROJECT)
    result = subprocess.run([sys.executable, "-c", code], cwd=PROJECT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    assert result.returncode == 72
    assert target.read_text(encoding="utf-8") == "before"

    inspected = TaskStateStore(workspace)._inspect_task_journals("crash-rollback")
    assert inspected["status"] == "uncertain"
    assert inspected["unresolved_operations"] >= 1


def test_task_record_survives_crash_before_active_pointer_replace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    code = r'''
import os
from execution.task_state import TaskStateStore
store = TaskStateStore(os.environ["TEST_WORKSPACE"])
store.save("existing-task", "planned", {"goal":"old"})
original = os.replace
count = {"n": 0}
def replace(src, dst):
    count["n"] += 1
    if count["n"] == 2:
        os._exit(73)
    return original(src, dst)
os.replace = replace
store.save("existing-task", "executing", {"goal":"new"})
'''
    env = os.environ.copy()
    env["TEST_WORKSPACE"] = str(workspace)
    env["PYTHONPATH"] = str(PROJECT)
    result = subprocess.run([sys.executable, "-c", code], cwd=PROJECT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    assert result.returncode == 73
    store = TaskStateStore(workspace)
    record = store.load("existing-task")
    active = store.load()
    assert record["status"] == "stored"
    assert record["state"]["payload"]["goal"] == "new"
    assert active["status"] == "stored"
    assert active["state"]["payload"]["goal"] == "old"


def test_checkpoint_crash_before_atomic_publish_leaves_no_named_snapshot(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "hello.txt").write_text("hello", encoding="utf-8")
    code = r'''
import os
from pathlib import Path
from execution.checkpoint import CheckpointManager
root = Path(os.environ["TEST_WORKSPACE"])
os.replace = lambda *a, **k: os._exit(74)
CheckpointManager(root).create_snapshot("pre_mutation")
'''
    env = os.environ.copy()
    env["TEST_WORKSPACE"] = str(workspace)
    env["PYTHONPATH"] = str(PROJECT)
    result = subprocess.run([sys.executable, "-c", code], cwd=PROJECT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    assert result.returncode == 74
    checkpoint_dir = workspace / ".zx_checkpoints"
    assert not list(checkpoint_dir.glob("pre_mutation_*.zip"))
    # A process-kill can leave an untrusted temporary file; it is not published.
    for temp in checkpoint_dir.glob(".checkpoint-*.tmp"):
        temp.unlink()


def test_checkpoint_crash_after_replace_leaves_complete_zip(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "hello.txt").write_text("hello", encoding="utf-8")
    code = r'''
import os, stat
from pathlib import Path
from execution.checkpoint import CheckpointManager
root = Path(os.environ["TEST_WORKSPACE"])
real_fsync = os.fsync
def fsync(fd):
    if stat.S_ISDIR(os.fstat(fd).st_mode):
        os._exit(75)
    return real_fsync(fd)
os.fsync = fsync
CheckpointManager(root).create_snapshot("pre_mutation")
'''
    env = os.environ.copy()
    env["TEST_WORKSPACE"] = str(workspace)
    env["PYTHONPATH"] = str(PROJECT)
    result = subprocess.run([sys.executable, "-c", code], cwd=PROJECT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    assert result.returncode == 75
    snapshots = list((workspace / ".zx_checkpoints").glob("pre_mutation_*.zip"))
    assert len(snapshots) == 1
    with zipfile.ZipFile(snapshots[0]) as archive:
        assert archive.testzip() is None
        assert archive.read("hello.txt") == b"hello"
