"""Tests for the tool engine: native tools, markdown protocol, safety guard."""
import asyncio
import os
from pathlib import Path

import pytest

from tools import ToolEngine


@pytest.fixture()
def engine(workspace):
    return ToolEngine(workspace)


def test_tool_schemas(engine):
    # File edits are opt-in, so the default engine does not advertise them.
    schemas = engine.get_tool_schemas()
    names = {s["function"]["name"] for s in schemas}
    assert {"read_file", "run_command", "search_files", "glob_files",
            "git_status", "git_diff", "delegate_tasks"} <= names
    assert not {"write_file", "edit_file"} & names

    engine.set_file_mutations_enabled(True)
    names = {s["function"]["name"] for s in engine.get_tool_schemas()}
    assert {"write_file", "edit_file"} <= names


def test_write_then_read(engine, workspace):
    result = asyncio.run(engine.execute_tool("write_file", {
        "file_path": "a.txt", "content": "hello"}))
    assert result["status"] == "error"
    assert "native file mutation is disabled" in result["output"].lower()
    assert "hello" not in result["output"]
    read = asyncio.run(engine.execute_tool("read_file", {"file_path": "a.txt"}))
    assert read["status"] == "error"
    assert not (workspace / "a.txt").exists()


def test_edit_file_fails_closed_without_changing_file(engine, workspace):
    original = "before\nmiddle\nafter\n"
    (workspace / "b.txt").write_text(original)
    result = asyncio.run(engine.execute_tool("edit_file", {
        "file_path": "b.txt", "search": "middle", "replace": "changed"}))
    assert result["status"] == "error"
    assert "native file mutation is disabled" in result["output"].lower()
    assert "middle" not in result["output"]
    assert "changed" not in result["output"]
    assert (workspace / "b.txt").read_text() == original


def test_edit_file_failure_is_error(engine, workspace):
    (workspace / "b.txt").write_text("before\nmiddle\nafter\n")
    result = asyncio.run(engine.execute_tool("edit_file", {
        "file_path": "b.txt", "search": "NOT-PRESENT", "replace": "x"}))
    assert result["status"] == "error"


def test_dangerous_command_guard(engine):
    assert engine.is_command_dangerous("rm -rf /")
    assert engine.is_command_dangerous("git push origin main")
    assert not engine.is_command_dangerous("ls -la")


def test_unknown_tool(engine):
    result = asyncio.run(engine.execute_tool("nope", {}))
    assert result["status"] == "error"


def test_path_escape_blocked(engine, workspace):
    result = asyncio.run(engine.execute_tool("write_file", {
        "file_path": "../evil.txt", "content": "x"}))
    assert result["status"] == "error" or "Access denied" in result["output"]


def test_markdown_protocol(engine, workspace):
    text = '```file notes.md\n# Notes\n```'
    callbacks = []
    result = asyncio.run(engine.execute_markdown_actions(
        text,
        before_action=lambda name: callbacks.append(("before", name)) or "snapshot",
        after_action=lambda name, snapshot, status: callbacks.append(
            ("after", name, snapshot, status)) or True,
    ))
    assert "notes.md" in result
    assert "native file mutation is disabled" in result.lower()
    assert not (workspace / "notes.md").exists()
    assert callbacks == [("before", "write_file"), ("after", "write_file", "snapshot", "error")]


def test_markdown_file_action_fails_closed_without_creating_journal(engine, workspace):
    result = asyncio.run(engine.execute_markdown_actions('```file notes.md\n# Notes\n```'))

    assert "notes.md" in result
    assert "native file mutation is disabled" in result.lower()
    assert not (workspace / "notes.md").exists()
    assert not (workspace / ".zx_checkpoints").exists()


