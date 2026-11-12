import os
import stat

import pytest

from config.models import AppConfig
from config.settings import SettingsManager
from utils.filesystem import FileSystemUtils


def test_settings_file_is_private_even_with_permissive_umask(tmp_path):
    config_path = tmp_path / "config.json"
    previous_umask = os.umask(0)
    try:
        SettingsManager(config_path).save(AppConfig(api_key="test-secret"))
    finally:
        os.umask(previous_umask)

    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    assert "test-secret" in config_path.read_text(encoding="utf-8")


def test_loading_existing_config_tightens_permissions(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text('{"api_key": "test-secret"}', encoding="utf-8")
    config_path.chmod(0o644)

    loaded = SettingsManager(config_path).load()

    assert loaded.api_key == "test-secret"
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600


def test_default_config_directory_is_private(tmp_path, monkeypatch):
    from config import settings as settings_module

    config_dir = tmp_path / "private-config"
    config_path = config_dir / "config.json"
    monkeypatch.setattr(settings_module, "DEFAULT_CONFIG_DIR", config_dir)
    monkeypatch.setattr(settings_module, "DEFAULT_CONFIG_FILE", config_path)

    SettingsManager(config_path).save(AppConfig(api_key="test-secret"))

    assert stat.S_IMODE(config_dir.stat().st_mode) == 0o700


def test_safe_relative_path_raises_for_escape(tmp_path):
    base = tmp_path / "workspace"
    base.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")

    with pytest.raises(ValueError):
        FileSystemUtils.safe_relative_path(outside, base)


def test_safe_relative_path_resolves_symlink_escape(tmp_path):
    base = tmp_path / "workspace"
    base.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    link = base / "link.txt"
    try:
        link.symlink_to(outside)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(ValueError):
        FileSystemUtils.safe_relative_path(link, base)


def test_workspace_context_skips_symlinks_outside_workspace(tmp_path):
    from core import ZXAgentPro

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside_file = tmp_path / "outside.md"
    outside_file.write_text("OUTSIDE_CONTEXT_SECRET", encoding="utf-8")
    outside_dir = tmp_path / "outside-dir"
    outside_dir.mkdir()
    (outside_dir / "secret.md").write_text("OUTSIDE_DIR_SECRET", encoding="utf-8")
    try:
        (workspace / "linked.md").symlink_to(outside_file)
        (workspace / "linked-dir").symlink_to(outside_dir, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    agent = object.__new__(ZXAgentPro)
    agent.workspace = workspace
    context = agent._get_workspace_snapshot()

    assert "OUTSIDE_CONTEXT_SECRET" not in context
    assert "OUTSIDE_DIR_SECRET" not in context
    assert "linked-dir/" not in context


def test_workspace_context_omits_visible_credential_like_files(tmp_path):
    from core import ZXAgentPro

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    secret_file = workspace / "credentials.json"
    secret_file.write_text('{"token":"CONTEXT_SECRET_SENTINEL"}', encoding="utf-8")
    agent = object.__new__(ZXAgentPro)
    agent.workspace = workspace

    context = agent._get_workspace_snapshot()

    assert "CONTEXT_SECRET_SENTINEL" not in context
    assert "credentials.json (" not in context


@pytest.mark.asyncio
async def test_hard_link_alias_is_not_sent_in_provider_context(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from config import constants
    from core import AgentConfig, ZXAgentPro

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sentinel = "HARDLINK_PROVIDER_CONTEXT_SENTINEL"
    secret = workspace / ".env"
    secret.write_text(sentinel + "\n", encoding="utf-8")
    try:
        os.link(secret, workspace / "notes.md")
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"hard links unavailable: {exc}")
    monkeypatch.setattr(constants, "DEFAULT_PLUGIN_DIR", tmp_path / "no-plugins")

    class CapturingProvider:
        model = "hardlink-context-test"
        supports_tools = True

        def __init__(self):
            self.payloads = []

        async def complete_async(self, system, messages, tools=None):
            self.payloads.append(f"{system}\n{messages}")
            return SimpleNamespace(text=(
                '{"objective":"inspect","steps":[{"description":"inspect",'
                '"depends_on":[]}]}'))

        async def stream_complete_async(self, system, messages, tools=None):
            self.payloads.append(f"{system}\n{messages}")
            yield {"type": "text", "content": "inspected"}
            yield {"type": "done", "content": ""}

    provider = CapturingProvider()
    agent = ZXAgentPro(
        provider, workspace,
        config=AgentConfig(
            checkpoint_mutations=False, auto_rollback=False, store_tool_memory=False),
    )
    events = [event async for event in agent.chat_stream("inspect workspace")]

    assert events[-1]["type"] == "done"
    assert provider.payloads
    assert all(sentinel not in payload for payload in provider.payloads)
    assert all("notes.md (" not in payload for payload in provider.payloads)


def test_semantic_indexer_skips_hard_link_alias(tmp_path):
    from indexing.semantic_index import SemanticIndexer

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    secret = workspace / ".env"
    secret.write_text("def HARDLINK_INDEX_SENTINEL(): pass\n", encoding="utf-8")
    try:
        os.link(secret, workspace / "notes.py")
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"hard links unavailable: {exc}")

    indexer = SemanticIndexer(workspace)
    indexer.engine.is_active = True
    indexer.engine.languages = {".py": object()}
    parsed_contents = []

    class ParsedTree:
        root_node = object()

    def parse_file(content, extension):
        parsed_contents.append(content)
        return ParsedTree()

    indexer.engine.parse_file = parse_file
    indexer._extract_symbols = lambda *args: []
    indexer.cache.is_changed = lambda path: True
    indexer.cache.update_hash = lambda path: None
    indexer.cache.save_cache = lambda: None

    indexed = indexer.scan_workspace()

    assert "notes.py" not in {item["path"] for item in indexed}
    assert not parsed_contents


def test_symbol_overview_skips_stale_hard_link_alias(tmp_path):
    from types import SimpleNamespace

    from core import ZXAgentPro

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    secret = workspace / ".env"
    secret.write_text("def STALE_HARDLINK_SYMBOL_SENTINEL(): pass\n", encoding="utf-8")
    try:
        os.link(secret, workspace / "notes.py")
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"hard links unavailable: {exc}")

    agent = object.__new__(ZXAgentPro)
    agent.workspace = workspace
    agent.indexer = SimpleNamespace(indexed_data=[{
        "path": "notes.py",
        "symbols": [{"type": "function", "name": "STALE_HARDLINK_SYMBOL_SENTINEL"}],
    }])

    overview = agent._get_symbol_overview()

    assert "STALE_HARDLINK_SYMBOL_SENTINEL" not in overview
    assert "notes.py" not in overview
