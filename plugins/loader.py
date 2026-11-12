import hashlib
import importlib.util
import logging
from pathlib import Path
from typing import Callable, List

from .registry import PluginRegistry
from .sdk import BasePlugin, PluginContext

log = logging.getLogger("zx.plugins")


class PluginLoadError(RuntimeError):
    """Raised when a plugin directory cannot be loaded transactionally."""


class PluginLoader:
    """Load Python plugins only after an explicit review of the exact source bytes."""

    MAX_REVIEWED_SOURCE_BYTES = 32 * 1024

    def __init__(self, registry: PluginRegistry, ctx: PluginContext = None,
                 approval_callback: Callable[[str], bool] = None):
        self.registry = registry
        self.ctx = ctx or PluginContext()
        self.approval_callback = approval_callback

    def load_from_directory(self, plugin_dir: Path) -> List[str]:
        plugin_dir = Path(plugin_dir)
        if not plugin_dir.exists():
            return []
        if not plugin_dir.is_dir() or plugin_dir.is_symlink():
            raise PluginLoadError(f"Plugin directory must be a real directory: {plugin_dir}")

        root = plugin_dir.resolve()
        initial_names = {plugin.display_name for plugin in self.registry.all()}
        loaded: List[str] = []
        try:
            for file_path in sorted(plugin_dir.glob("*.py")):
                if file_path.name.startswith("_"):
                    continue
                if file_path.is_symlink():
                    raise ValueError(f"Plugin module may not be a symlink: {file_path.name}")
                resolved = file_path.resolve()
                resolved.relative_to(root)
                module_name = f"zx_plugin_{file_path.stem}"
                spec = importlib.util.spec_from_file_location(module_name, resolved)
                if not spec or not spec.loader:
                    raise ImportError(f"Could not load plugin module {file_path.name}")
                source_bytes = resolved.read_bytes()
                if len(source_bytes) > self.MAX_REVIEWED_SOURCE_BYTES:
                    raise ValueError(f"Plugin source exceeds the reviewable size limit: {file_path.name}")
                try:
                    source_text = source_bytes.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise ValueError(f"Plugin source must be UTF-8 for review: {file_path.name}") from exc
                source_digest = hashlib.sha256(source_bytes).hexdigest()
                preview = (
                    f"Python plugin activation review\nFile: {resolved}\n"
                    f"SHA-256: {source_digest}\n"
                    "The following complete source file will be activated for this session:\n"
                    f"--- BEGIN SOURCE ---\n{source_text}\n--- END SOURCE ---"
                )
                if self.approval_callback is None or self.approval_callback(preview) is not True:
                    raise PermissionError("Explicit plugin approval is required")
                module = importlib.util.module_from_spec(spec)
                # Compile the exact bytes shown to the reviewer; don't re-read a changed file.
                exec(compile(source_bytes, str(resolved), "exec"), module.__dict__)
                registered = self._register_plugins_from_module(module)
                if registered:
                    loaded.append(file_path.stem)
        except Exception as exc:
            self.registry.rollback_to(initial_names)
            log.error("plugin directory load failed closed for %s: %s", plugin_dir, exc)
            raise PluginLoadError(f"Plugin directory rejected: {plugin_dir}: {exc}") from exc
        return loaded

    def _register_plugins_from_module(self, module) -> int:
        count = 0
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (isinstance(attr, type)
                    and attr.__module__ == module.__name__
                    and issubclass(attr, BasePlugin)
                    and attr is not BasePlugin):
                instance = attr()
                self.registry.register(instance, ctx=self.ctx)
                log.info("loaded plugin %s", instance.display_name)
                count += 1
        return count
