"""Tests for the plugin SDK: loading, lifecycle, custom tools."""
import tempfile
from pathlib import Path

from plugins import PluginRegistry, PluginLoader, PluginContext, BasePlugin
from plugins.sdk import BasePlugin as SDKBase


def test_plugin_sdk_roundtrip(tmp_path):
    """A plugin module placed in a directory should load and register."""
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    (plugin_dir / "hello_plugin.py").write_text('''
from plugins.sdk import BasePlugin, PluginContext

class HelloPlugin(BasePlugin):
    name = "hello"
    description = "Greets people"
    version = "1.0.0"

    def initialize(self, ctx: PluginContext):
        self.ctx = ctx
        self.greeted = []

    def get_tools(self):
        return [{
            "type": "function",
            "function": {
                "name": "greet",
                "description": "Greet a name.",
                "parameters": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            },
        }]

    def execute_tool(self, name, args):
        if name == "greet":
            return f"Hello, {args.get('name')}!"
        return "?"

    def on_event(self, event):
        self.greeted.append(event.get("type"))
''')

    registry = PluginRegistry()
    ctx = PluginContext(logger=None)
    loader = PluginLoader(registry, ctx=ctx, approval_callback=lambda preview: True)
    loaded = loader.load_from_directory(plugin_dir)

    assert loaded == ["hello_plugin"]
    assert registry.get("hello") is not None
    assert registry.get("hello").version == "1.0.0"

    schemas = registry.collect_tool_schemas()
    assert len(schemas) == 1
    assert schemas[0]["function"]["name"] == "greet"

    # Tool handler works.
    plugin = registry.get("hello")
    assert plugin.execute_tool("greet", {"name": "Budi"}) == "Hello, Budi!"

    # Event dispatch reaches the plugin.
    registry.dispatch_event({"type": "tool_result", "name": "greet"})
    assert plugin.greeted == ["tool_result"]

    registry.shutdown_all()
    assert not registry.plugins


def test_plugin_tool_in_engine(tmp_path):
    """A plugin tool handler must be callable through the ToolEngine."""
    from tools import ToolEngine

    registry = PluginRegistry()
    loader = PluginLoader(registry, approval_callback=lambda preview: True)
    (tmp_path / "p.py").write_text('''
from plugins.sdk import BasePlugin

class Mini(BasePlugin):
    name = "mini"
    def get_tools(self):
        return [{"type": "function", "function": {
            "name": "shout",
            "description": "Uppercase text",
            "parameters": {"type": "object",
                           "properties": {"text": {"type": "string"}},
                           "required": ["text"]}}}]
    def execute_tool(self, name, args):
        return args.get("text", "").upper()
''')
    loader.load_from_directory(tmp_path)

    engine = ToolEngine(tmp_path)
    for schema in registry.collect_tool_schemas():
        fname = schema["function"]["name"]
        plugin = registry.get("mini")
        engine.register_plugin_tool(fname, lambda args, h=plugin.execute_tool: h(fname, args))
        engine._plugin_schemas.append(schema)

    names = {s["function"]["name"] for s in engine.get_tool_schemas()}
    assert "shout" in names

    import asyncio
    result = asyncio.run(engine.execute_tool("shout", {"text": "halo"}))
    assert result["status"] == "ok"
    assert result["output"] == "HALO"


def test_plugin_directory_load_rolls_back_on_initialization_failure(tmp_path):
    import pytest
    from plugins.loader import PluginLoadError
    from tools import ToolEngine

    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    cleanup_marker = tmp_path / "shutdown-called"
    (plugin_dir / "a_good.py").write_text(f'''
from plugins.sdk import BasePlugin
class Good(BasePlugin):
    name = "good"
    def initialize(self, ctx):
        ctx.register_tool_handler("good_tool", lambda args: "ok")
    def shutdown(self):
        open({str(cleanup_marker)!r}, "w").write("yes")
''')
    (plugin_dir / "z_bad.py").write_text('''
from plugins.sdk import BasePlugin
class Bad(BasePlugin):
    name = "bad"
    def initialize(self, ctx):
        raise RuntimeError("init failed")
''')

    registry = PluginRegistry()
    engine = ToolEngine(tmp_path)
    ctx = PluginContext(workspace=tmp_path, tool_engine=engine)
    with pytest.raises(PluginLoadError):
        PluginLoader(registry, ctx=ctx, approval_callback=lambda preview: True).load_from_directory(plugin_dir)

    assert registry.all() == []
    assert "good_tool" not in engine._plugin_tools
    assert cleanup_marker.read_text() == "yes"


