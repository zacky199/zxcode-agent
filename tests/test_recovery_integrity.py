import json

from execution.journal import MutationJournal
from execution.task_state import TaskStateStore


def _make_closed_journal(workspace, owner="chain-task-run1"):
    journal = MutationJournal(workspace, owner_id=owner)
    journal._ensure_ready()
    journal.close()
    return workspace / ".zx_checkpoints" / "journals" / owner


def test_recovery_accepts_valid_hash_chained_journal(tmp_path):
    store = TaskStateStore(tmp_path)
    owner = _make_closed_journal(tmp_path)
    result = store._inspect_task_journals("chain-task")
    manifest = json.loads((owner / "manifest.json").read_text())
    chain = (owner / "audit-chain.jsonl").read_text().splitlines()

    assert result["status"] == "clear"
    assert manifest["version"] == 2
    assert manifest["integrity"]["sequence"] == len(chain)
    assert len(chain) >= 2


def test_recovery_rejects_manifest_tampering(tmp_path):
    store = TaskStateStore(tmp_path)
    owner = _make_closed_journal(tmp_path)
    path = owner / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["rollback_skips"] = ["tampered"]
    path.write_text(json.dumps(manifest))

    result = store._inspect_task_journals("chain-task")
    assert result["status"] == "unknown"
    assert "integrity" in result["reason"]


def test_recovery_rejects_truncated_or_modified_audit_chain(tmp_path):
    store = TaskStateStore(tmp_path)
    owner = _make_closed_journal(tmp_path)
    path = owner / "audit-chain.jsonl"
    lines = path.read_text().splitlines()
    path.write_text("\n".join(lines[:-1]) + "\n")

    result = store._inspect_task_journals("chain-task")
    assert result["status"] == "unknown"
    assert "integrity" in result["reason"]


def test_recovery_rejects_legacy_journal_without_audit_chain(tmp_path):
    store = TaskStateStore(tmp_path)
    owner = tmp_path / ".zx_checkpoints" / "journals" / "chain-task-legacy"
    owner.mkdir(parents=True)
    (owner / "manifest.json").write_text(json.dumps({
        "version": 1, "owner_id": owner.name, "workspace": str(tmp_path),
        "operations": [], "tool_invocations": {}, "rollback_skips": [],
    }))

    result = store._inspect_task_journals("chain-task")
    assert result["status"] == "unknown"
    assert "legacy" in result["reason"]