def test_markdown_patch_action_fails_closed_without_changing_file(engine, workspace):
    target = workspace / "notes.md"
    target.write_text("before\n", encoding="utf-8")
    text = """```patch notes.md
<<<< SEARCH
before
====
after
>>>> REPLACE
```"""

    result = asyncio.run(engine.execute_markdown_actions(text))

    assert "native file mutation is disabled" in result.lower()
    assert target.read_text(encoding="utf-8") == "before\n"
    assert not (workspace / ".zx_checkpoints").exists()



def _python_marker_command(marker: Path) -> str:
    import shlex
    import sys

    code = f"from pathlib import Path; Path({str(marker)!r}).write_text('executed')"
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"


def test_shell_command_without_authorizer_is_denied(engine, workspace):
    marker = workspace / "command-ran.txt"
    result = asyncio.run(engine.execute_tool("run_command", {
        "command": _python_marker_command(marker)}))

    assert result["status"] == "error"
    assert "authorization" in result["output"].lower()
    assert not marker.exists()


def test_shell_command_requires_approval_even_if_not_patterned(workspace):
    calls = []
    engine = ToolEngine(workspace, confirm_callback=lambda command: calls.append(command) or False)
    result = asyncio.run(engine.execute_tool("run_command", {"command": "printf safe-looking"}))

    assert result["status"] == "error"
    assert calls == ["printf safe-looking"]


def test_markdown_bash_uses_same_fail_closed_gate(engine, workspace):
    marker = workspace / "markdown-command-ran.txt"
    result = asyncio.run(engine.execute_markdown_actions(
        f"```bash\n{_python_marker_command(marker)}\n```"))

    assert "authorization" in result.lower()
    assert not marker.exists()


def test_explicit_shell_approval_executes_command(workspace):
    approved = []
    engine = ToolEngine(workspace, confirm_callback=lambda command: approved.append(command) or True,
                        sandbox_mode="host", allow_host_execution=True)
    result = asyncio.run(engine.execute_tool("run_command", {"command": "printf approved"}))

    assert result["status"] == "ok"
    assert "approved" in result["output"]
    assert approved == ["printf approved"]


def test_builtin_command_entry_point_denies_without_authorizer(workspace):
    from tools.builtin import BuiltinTools

    result = BuiltinTools(workspace).execute_command("printf bypass")

    assert "authorization" in result.lower()


def test_search_files_skips_symlinks_outside_workspace(engine, workspace, tmp_path):
    external_root = workspace.parent / f"{workspace.name}-external"
    external_root.mkdir()
    outside_file = external_root / "outside-search.md"
    outside_file.write_text("SYMLINK_SEARCH_SECRET", encoding="utf-8")
    outside_dir = external_root / "outside-search-dir"
    outside_dir.mkdir()
    (outside_dir / "nested.md").write_text("SYMLINK_NESTED_SECRET", encoding="utf-8")
    try:
        (workspace / "linked.md").symlink_to(outside_file)
        (workspace / "linked-dir").symlink_to(outside_dir, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    result = engine.builtin.search_files("SYMLINK_")

    assert "No matches found" in result
    assert "SYMLINK_SEARCH_SECRET" not in result
    assert "SYMLINK_NESTED_SECRET" not in result


@pytest.mark.parametrize("filename", [
    ".env", ".env.production", ".npmrc", ".aws/credentials",
    "id_rsa", "id_ed25519_sk", "server.key", "credentials.json", "client_secret.json",
])
def test_read_file_denies_credential_like_files(engine, workspace, filename):
    target = workspace / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("TOP_SECRET_SENTINEL=do-not-return", encoding="utf-8")

    result = engine.builtin.read_file(filename)

    assert "denied" in result.lower()
    assert "TOP_SECRET_SENTINEL" not in result
    assert "do-not-return" not in result


@pytest.mark.parametrize("filename", [
    ".env", ".env.production", ".npmrc", ".aws/credentials",
    "id_rsa", "id_ed25519_sk", "server.key", "credentials.json", "client_secret.json",
])
def test_search_files_omits_credential_like_files(engine, workspace, filename):
    target = workspace / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("TOP_SECRET_SENTINEL=do-not-return", encoding="utf-8")

    result = engine.builtin.search_files("TOP_SECRET_SENTINEL")

    assert "TOP_SECRET_SENTINEL" not in result
    assert "do-not-return" not in result



def test_git_diff_filters_tracked_sensitive_paths(engine, workspace):
    import shutil
    import subprocess

    if shutil.which("git") is None:
        pytest.skip("git is unavailable")

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=workspace, check=True,
            capture_output=True, text=True,
        )

    git("init", "--quiet")
    git("config", "user.name", "Cue Test")
    git("config", "user.email", "cue-test@example.invalid")
    (workspace / ".env").write_text("TOKEN=GIT_ENV_SECRET_BEFORE\n", encoding="utf-8")
    key_dir = workspace / "deploy"
    key_dir.mkdir()
    (key_dir / "server.key").write_text("GIT_KEY_SECRET_BEFORE\n", encoding="utf-8")
    (workspace / "safe.md").write_text("safe before\n", encoding="utf-8")
    git("add", ".")
    git("commit", "--quiet", "-m", "baseline")

    (workspace / ".env").write_text("TOKEN=GIT_ENV_SECRET_AFTER\n", encoding="utf-8")
    (key_dir / "server.key").write_text("GIT_KEY_SECRET_AFTER\n", encoding="utf-8")
    (workspace / "safe.md").write_text("safe after\n", encoding="utf-8")

    output = engine.builtin.git_diff()

    assert "GIT_ENV_SECRET" not in output
    assert "GIT_KEY_SECRET" not in output
    assert ".env" not in output
    assert "server.key" not in output
    assert "safe.md" in output