def test_plugin_directory_load_fails_closed_on_import_error(tmp_path):
    import pytest
    from plugins.loader import PluginLoadError

    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    (plugin_dir / "a_good.py").write_text('''
from plugins.sdk import BasePlugin
class Good(BasePlugin):
    name = "good"
''')
    (plugin_dir / "z_broken.py").write_text("this is not valid Python !!!")

    registry = PluginRegistry()
    with pytest.raises(PluginLoadError):
        PluginLoader(registry, approval_callback=lambda preview: True).load_from_directory(plugin_dir)

    assert registry.all() == []


def test_plugin_schema_failure_is_not_silently_ignored(tmp_path):
    import pytest

    plugin = BasePlugin()
    plugin.name = "broken-schema"
    plugin.get_tools = lambda: (_ for _ in ()).throw(RuntimeError("schema failed"))
    registry = PluginRegistry()
    registry.register(plugin)

    with pytest.raises(RuntimeError, match="schema failed"):
        registry.collect_tool_schemas()


def test_plugin_event_failure_disables_plugin_and_tool(tmp_path):
    from tools import ToolEngine

    engine = ToolEngine(tmp_path)
    ctx = PluginContext(workspace=tmp_path, tool_engine=engine)
    plugin = BasePlugin()
    plugin.name = "event-broken"
    plugin.initialize = lambda context: context.register_tool_handler(
        "event_tool", lambda args: "ok")
    plugin.on_event = lambda event: (_ for _ in ()).throw(RuntimeError("event failed"))
    registry = PluginRegistry()
    registry.register(plugin, ctx=ctx)

    registry.dispatch_event({"type": "tool_result"})

    assert registry.get("event-broken") is None
    assert "event_tool" not in engine._plugin_tools


def test_workspace_plugins_are_not_auto_loaded_at_startup(tmp_path, monkeypatch):
    from config import constants
    from core import ZXAgentPro
    from tools import ToolEngine

    workspace = tmp_path / "project"
    local_plugins = workspace / ".zxagent" / "plugins"
    local_plugins.mkdir(parents=True)
    user_plugins = tmp_path / "user-plugins"
    user_plugins.mkdir()
    marker = tmp_path / "workspace-plugin-imported"
    (local_plugins / "untrusted.py").write_text(f'''\
from pathlib import Path
Path({str(marker)!r}).write_text("imported")
from plugins.sdk import BasePlugin
class WorkspacePlugin(BasePlugin):
    name = "workspace-plugin"
''')
    (user_plugins / "trusted.py").write_text('''\
from plugins.sdk import BasePlugin
class TrustedPlugin(BasePlugin):
    name = "trusted-plugin"
''')
    monkeypatch.setattr(constants, "DEFAULT_PLUGIN_DIR", user_plugins)

    agent = object.__new__(ZXAgentPro)
    agent.tool_engine = ToolEngine(workspace)
    agent.memory = None
    agent.plugin_approval_callback = lambda preview: True
    registry = agent._load_plugins(workspace)

    assert not marker.exists()
    assert registry.get("workspace-plugin") is None
    assert registry.get("trusted-plugin") is not None



def test_plugin_loader_requires_preview_approval_before_import(tmp_path):
    import hashlib
    import pytest
    from plugins.loader import PluginLoadError

    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    marker = tmp_path / "plugin-imported"
    source = f'''from pathlib import Path
Path({str(marker)!r}).write_text("imported")
from plugins.sdk import BasePlugin
class Reviewed(BasePlugin):
    name = "reviewed"
'''
    plugin_file = plugin_dir / "reviewed.py"
    plugin_file.write_text(source, encoding="utf-8")

    registry = PluginRegistry()
    with pytest.raises(PluginLoadError, match="approval"):
        PluginLoader(registry).load_from_directory(plugin_dir)
    assert not marker.exists()

    rejecting_loader = PluginLoader(registry, approval_callback=lambda preview: False)
    with pytest.raises(PluginLoadError, match="approval"):
        rejecting_loader.load_from_directory(plugin_dir)
    assert not marker.exists()

    previews = []
    loader = PluginLoader(registry, approval_callback=lambda preview: previews.append(preview) or True)
    assert loader.load_from_directory(plugin_dir) == ["reviewed"]
    assert marker.read_text(encoding="utf-8") == "imported"
    assert "Path(" in previews[0]
    assert hashlib.sha256(plugin_file.read_bytes()).hexdigest() in previews[0]
