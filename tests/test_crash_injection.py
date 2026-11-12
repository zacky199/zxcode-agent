"""Process-death tests for durable checkpoint/journal boundaries."""
import json
import os
import subprocess
import sys
from pathlib import Path


def _crash(script: str, *args: str) -> None:
    completed = subprocess.run([sys.executable, "-c", script, *args],
                               capture_output=True, text=True, timeout=15)
    assert completed.returncode == 73, (completed.returncode, completed.stdout, completed.stderr)


def test_task_checkpoint_survives_immediate_process_exit(tmp_path):
    script = r'''
import os, sys
from pathlib import Path
from execution.task_state import TaskStateStore
store = TaskStateStore(Path(sys.argv[1]))
store.save("crash-checkpoint", "node_boundary", {"goal": "persist me"})
os._exit(73)
'''
    _crash(script, str(tmp_path))
    from execution.task_state import TaskStateStore
    loaded = TaskStateStore(tmp_path).load("crash-checkpoint")
    assert loaded["status"] == "stored"
    assert loaded["state"]["payload"]["goal"] == "persist me"


def test_tool_invocation_claim_is_durable_before_process_exit(tmp_path):
    script = r'''
import os, sys
from pathlib import Path
from execution.journal import MutationJournal
journal = MutationJournal(Path(sys.argv[1]), owner_id="crash-invocation")
journal.claim_tool_invocation("call-1", "write_file", {"file_path": "a.txt", "content": "x"})
os._exit(73)
'''
    _crash(script, str(tmp_path))
    manifest = tmp_path / ".zx_checkpoints" / "journals" / "crash-invocation" / "manifest.json"
    data = json.loads(manifest.read_text())
    assert data["tool_invocations"]
    assert next(iter(data["tool_invocations"].values()))["status"] == "in_flight"


def test_mutation_preimage_and_pending_operation_are_durable_before_process_exit(tmp_path):
    script = r'''
import os, sys
from pathlib import Path
from execution.journal import MutationJournal
root = Path(sys.argv[1]); (root / "before.txt").write_text("original")
journal = MutationJournal(root, owner_id="crash-mutation")
journal.begin_operation("write_file", {"file_path": "before.txt", "content": "replacement"})
os._exit(73)
'''
    _crash(script, str(tmp_path))
    manifest = tmp_path / ".zx_checkpoints" / "journals" / "crash-mutation" / "manifest.json"
    data = json.loads(manifest.read_text())
    assert data.get("operations"), data.keys()
    assert (tmp_path / "before.txt").read_text() == "original"


def test_crash_after_workspace_mutation_requires_manual_reconciliation(tmp_path):
    script = r'''
import os, sys
from pathlib import Path
from execution.journal import MutationJournal
from execution.task_state import TaskStateStore
from execution.resume import ExecutionCursor
from utils.filesystem import FileSystemUtils
root = Path(sys.argv[1]); store = TaskStateStore(root)
cursor = ExecutionCursor(phase="executing", current_node_id="step_1", mutation_started=True)
store.save("interrupted-task", "node_running", {"goal": "edit", "execution_cursor": cursor.to_dict(),
    "task_graph": {"nodes": [{"id":"step_1", "description":"edit", "dependencies":[], "status":"running"}]}})
journal = MutationJournal(root, owner_id="interrupted-task")
ticket = journal.begin_operation("write_file", {"file_path":"edited.txt", "content":"after"})
FileSystemUtils.atomic_write(root / "edited.txt", "after")
os._exit(73)
'''
    _crash(script, str(tmp_path))
    from execution.task_state import TaskStateStore
    store = TaskStateStore(tmp_path)
    decision = store.assess_recovery("interrupted-task")
    assert decision["action"] == "manual_reconciliation"
    assert (tmp_path / "edited.txt").read_text() == "after"