@pytest.mark.asyncio
async def test_git_diff_sensitive_tracked_paths_are_not_sent_to_provider(tmp_path, monkeypatch):
    import json
    import subprocess
    from types import SimpleNamespace

    from config import constants
    from core import AgentConfig, ZXAgentPro

    workspace = tmp_path / "project"
    workspace.mkdir()

    def git(*args):
        subprocess.run(
            ["git", *args], cwd=workspace, check=True,
            capture_output=True, text=True,
        )

    git("init", "--quiet")
    git("config", "user.name", "Cue Test")
    git("config", "user.email", "cue-test@example.invalid")
    (workspace / ".env").write_text("TOKEN=PROVIDER_ENV_SECRET_BEFORE\n", encoding="utf-8")
    key_dir = workspace / "deploy"
    key_dir.mkdir()
    (key_dir / "server.key").write_text(
        "PROVIDER_KEY_SECRET_BEFORE\n", encoding="utf-8")
    (workspace / "credentials.json").write_text(
        '{"token":"PROVIDER_CREDENTIAL_SECRET_BEFORE"}\n', encoding="utf-8")
    (workspace / "safe.md").write_text("safe before\n", encoding="utf-8")
    git("add", ".")
    git("commit", "--quiet", "-m", "baseline")

    (workspace / ".env").write_text("TOKEN=PROVIDER_ENV_SECRET_AFTER\n", encoding="utf-8")
    (key_dir / "server.key").write_text(
        "PROVIDER_KEY_SECRET_AFTER\n", encoding="utf-8")
    (workspace / "credentials.json").write_text(
        '{"token":"PROVIDER_CREDENTIAL_SECRET_AFTER"}\n', encoding="utf-8")
    (workspace / "safe.md").write_text("safe after\n", encoding="utf-8")

    class CapturingProvider:
        model = "git-diff-capture"
        supports_tools = True

        def __init__(self):
            self.stream_calls = 0
            self.contexts = []

        async def complete_async(self, system, messages, tools=None):
            self.contexts.append(system + "\n" + json.dumps(messages, sort_keys=True))
            return SimpleNamespace(text=(
                '{"objective":"inspect diff","steps":[{"description":"inspect",'
                '"depends_on":[]}]}'
            ))

        async def stream_complete_async(self, system, messages, tools=None):
            self.stream_calls += 1
            if self.stream_calls == 1:
                yield {"type": "tool_call", "content": {
                    "id": "read-diff", "name": "git_diff", "arguments": {}}}
            else:
                self.contexts.append(system + "\n" + json.dumps(messages, sort_keys=True))
                yield {"type": "text", "content": "reviewed"}
            yield {"type": "done", "content": ""}

    monkeypatch.setattr(constants, "DEFAULT_PLUGIN_DIR", tmp_path / "no-plugins")
    provider = CapturingProvider()
    agent = ZXAgentPro(
        provider, workspace,
        config=AgentConfig(
            checkpoint_mutations=False, auto_rollback=False, store_tool_memory=False),
    )
    events = [event async for event in agent.chat_stream("inspect tracked changes")]

    assert events[-1]["type"] == "done"
    provider_context = "\n".join(provider.contexts)
    for sentinel in (
        "PROVIDER_ENV_SECRET", "PROVIDER_KEY_SECRET", "PROVIDER_CREDENTIAL_SECRET",
        ".env", "server.key", "credentials.json",
    ):
        assert sentinel not in provider_context
    assert "safe.md" in provider_context



