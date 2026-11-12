import json
import shutil
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import List, Dict, Optional


class PluginManager:
    """
    Administrator for the skill/plugin ecosystem.

    Scans both the user-installed plugin directory (``~/.zxagent/skills``) and
    the built-in skills shipped with the repo, merging them into one catalog.
    """

    def __init__(self, plugin_dir: Path, builtin_dir: Optional[Path] = None):
        self.plugin_dir = plugin_dir
        self.builtin_dir = builtin_dir
        self.plugin_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    def _scan_dir(self, base_dir: Path, mark_builtin: bool) -> List[Dict]:
        plugins = []
        if not base_dir.exists() or base_dir.is_symlink() or not base_dir.is_dir():
            return plugins
        for item in sorted(base_dir.iterdir()):
            if item.is_symlink() or not item.is_dir():
                continue
            manifest_path = item / "manifest.json"
            if manifest_path.is_symlink():
                continue
            if manifest_path.is_file():
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    if not isinstance(manifest, dict):
                        continue
                    manifest_path.resolve().relative_to(item.resolve())
                    manifest["_folder_name"] = item.name
                    manifest["_location"] = item
                    manifest["_builtin"] = bool(mark_builtin)
                    plugins.append(manifest)
                except json.JSONDecodeError:
                    continue
            else:
                # Allow a bare SKILL.md folder as a valid skill.
                skill_md = item / "SKILL.md"
                if skill_md.exists():
                    plugins.append({
                        "name": item.name.replace("-", " ").title(),
                        "description": "Installed skill",
                        "entry_point": "SKILL.md",
                        "_folder_name": item.name,
                        "_location": item,
                        "_builtin": bool(mark_builtin),
                    })
        return plugins

    def list_installed_plugins(self) -> List[Dict]:
        """Return all installed plugins plus built-in skills."""
        installed = self._scan_dir(self.plugin_dir, mark_builtin=False)
        if self.builtin_dir:
            installed.extend(self._scan_dir(self.builtin_dir, mark_builtin=True))
        return installed

    def get_plugin_info(self, plugin_name: str) -> Optional[Dict]:
        for plugin in self.list_installed_plugins():
            if plugin["_folder_name"] == plugin_name or plugin.get("name") == plugin_name:
                return plugin
        return None

    def get_plugin_prompt(self, plugin_name: str) -> Optional[str]:
        info = self.get_plugin_info(plugin_name)
        if not info:
            return None
        entry_point = info.get("entry_point", "prompt.md")
        if not isinstance(entry_point, str) or "\\" in entry_point:
            return None
        posix_path = PurePosixPath(entry_point)
        windows_path = PureWindowsPath(entry_point)
        if (not entry_point or posix_path.is_absolute() or windows_path.is_absolute()
                or windows_path.drive or any(part in ("", ".", "..") for part in entry_point.split("/"))):
            return None
        plugin_dir = Path(info["_location"])
        prompt_path = plugin_dir / Path(*posix_path.parts)
        if prompt_path.is_symlink() or not prompt_path.is_file():
            return None
        try:
            prompt_path.resolve().relative_to(plugin_dir.resolve())
            return prompt_path.read_text(encoding="utf-8")
        except (OSError, ValueError, UnicodeError):
            return None

    def get_plugin_description(self, plugin_name: str) -> Optional[str]:
        info = self.get_plugin_info(plugin_name)
        return info.get("description", "") if info else None

    def uninstall_plugin(self, plugin_name: str) -> bool:
        info = self.get_plugin_info(plugin_name)
        if not info:
            return False
        if info.get("_builtin"):
            return False  # built-in skills are read-only
        target_dir = info.get("_location")
        if target_dir and target_dir.exists():
            try:
                shutil.rmtree(target_dir)
                return True
            except Exception:
                return False
        return False
