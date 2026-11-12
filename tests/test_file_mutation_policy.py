"""File mutations stay disabled until verified OS-level isolation exists."""
import asyncio

import pytest

from tools import ToolEngine
from tools.builtin import BuiltinTools


BLOCKED = "native file mutation is disabled"


def _setup_target(workspace, parent_state):
    if parent_state == "existing":
        parent = workspace / "parent"
        parent.mkdir(parents=True)
        target = parent / "target.txt"
        target.write_text("ORIGINAL_FILE_SENTINEL", encoding="utf-8")
        return "parent/target.txt", target, "ORIGINAL_FILE_SENTINEL"
    return "missing/target.txt", workspace / "missing" / "target.txt", None


def _assert_blocked_without_leak(output, *sentinels):
    assert BLOCKED in output.lower()
    for sentinel in sentinels:
        assert sentinel not in output


@pytest.mark.parametrize("tool_name", ["write_file", "edit_file"])
@pytest.mark.parametrize("parent_state", ["existing", "missing"])
def test_direct_builtin_file_mutations_fail_closed(workspace, tool_name, parent_state):
    filepath, target, original = _setup_target(workspace, parent_state)
    builtin = BuiltinTools(workspace)
    kwargs = {
        "before_replace": lambda *_: None,
        "retain_displaced": lambda *_: None,
        "parent_anchor": object(),
    }
    if tool_name == "write_file":
        output = builtin.write_file(filepath, "NEW_CONTENT_SENTINEL", **kwargs)
    else:
        output = builtin.edit_file(
            filepath, "ORIGINAL_FILE_SENTINEL", "NEW_CONTENT_SENTINEL", **kwargs)

    _assert_blocked_without_leak(
        output, "ORIGINAL_FILE_SENTINEL", "NEW_CONTENT_SENTINEL")
    if original is None:
        assert not target.parent.exists()
    else:
        assert target.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("tool_name", ["write_file", "edit_file"])
@pytest.mark.parametrize("parent_state", ["existing", "missing"])
def test_tool_engine_blocks_before_handler_and_checkpoint(
        workspace, monkeypatch, tool_name, parent_state):
    filepath, target, original = _setup_target(workspace, parent_state)
    engine = ToolEngine(workspace)
    invoked = []

    def bypass_handler(*args, **kwargs):
        invoked.append((args, kwargs))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("HANDLER_MUTATION_SENTINEL", encoding="utf-8")
        return "Success: handler ran"

    monkeypatch.setitem(engine._registry, tool_name, bypass_handler)
    if tool_name == "write_file":
        args = {
            "file_path": filepath,
            "content": "NEW_CONTENT_SENTINEL",
            "allow_file_mutation": True,
            "os_isolation": True,
        }
    else:
        args = {
            "file_path": filepath,
            "search": "ORIGINAL_FILE_SENTINEL",
            "replace": "NEW_CONTENT_SENTINEL",
            "allow_file_mutation": True,
            "os_isolation": True,
        }

    result = asyncio.run(engine.execute_tool(tool_name, args))

    assert result["status"] == "error"
    _assert_blocked_without_leak(
        result["output"], "ORIGINAL_FILE_SENTINEL", "NEW_CONTENT_SENTINEL",
        "HANDLER_MUTATION_SENTINEL")
    assert invoked == []
    assert not (workspace / ".zx_checkpoints").exists()
    if original is None:
        assert not target.parent.exists()
    else:
        assert target.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("block_kind", ["file", "patch"])
@pytest.mark.parametrize("parent_state", ["existing", "missing"])
def test_markdown_file_and_patch_are_denied_without_journal_or_content_leak(
        workspace, block_kind, parent_state):
    filepath, target, original = _setup_target(workspace, parent_state)
    engine = ToolEngine(workspace)
    if block_kind == "file":
        block = f"```file {filepath}\nNEW_MARKDOWN_SENTINEL\n```"
        action = "write_file"
    else:
        block = (
            f"```patch {filepath}\n<<<< SEARCH\nORIGINAL_FILE_SENTINEL\n"
            "====\nNEW_MARKDOWN_SENTINEL\n>>>> REPLACE\n```"
        )
        action = "edit_file"
    callbacks = []

    output = asyncio.run(engine.execute_markdown_actions(
        block,
        before_action=lambda name: callbacks.append(("before", name)) or object(),
        after_action=lambda name, snapshot, status: callbacks.append(
            ("after", name, status)) or True,
    ))

    _assert_blocked_without_leak(
        output, "ORIGINAL_FILE_SENTINEL", "NEW_MARKDOWN_SENTINEL")
    assert callbacks == [("before", action), ("after", action, "error")]
    assert not (workspace / ".zx_checkpoints").exists()
    if original is None:
        assert not target.parent.exists()
    else:
        assert target.read_text(encoding="utf-8") == original


def test_tool_schemas_omit_native_file_mutations_when_disabled(workspace):
    schemas = ToolEngine(workspace).get_tool_schemas()
    names = {schema["function"]["name"] for schema in schemas}
    assert "write_file" not in names
    assert "edit_file" not in names