def test_git_diff_treats_pathspec_magic_filenames_literally(engine, workspace):
    import shutil
    import subprocess

    if shutil.which("git") is None:
        pytest.skip("git is unavailable")

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=workspace, check=True,
            capture_output=True, text=True,
        )

    git("init", "--quiet")
    git("config", "user.name", "Cue Test")
    git("config", "user.email", "cue-test@example.invalid")
    (workspace / ".env").write_text("TOKEN=PATHSPEC_SECRET_BEFORE\n", encoding="utf-8")
    magic_file = workspace / ":(glob)*"
    magic_file.write_text("safe before\n", encoding="utf-8")
    git("add", "--all")
    git("commit", "--quiet", "-m", "baseline")

    (workspace / ".env").write_text("TOKEN=PATHSPEC_SECRET_AFTER\n", encoding="utf-8")
    magic_file.write_text("safe after\n", encoding="utf-8")

    output = engine.builtin.git_diff()

    assert "PATHSPEC_SECRET" not in output
    assert ".env" not in output


@pytest.mark.parametrize("filename", [
    ".env", ".env.production", "server.key", "credentials.json",
    ".aws/credentials", "private_keys/id_rsa",
])
def test_write_file_denies_sensitive_paths_without_changing_file(engine, workspace, filename):
    target = workspace / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    original = "ORIGINAL_SECRET_SENTINEL=keep\n"
    target.write_text(original, encoding="utf-8")

    result = asyncio.run(engine.execute_tool("write_file", {
        "file_path": filename, "content": "NEW_SECRET_SENTINEL=overwrite\n"}))

    assert result["status"] == "error"
    assert "denied" in result["output"].lower()
    assert target.read_text(encoding="utf-8") == original
    assert "ORIGINAL_SECRET_SENTINEL" not in result["output"]
    assert "NEW_SECRET_SENTINEL" not in result["output"]


@pytest.mark.parametrize("filename", [
    ".env", ".env.production", "server.key", "credentials.json",
    ".aws/credentials", "private_keys/id_rsa",
])
def test_edit_file_denies_sensitive_paths_without_changing_file(engine, workspace, filename):
    target = workspace / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    original = "ORIGINAL_SECRET_SENTINEL=keep\n"
    target.write_text(original, encoding="utf-8")

    result = asyncio.run(engine.execute_tool("edit_file", {
        "file_path": filename,
        "search": "ORIGINAL_SECRET_SENTINEL=keep",
        "replace": "NEW_SECRET_SENTINEL=overwrite",
    }))

    assert result["status"] == "error"
    assert "denied" in result["output"].lower()
    assert target.read_text(encoding="utf-8") == original
    assert "ORIGINAL_SECRET_SENTINEL" not in result["output"]
    assert "NEW_SECRET_SENTINEL" not in result["output"]


