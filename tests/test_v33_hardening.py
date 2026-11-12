import json
import os
from pathlib import Path

from core.roles import parse_review_findings, parse_test_commands
from execution.sandbox import SandboxExecutor
from execution.task_state import TaskStateStore
from execution.test_runner import TesterRunner


def test_sandbox_fails_closed_when_runtime_missing(tmp_path):
    runner = SandboxExecutor(tmp_path, runtime="/definitely/missing/runtime",
                             command_authorizer=lambda _: True)
    code, out, err = runner.run_command("echo unsafe")
    assert code == 125
    assert "SANDBOX UNAVAILABLE" in err
    assert out == ""


def test_host_execution_requires_explicit_unsafe_opt_in(tmp_path):
    try:
        SandboxExecutor(tmp_path, mode="host", command_authorizer=lambda _: True)
    except ValueError as exc:
        assert "allow_host_execution" in str(exc)
    else:
        raise AssertionError("host mode must require explicit opt-in")


def test_container_command_has_isolation_flags(tmp_path):
    runner = SandboxExecutor(tmp_path, runtime="/bin/true", command_authorizer=lambda _: True)
    args = runner._container_argv("pytest -q")
    joined = " ".join(args)
    for required in ("--read-only", "--network none", "--cap-drop ALL",
                     "no-new-privileges", "--pids-limit 256", "--memory 2g",
                     "--cpus 2", "/workspace:rw"):
        assert required in joined
    assert "--privileged" not in args
    # Do not start a login shell: it can overwrite the sandbox PATH and hide
    # toolchains installed under /usr/local (notably Go).
    assert args[-3:] == ["sh", "-c", "pytest -q"]


def test_tester_runner_executes_each_command_separately(tmp_path, monkeypatch):
    runner = SandboxExecutor(tmp_path, runtime="/bin/true", command_authorizer=lambda _: True)
    calls = []
    monkeypatch.setattr(runner, "run_command", lambda cmd, timeout=None, **kwargs: (calls.append(cmd) or (0, "ok", "")))
    result = TesterRunner(runner).run(["pytest -q", "npm test"])
    assert result["status"] == "passed"
    assert calls == ["pytest -q", "npm test"]


def test_task_state_is_atomic_recoverable_and_scoped_to_workspace(tmp_path):
    store = TaskStateStore(tmp_path)
    store.save("t-1", "planned", {"goal": "fix bug", "plan": ["test"], "api_key": "must-not-persist"})
    loaded = store.load()
    assert loaded["status"] == "stored"
    payload = loaded["state"]["payload"]
    assert payload["goal"] == "fix bug"
    assert "api_key" not in payload
    assert os.stat(store.path).st_mode & 0o777 == 0o600


def test_task_state_detects_corruption(tmp_path):
    store = TaskStateStore(tmp_path)
    store.path.write_text("{broken", encoding="utf-8")
    assert store.load()["status"] == "corrupt"


def test_review_gate_blocks_critical_and_high_but_not_medium():
    high = parse_review_findings(json.dumps({"findings": [{"severity": "high", "title": "unsafe"}]}))
    medium = parse_review_findings(json.dumps({"findings": [{"severity": "medium", "title": "style"}]}))
    assert high["status"] == "blocked" and high["blocking"]
    assert medium["status"] == "passed" and not medium["blocking"]


def test_unstructured_review_is_inconclusive_not_pass():
    result = parse_review_findings("Looks good to me")
    assert result["status"] == "inconclusive"
    assert result["blocking"] is True


def test_tester_requires_structured_commands():
    assert parse_test_commands("Run pytest") ["status"] == "inconclusive"
    parsed = parse_test_commands('{"commands":["pytest -q"]}')
    assert parsed["status"] == "ready"
    assert parsed["commands"] == ["pytest -q"]


def test_approval_denial_happens_before_runtime_invocation(tmp_path):
    runner = SandboxExecutor(tmp_path, runtime="/bin/true",
                             command_authorizer=lambda _: False)
    code, _, error = runner.run_command("echo denied")
    assert code == 126
    assert "not granted" in error.lower()



def test_verification_container_mount_is_read_only(tmp_path):
    runner = SandboxExecutor(tmp_path, mode="container", runtime="docker")
    args = runner._container_argv("pytest -q", read_only_workspace=True)
    volume_index = args.index("--volume") + 1
    assert args[volume_index].endswith(":/workspace:ro")


def test_verification_refuses_host_execution(tmp_path):
    runner = SandboxExecutor(tmp_path, mode="host", allow_host_execution=True)
    code, stdout, stderr = runner.run_verification("pytest -q")
    assert code == 125
    assert not stdout
    assert "container sandbox is required" in stderr
