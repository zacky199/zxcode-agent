"""Atomic, workspace-bound task metadata checkpoints (never store provider credentials)."""
from __future__ import annotations

import json
import os
import re
import hashlib
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


class TaskStateStore:
    VERSION = 3
    READABLE_VERSIONS = {1, 2, 3}
    FINGERPRINT_EXCLUDED = {".git", ".zx_checkpoints", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
    FINGERPRINT_MAX_FILES = 20000
    FINGERPRINT_MAX_BYTES = 512 * 1024 * 1024
    _TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace).resolve()
        checkpoint_parent = self.workspace / ".zx_checkpoints"
        if checkpoint_parent.is_symlink():
            raise ValueError("task checkpoint parent cannot be a symlink")
        checkpoint_parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root = checkpoint_parent / "tasks"
        if self.root.is_symlink():
            raise ValueError("task checkpoint directory cannot be a symlink")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.records = self.root / "records"
        if self.records.is_symlink():
            raise ValueError("task records directory cannot be a symlink")
        self.records.mkdir(parents=True, exist_ok=True, mode=0o700)
        for directory in (checkpoint_parent, self.root, self.records):
            try:
                directory.resolve().relative_to(self.workspace)
            except ValueError as exc:
                raise ValueError("task checkpoint directory escapes workspace") from exc
            os.chmod(directory, 0o700)
        self.path = self.root / "active-task.json"

    @classmethod
    def _validate_task_id(cls, task_id: str) -> str:
        if not isinstance(task_id, str) or not cls._TASK_ID.fullmatch(task_id):
            raise ValueError("invalid task_id; use 1-80 letters, digits, '_' or '-'")
        return task_id

    def _atomic_write(self, destination: Path, data: dict) -> None:
        if destination.is_symlink():
            raise ValueError("refusing to overwrite symlinked task state")
        fd, tmp = tempfile.mkstemp(prefix=".task-state-", dir=destination.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, destination)
            if hasattr(os, "O_DIRECTORY"):
                dir_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    @staticmethod
    def _safe_payload(payload: Dict[str, Any]) -> dict:
        # Explicit allow-list prevents accidental persistence of API keys/provider objects.
        safe = {}
        if "goal" in payload:
            safe["goal"] = str(payload["goal"])[:12000]
        if "plan" in payload:
            safe["plan"] = [str(x)[:2000] for x in payload["plan"][:100]] if isinstance(payload["plan"], list) else []
        if "task_graph" in payload and isinstance(payload["task_graph"], dict):
            nodes = payload["task_graph"].get("nodes", [])
            if isinstance(nodes, list) and len(nodes) <= 100:
                safe_nodes = []
                for node in nodes:
                    if not isinstance(node, dict):
                        continue
                    item = {
                        "id": str(node.get("id", ""))[:80],
                        "description": str(node.get("description", ""))[:2000],
                        "dependencies": [str(dep)[:80] for dep in node.get("dependencies", [])[:100]],
                        "status": str(node.get("status", "pending"))[:20],
                    }
                    if "result" in node:
                        item["result"] = str(node.get("result", ""))[:6000]
                    if "error_log" in node:
                        item["error_log"] = str(node.get("error_log", ""))[:3000]
                    if "retry_count" in node:
                        item["retry_count"] = max(0, min(int(node.get("retry_count", 0)), 10))
                    if "max_retries" in node:
                        item["max_retries"] = max(0, min(int(node.get("max_retries", 3)), 10))
                    acceptance = node.get("acceptance")
                    if isinstance(acceptance, dict):
                        checks = acceptance.get("checks", {})
                        item["acceptance"] = {
                            "accepted": bool(acceptance.get("accepted", False)),
                            "checks": {str(k)[:80]: bool(v) for k, v in checks.items()
                                       if isinstance(k, str)} if isinstance(checks, dict) else {},
                            "evidence": str(acceptance.get("evidence", ""))[:1000],
                        }
                    commands = node.get("verification_commands")
                    if isinstance(commands, list) and len(commands) <= 5:
                        item["verification_commands"] = [str(command)[:500] for command in commands[:5]]
                    verification = node.get("verification_evidence")
                    if isinstance(verification, dict):
                        results = verification.get("results", [])
                        item["verification_evidence"] = {
                            "status": str(verification.get("status", "unknown"))[:40],
                            "reason": str(verification.get("reason", ""))[:500],
                            "results": [{
                                "command": str(entry.get("command", ""))[:500],
                                "status": str(entry.get("status", "unknown"))[:40],
                                "returncode": (entry.get("returncode", -1)
                                               if isinstance(entry.get("returncode", -1), int)
                                               and not isinstance(entry.get("returncode", -1), bool) else -1),
                                "stdout": str(entry.get("stdout", ""))[:3000],
                                "stderr": str(entry.get("stderr", ""))[:3000],
                            } for entry in results if isinstance(entry, dict)][:5] if isinstance(results, list) else [],
                        }
                    safe_nodes.append(item)
                safe["task_graph"] = {"nodes": safe_nodes}
        if "role_results" in payload:
            safe["role_results"] = [{"role": str(x.get("role", ""))[:80],
                "model": str(x.get("model", ""))[:120], "text": str(x.get("text", ""))[:6000]}
                for x in payload["role_results"] if isinstance(x, dict)][-12:]
        if "test_results" in payload and isinstance(payload["test_results"], dict):
            tests = payload["test_results"]
            results = tests.get("results", [])
            safe["test_results"] = {"status": str(tests.get("status", "unknown"))[:40],
                "reason": str(tests.get("reason", ""))[:1000],
                "results": [{"command": str(x.get("command", ""))[:500],
                    "status": str(x.get("status", "unknown"))[:40],
                    "returncode": x.get("returncode")}
                    for x in results if isinstance(x, dict)][:20] if isinstance(results, list) else []}
        if "repair_attempt" in payload:
            safe["repair_attempt"] = max(0, min(int(payload["repair_attempt"]), 10))
        if "execution_cursor" in payload and isinstance(payload["execution_cursor"], dict):
            from execution.resume import ExecutionCursor
            safe["execution_cursor"] = ExecutionCursor.from_dict(payload["execution_cursor"]).to_dict()
        return safe

    def workspace_fingerprint(self) -> dict:
        """Hash workspace source state while excluding VCS/checkpoint/cache output.

        Bounded traversal fails closed rather than issuing a misleading partial hash.
        Symlinks are recorded as links and never followed.
        """
        digest = hashlib.sha256()
        files_seen = 0
        bytes_seen = 0
        for root, dirs, files in os.walk(self.workspace, topdown=True, followlinks=False):
            retained_dirs = []
            for dirname in sorted(dirs):
                directory = Path(root) / dirname
                if dirname in self.FINGERPRINT_EXCLUDED:
                    continue
                try:
                    dst = directory.lstat()
                    rel_dir = directory.relative_to(self.workspace).as_posix()
                    digest.update(b"dir\0" + rel_dir.encode("utf-8", "surrogateescape") + b"\0")
                    digest.update(str(stat.S_IMODE(dst.st_mode)).encode() + b"\0")
                    if stat.S_ISLNK(dst.st_mode):
                        digest.update(os.readlink(directory).encode("utf-8", "surrogateescape"))
                    else:
                        retained_dirs.append(dirname)
                except OSError as exc:
                    return {"status": "unavailable", "reason": f"cannot fingerprint directory {dirname}: {type(exc).__name__}"}
            dirs[:] = retained_dirs
            for name in sorted(files):
                path = Path(root) / name
                try:
                    rel = path.relative_to(self.workspace).as_posix()
                    st = path.lstat()
                    files_seen += 1
                    if files_seen > self.FINGERPRINT_MAX_FILES:
                        return {"status": "unavailable", "reason": "workspace exceeds fingerprint file limit"}
                    digest.update(rel.encode("utf-8", "surrogateescape") + b"\0")
                    digest.update(str(stat.S_IMODE(st.st_mode)).encode() + b"\0")
                    if stat.S_ISLNK(st.st_mode):
                        digest.update(b"link\0" + os.readlink(path).encode("utf-8", "surrogateescape"))
                    elif stat.S_ISREG(st.st_mode):
                        bytes_seen += st.st_size
                        if bytes_seen > self.FINGERPRINT_MAX_BYTES:
                            return {"status": "unavailable", "reason": "workspace exceeds fingerprint byte limit"}
                        digest.update(b"file\0")
                        with path.open("rb") as stream:
                            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                                digest.update(chunk)
                        after = path.lstat()
                        if (st.st_size, st.st_mtime_ns, st.st_ino, stat.S_IMODE(st.st_mode)) != (after.st_size, after.st_mtime_ns, after.st_ino, stat.S_IMODE(after.st_mode)):
                            return {"status": "unavailable", "reason": f"workspace file changed while fingerprinting: {rel}"}
                    else:
                        digest.update(b"special\0" + str(st.st_mode).encode())
                except (OSError, ValueError) as exc:
                    return {"status": "unavailable", "reason": f"cannot fingerprint {path.name}: {type(exc).__name__}"}
        return {"status": "ok", "sha256": digest.hexdigest(),
                "files": files_seen, "bytes": bytes_seen}

    def compare_workspace_fingerprint(self, state: dict) -> dict:
        """Compare current workspace against the durable snapshot in a task record."""
        expected = state.get("workspace_fingerprint") if isinstance(state, dict) else None
        if not isinstance(expected, dict) or expected.get("status") != "ok":
            return {"status": "unknown", "action": "manual_reconciliation",
                    "reason": "task has no complete workspace fingerprint"}
        current = self.workspace_fingerprint()
        if current.get("status") != "ok":
            return {"status": "unknown", "action": "manual_reconciliation",
                    "reason": current.get("reason", "workspace fingerprint unavailable")}
        if current.get("sha256") != expected.get("sha256"):
            return {"status": "drift", "action": "manual_reconciliation",
                    "reason": "workspace changed since the last durable task checkpoint",
                    "expected": expected.get("sha256"), "current": current.get("sha256")}
        return {"status": "match", "action": "resume_allowed", "reason": "workspace matches the last durable checkpoint"}

    def save(self, task_id: str, stage: str, payload: Dict[str, Any]) -> Path:
        task_id = self._validate_task_id(task_id)
        if not isinstance(payload, dict):
            raise TypeError("task payload must be a dictionary")
        fingerprint = self.workspace_fingerprint()
        data = {"version": self.VERSION, "task_id": task_id,
                "workspace": str(self.workspace), "stage": str(stage)[:80],
                "workspace_fingerprint": fingerprint,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "payload": self._safe_payload(payload)}
        # Persist the per-task record first. If updating the active pointer fails,
        # the task record remains available and list_tasks() can still find it.
        record_path = self.records / f"{task_id}.json"
        self._atomic_write(record_path, data)
        self._atomic_write(self.path, data)
        return record_path

    def _load_path(self, path: Path) -> dict:
        if path.is_symlink() or not path.is_file():
            return {"status": "missing"}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if (not isinstance(data, dict) or data.get("version") not in self.READABLE_VERSIONS
                    or data.get("workspace") != str(self.workspace)):
                return {"status": "incompatible"}
            if not self._TASK_ID.fullmatch(str(data.get("task_id", ""))):
                return {"status": "corrupt"}
            if not isinstance(data.get("payload"), dict):
                return {"status": "corrupt"}
            return {"status": "stored", "state": data}
        except (OSError, ValueError, TypeError):
            return {"status": "corrupt"}

    def load(self, task_id: Optional[str] = None) -> dict:
        if task_id is None:
            return self._load_path(self.path)
        task_id = self._validate_task_id(task_id)
        return self._load_path(self.records / f"{task_id}.json")

    def _inspect_task_journals(self, task_id: str) -> dict:
        """Fail closed when a task-owned journal contains an unresolved operation."""
        journal_root = self.workspace / ".zx_checkpoints" / "journals"
        if journal_root.is_symlink():
            return {"status": "unknown", "reason": "journal directory is a symlink"}
        if not journal_root.exists():
            return {"status": "clear", "reason": "no task journals found"}
        try:
            journal_root.resolve(strict=True).relative_to(self.workspace)
        except (OSError, ValueError):
            return {"status": "unknown", "reason": "journal directory escapes workspace"}
        for owner_dir in sorted(journal_root.iterdir()):
            if not owner_dir.name.startswith(task_id + "-"):
                continue
            if owner_dir.is_symlink() or not owner_dir.is_dir():
                return {"status": "unknown", "reason": "task journal owner path is unsafe"}
            manifest = owner_dir / "manifest.json"
            if manifest.is_symlink() or not manifest.is_file():
                return {"status": "unknown", "reason": f"journal manifest unavailable for {owner_dir.name}"}
            try:
                if manifest.stat().st_size > 8 * 1024 * 1024:
                    return {"status": "unknown", "reason": "journal manifest exceeds inspection limit"}
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                return {"status": "unknown", "reason": f"journal manifest is unreadable for {owner_dir.name}"}
            if isinstance(data, dict) and data.get("version") == 1:
                skips = data.get("rollback_skips", [])
                return {"status": "unknown", "owner_id": owner_dir.name,
                        "rollback_skips": len(skips) if isinstance(skips, list) else 0,
                        "reason": "legacy journal has no verifiable audit chain"}
            if (not isinstance(data, dict) or data.get("version") != 2
                    or data.get("owner_id") != owner_dir.name
                    or data.get("workspace") != str(self.workspace)
                    or not isinstance(data.get("operations", []), list)
                    or not isinstance(data.get("tool_invocations", {}), dict)
                    or not isinstance(data.get("rollback_skips", []), list)
                    or not isinstance(data.get("retained_preimages", []), list)):
                return {"status": "unknown", "reason": f"journal manifest is invalid for {owner_dir.name}"}
            chain_path = owner_dir / "audit-chain.jsonl"
            if chain_path.is_symlink() or not chain_path.is_file():
                return {"status": "unknown", "reason": f"journal audit chain unavailable for {owner_dir.name}"}
            try:
                if chain_path.stat().st_size > 16 * 1024 * 1024:
                    return {"status": "unknown", "reason": "journal audit chain exceeds inspection limit"}
                lines = chain_path.read_text(encoding="utf-8").splitlines()
                if not lines or len(lines) > 100000:
                    raise ValueError("invalid chain length")
                previous = "0" * 64
                latest = None
                for seq, line in enumerate(lines, 1):
                    entry = json.loads(line)
                    body = {"sequence": entry.get("sequence"),
                            "previous_hash": entry.get("previous_hash"),
                            "payload_hash": entry.get("payload_hash")}
                    digest = hashlib.sha256(json.dumps(
                        body, sort_keys=True, separators=(",", ":")
                    ).encode("utf-8")).hexdigest()
                    if (body["sequence"] != seq or body["previous_hash"] != previous
                            or not isinstance(body["payload_hash"], str)
                            or len(body["payload_hash"]) != 64
                            or entry.get("entry_hash") != digest):
                        raise ValueError("chain link mismatch")
                    previous = digest
                    latest = {"sequence": seq, "payload_hash": body["payload_hash"],
                              "chain_hash": digest}
                integrity = data.get("integrity")
                payload = {k: v for k, v in data.items() if k != "integrity"}
                actual = hashlib.sha256(json.dumps(
                    payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode("utf-8")).hexdigest()
                if not isinstance(integrity, dict) or integrity != latest or actual != integrity.get("payload_hash"):
                    raise ValueError("manifest and chain tail mismatch")
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                return {"status": "unknown", "reason": f"journal integrity verification failed for {owner_dir.name}"}
            terminal_operations = {"finished", "rolled_back", "aborted_before_mutation"}
            unresolved = [op for op in data.get("operations", [])
                          if not isinstance(op, dict)
                          or op.get("status") not in terminal_operations]
            inflight = [entry for entry in data.get("tool_invocations", {}).values()
                        if not isinstance(entry, dict) or entry.get("status") != "completed"]
            rollback_skips = data.get("rollback_skips", [])
            if not isinstance(rollback_skips, list):
                return {"status": "unknown", "reason": f"journal rollback metadata is invalid for {owner_dir.name}"}
            if unresolved or inflight or rollback_skips:
                return {"status": "uncertain", "owner_id": owner_dir.name,
                        "unresolved_operations": len(unresolved),
                        "in_flight_invocations": len(inflight),
                        "rollback_skips": len(rollback_skips)}
        return {"status": "clear", "reason": "all task-owned journal operations are finished"}

    def assess_recovery(self, task_id: Optional[str] = None) -> dict:
        """Assess resume safety using cursor, graph, journals and workspace drift."""
        from execution.resume import assess_recovery
        loaded = self.load(task_id)
        if loaded.get("status") != "stored":
            return {"status": loaded.get("status", "missing"), "action": "do_not_resume",
                    "reason": "task checkpoint is not available"}
        state = loaded["state"]
        decision = assess_recovery(state)
        if decision.get("action") != "restart_from_user_confirmation":
            return decision
        selected_id = state.get("task_id")
        journal = self._inspect_task_journals(selected_id)
        if journal.get("status") != "clear":
            return {"status": "uncertain", "action": "manual_reconciliation",
                    "reason": "task journal contains unresolved or unverifiable mutation evidence",
                    "journal": journal}
        fingerprint = self.compare_workspace_fingerprint(state)
        if fingerprint.get("action") != "resume_allowed":
            return {"status": fingerprint.get("status", "unknown"),
                    "action": "manual_reconciliation",
                    "reason": fingerprint.get("reason", "workspace fingerprint could not be verified"),
                    "workspace": fingerprint}
        return {**decision, "workspace": fingerprint, "journal": journal,
                "reason": "task is at a durable node boundary; journal is clear and workspace fingerprint matches; explicit user confirmation is still required"}

    def list_tasks(self) -> list[dict]:
        """List persisted task metadata newest-first; malformed records are surfaced."""
        tasks = []
        for path in self.records.glob("*.json"):
            if path.is_symlink() or not path.is_file():
                continue
            result = self._load_path(path)
            if result["status"] == "stored":
                state = result["state"]
                tasks.append({"task_id": state["task_id"], "stage": state.get("stage", "unknown"),
                              "updated_at": state.get("updated_at", ""), "status": "stored"})
            else:
                tasks.append({"task_id": path.stem, "stage": "unknown",
                              "updated_at": "", "status": result["status"]})
        return sorted(tasks, key=lambda item: item.get("updated_at", ""), reverse=True)

    def clear(self, task_id: Optional[str] = None) -> None:
        if task_id is None:
            if self.path.is_symlink():
                raise ValueError("refusing to remove symlinked task state")
            self.path.unlink(missing_ok=True)
            return
        task_id = self._validate_task_id(task_id)
        record = self.records / f"{task_id}.json"
        if record.is_symlink():
            raise ValueError("refusing to remove symlinked task record")
        record.unlink(missing_ok=True)
        active = self._load_path(self.path)
        if active.get("status") == "stored" and active["state"].get("task_id") == task_id:
            self.clear()