def test_markdown_file_block_denies_sensitive_target_without_changing_file(engine, workspace):
    target = workspace / ".env"
    original = "ORIGINAL_SECRET_SENTINEL=keep\n"
    target.write_text(original, encoding="utf-8")
    callbacks = []
    result = asyncio.run(engine.execute_markdown_actions(
        "```file .env\nNEW_SECRET_SENTINEL=overwrite\n```",
        before_action=lambda name: callbacks.append(("before", name)) or object(),
        after_action=lambda name, snapshot, status: callbacks.append(
            ("after", name, status)) or True,
    ))

    assert "denied" in result.lower()
    assert "ORIGINAL_SECRET_SENTINEL" not in result
    assert "NEW_SECRET_SENTINEL" not in result
    assert target.read_text(encoding="utf-8") == original
    assert callbacks == [("before", "write_file"), ("after", "write_file", "error")]


def test_markdown_patch_block_denies_sensitive_target_without_changing_file(engine, workspace):
    target = workspace / "server.key"
    original = "ORIGINAL_KEY_SENTINEL=keep\n"
    target.write_text(original, encoding="utf-8")
    patch = """```patch server.key
<<<< SEARCH
ORIGINAL_KEY_SENTINEL=keep
====
NEW_KEY_SENTINEL=overwrite
>>>> REPLACE
```"""
    callbacks = []
    result = asyncio.run(engine.execute_markdown_actions(
        patch,
        before_action=lambda name: callbacks.append(("before", name)) or object(),
        after_action=lambda name, snapshot, status: callbacks.append(
            ("after", name, status)) or True,
    ))

    assert "denied" in result.lower()
    assert "ORIGINAL_KEY_SENTINEL" not in result
    assert "NEW_KEY_SENTINEL" not in result
    assert target.read_text(encoding="utf-8") == original
    assert callbacks == [("before", "edit_file"), ("after", "edit_file", "error")]


@pytest.mark.parametrize("tool_name", ["write_file", "edit_file"])
def test_mutation_tools_deny_symlink_alias_to_sensitive_path(engine, workspace, tool_name):
    secret = workspace / ".env"
    original = "ORIGINAL_SECRET_SENTINEL=keep\n"
    secret.write_text(original, encoding="utf-8")
    alias = workspace / "notes.txt"
    try:
        alias.symlink_to(secret)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    if tool_name == "write_file":
        args = {"file_path": "notes.txt", "content": "NEW_SECRET_SENTINEL=overwrite\n"}
    else:
        args = {
            "file_path": "notes.txt",
            "search": "ORIGINAL_SECRET_SENTINEL=keep",
            "replace": "NEW_SECRET_SENTINEL=overwrite",
        }
    result = asyncio.run(engine.execute_tool(tool_name, args))

    assert result["status"] == "error"
    assert "denied" in result["output"].lower()
    assert secret.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("sensitive_name", [".env", "server.key"])
def test_edit_file_denies_hard_link_without_changing_secret(engine, workspace, sensitive_name):
    import os

    secret = workspace / sensitive_name
    original = b"ORIGINAL_HARDLINK_SENTINEL=keep\n"
    secret.write_bytes(original)
    target = workspace / "notes.txt"
    try:
        os.link(secret, target)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"hard links unavailable: {exc}")
    assert os.path.samefile(secret, target)

    result = asyncio.run(engine.execute_tool("edit_file", {
        "file_path": "notes.txt",
        "search": "ORIGINAL_HARDLINK_SENTINEL=keep",
        "replace": "SAFE_TARGET_CONTENT=updated",
    }))

    assert result["status"] == "error"
    assert "denied" in result["output"].lower()
    assert secret.read_bytes() == original
    assert target.read_bytes() == original
    assert os.path.samefile(secret, target)
    assert "ORIGINAL_HARDLINK_SENTINEL" not in result["output"]


@pytest.mark.parametrize("sensitive_name", [".env", "server.key"])
def test_markdown_patch_denies_hard_link_without_changing_secret(engine, workspace, sensitive_name):
    import os

    secret = workspace / sensitive_name
    original = b"ORIGINAL_HARDLINK_SENTINEL=keep\n"
    secret.write_bytes(original)
    target = workspace / "notes.txt"
    try:
        os.link(secret, target)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"hard links unavailable: {exc}")
    assert os.path.samefile(secret, target)
    patch = """```patch notes.txt
<<<< SEARCH
ORIGINAL_HARDLINK_SENTINEL=keep
====
SAFE_TARGET_CONTENT=updated
>>>> REPLACE
```"""
    callbacks = []

    result = asyncio.run(engine.execute_markdown_actions(
        patch,
        before_action=lambda name: object(),
        after_action=lambda name, snapshot, status: callbacks.append((name, status)) or True,
    ))

    assert "denied" in result.lower()
    assert secret.read_bytes() == original
    assert target.read_bytes() == original
    assert os.path.samefile(secret, target)
    assert "ORIGINAL_HARDLINK_SENTINEL" not in result
    assert callbacks == [("edit_file", "error")]


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are not portable to Windows")
def test_edit_file_preserves_standard_permissions(engine, workspace):
    import stat

    target = workspace / "permissions.txt"
    target.write_text("before\n", encoding="utf-8")
    target.chmod(0o640)

    result = asyncio.run(engine.execute_tool("edit_file", {
        "file_path": "permissions.txt", "search": "before", "replace": "after"}))

    assert result["status"] == "error"
    assert "native file mutation is disabled" in result["output"].lower()
    assert target.read_text(encoding="utf-8") == "before\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640


@pytest.mark.parametrize("secret_name", [".env", "server.key"])
def test_read_and_search_deny_hard_link_alias_to_sensitive_file(engine, workspace, secret_name):
    secret = workspace / secret_name
    sentinel = "HARDLINK_READ_SECRET_SENTINEL"
    secret.write_text(sentinel + "\n", encoding="utf-8")
    alias = workspace / "notes.md"
    try:
        os.link(secret, alias)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"hard links unavailable: {exc}")

    read_result = engine.builtin.read_file("notes.md")
    search_result = engine.builtin.search_files(sentinel)

    assert sentinel not in read_result
    assert "Access denied" in read_result
    assert sentinel not in search_result
    assert "No matches found" in search_result


@pytest.mark.parametrize("secret_name", [".env", "server.key"])
def test_git_diff_omits_hard_link_alias_to_sensitive_changes(engine, workspace, secret_name):
    import shutil
    import subprocess

    if shutil.which("git") is None:
        pytest.skip("git is unavailable")

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=workspace, check=True,
            capture_output=True, text=True,
        )

    git("init", "--quiet")
    git("config", "user.name", "Cue Test")
    git("config", "user.email", "cue-test@example.invalid")
    secret = workspace / secret_name
    secret.write_text("GIT_HARDLINK_SECRET_BEFORE\n", encoding="utf-8")
    alias = workspace / "notes.md"
    try:
        os.link(secret, alias)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"hard links unavailable: {exc}")
    git("add", "--all")
    git("commit", "--quiet", "-m", "baseline")

    secret.write_text("GIT_HARDLINK_SECRET_AFTER\n", encoding="utf-8")

    output = engine.builtin.git_diff()

    assert "GIT_HARDLINK_SECRET" not in output
    assert "GIT_HARDLINK_SECRET_BEFORE" not in output
    assert "GIT_HARDLINK_SECRET_AFTER" not in output
    assert "notes.md" not in output


def test_path_listings_omit_hard_link_alias(engine, workspace):
    secret = workspace / ".env"
    secret.write_text("LISTING_HARDLINK_SECRET_SENTINEL\n", encoding="utf-8")
    (workspace / "public.md").write_text("public", encoding="utf-8")
    alias = workspace / "notes.md"
    try:
        os.link(secret, alias)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"hard links unavailable: {exc}")

    directory_output = engine.builtin.list_directory(".")
    glob_output = engine.builtin.glob_files("*.md")

    assert "notes.md" not in directory_output
    assert "notes.md" not in glob_output
    assert "public.md" in directory_output
    assert "public.md" in glob_output
    assert "LISTING_HARDLINK_SECRET_SENTINEL" not in directory_output + glob_output


def test_git_status_omits_hard_link_alias_to_sensitive_changes(engine, workspace):
    import shutil
    import subprocess

    if shutil.which("git") is None:
        pytest.skip("git is unavailable")

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=workspace, check=True,
            capture_output=True, text=True,
        )

    git("init", "--quiet")
    git("config", "user.name", "Cue Test")
    git("config", "user.email", "cue-test@example.invalid")
    secret = workspace / ".env"
    secret.write_text("STATUS_HARDLINK_SECRET_BEFORE\n", encoding="utf-8")
    alias = workspace / "notes.md"
    try:
        os.link(secret, alias)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"hard links unavailable: {exc}")
    (workspace / "public.txt").write_text("STATUS_PUBLIC_BEFORE\n", encoding="utf-8")
    git("add", "--all")
    git("commit", "--quiet", "-m", "baseline")
    secret.write_text("STATUS_HARDLINK_SECRET_AFTER\n", encoding="utf-8")
    (workspace / "public.txt").write_text("STATUS_PUBLIC_AFTER\n", encoding="utf-8")

    output = engine.builtin.git_status()

    assert "notes.md" not in output
    assert ".env" not in output
    assert "public.txt" in output
    assert "STATUS_HARDLINK_SECRET" not in output


@pytest.mark.parametrize("secret_name", [".env", "server.key"])
def test_edit_file_hard_link_mismatch_denies_before_diff_engine(
        engine, workspace, secret_name, monkeypatch):
    sentinel = "HARDLINK_EDIT_SECRET_SENTINEL"
    original = f"API_TOKEN = {sentinel}\n"
    secret = workspace / secret_name
    secret.write_text(original, encoding="utf-8")
    alias = workspace / "notes.txt"
    try:
        os.link(secret, alias)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"hard links unavailable: {exc}")

    diff_calls = []
    original_apply_patch = engine.builtin.diff_engine.apply_patch

    def record_patch(*args, **kwargs):
        diff_calls.append(args)
        return original_apply_patch(*args, **kwargs)

    monkeypatch.setattr(engine.builtin.diff_engine, "apply_patch", record_patch)
    output = engine.builtin.edit_file(
        "notes.txt", f"API_TOKEN = {sentinel} missing", "API_TOKEN = replaced")

    assert diff_calls == []
    assert "denied" in output.lower()
    assert sentinel not in output
    assert secret.read_text(encoding="utf-8") == original
    assert alias.read_text(encoding="utf-8") == original
    assert os.path.samefile(secret, alias)


@pytest.mark.parametrize("secret_name", [".env", "server.key"])
def test_markdown_patch_hard_link_mismatch_denies_before_diff_engine(
        engine, workspace, secret_name, monkeypatch):
    sentinel = "MARKDOWN_HARDLINK_SECRET_SENTINEL"
    original = f"API_TOKEN = {sentinel}\n"
    secret = workspace / secret_name
    secret.write_text(original, encoding="utf-8")
    alias = workspace / "notes.txt"
    try:
        os.link(secret, alias)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"hard links unavailable: {exc}")

    diff_calls = []
    original_apply_patch = engine.builtin.diff_engine.apply_patch

    def record_patch(*args, **kwargs):
        diff_calls.append(args)
        return original_apply_patch(*args, **kwargs)

    monkeypatch.setattr(engine.builtin.diff_engine, "apply_patch", record_patch)
    patch = f"""```patch notes.txt
<<<< SEARCH
API_TOKEN = {sentinel} missing
====
API_TOKEN = replaced
>>>> REPLACE
```"""
    output = asyncio.run(engine.execute_markdown_actions(
        patch,
        before_action=lambda name: object(),
        after_action=lambda name, snapshot, status: True,
    ))

    assert diff_calls == []
    assert "denied" in output.lower()
    assert sentinel not in output
    assert secret.read_text(encoding="utf-8") == original
    assert alias.read_text(encoding="utf-8") == original
    assert os.path.samefile(secret, alias)


def test_shell_approval_and_execution_are_serialized(tmp_path, monkeypatch):
    import concurrent.futures
    import threading
    import time
    import execution.sandbox as sandbox_module
    from execution.sandbox import SandboxExecutor

    approved = []
    approval_lock = threading.Lock()
    active = 0
    max_active = 0

    def authorize(command):
        with approval_lock:
            approved.append(command)
        return True

    class FakeProcess:
        def __init__(self, command, **kwargs):
            self.returncode = None

        def communicate(self, timeout=None):
            nonlocal active, max_active
            with approval_lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.03)
            with approval_lock:
                active -= 1
            self.returncode = 0
            return "ok", ""

        def poll(self):
            return self.returncode

    monkeypatch.setattr(sandbox_module.subprocess, "Popen", FakeProcess)

    runner = SandboxExecutor(tmp_path, command_authorizer=authorize, mode="host", allow_host_execution=True)
    commands = ["printf first", "printf second"]
    start_barrier = threading.Barrier(2)

    def invoke(command):
        start_barrier.wait()
        return runner.run_command(command)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(invoke, commands))

    assert all(code == 0 for code, _, _ in results)
    assert sorted(approved) == sorted(commands)
    assert max_active == 1


def test_shell_approval_is_exact_and_not_reused(tmp_path):
    from execution.sandbox import SandboxExecutor

    reviewed = []
    runner = SandboxExecutor(
        tmp_path,
        command_authorizer=lambda command: reviewed.append(command) or command == "printf approved",
        mode="host", allow_host_execution=True,
    )

    approved_result = runner.run_command("printf approved")
    denied_result = runner.run_command("printf different")

    assert approved_result[0] == 0
    assert denied_result[0] == 126
    assert "not granted" in denied_result[2].lower()
    assert reviewed == ["printf approved", "printf different"]


def test_legacy_builtin_registration_exposes_read_only_tools_only(workspace):
    from tools.builtin import register_builtin_tools
    from tools.registry import ToolRegistry

    registry = ToolRegistry()
    with pytest.warns(DeprecationWarning, match="read-only"):
        register_builtin_tools(registry, workspace)

    assert {"read_file", "list_directory", "search_files", "glob_files"} <= set(registry.tools)
    assert not ({"write_file", "edit_file", "run_command"} & set(registry.tools))
    with pytest.raises(ValueError):
        registry.execute("write_file", {"file_path": "bypass.txt", "content": "blocked"})
    assert not (workspace / "bypass.txt").exists()


def test_shell_timeout_terminates_descendant_processes(tmp_path):
    import sys
    import time
    from execution.sandbox import SandboxExecutor

    marker = tmp_path / "descendant-survived.txt"
    child_code = (
        "import time; time.sleep(0.8); "
        f"open({str(marker)!r}, 'w').write('survived')"
    )
    command = f"{sys.executable} -c {child_code!r}"
    runner = SandboxExecutor(tmp_path, command_authorizer=lambda _: True, mode="host", allow_host_execution=True)

    code, _, stderr = runner.run_command(command, timeout=0.15)
    time.sleep(1.0)

    assert code == -1
    assert "process tree terminated" in stderr.lower()
    assert not marker.exists()
