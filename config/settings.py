import json
import os
import stat
import tempfile
from pathlib import Path

from .constants import DEFAULT_CONFIG_DIR, DEFAULT_CONFIG_FILE
from .models import AppConfig


class SettingsManager:
    def __init__(self, config_path: Path = DEFAULT_CONFIG_FILE):
        self.config_path = Path(config_path)

    def _secure_existing_config(self) -> None:
        if self.config_path == DEFAULT_CONFIG_FILE and self.config_path.parent.is_symlink():
            raise OSError("Default configuration directory must not be a symlink")
        if self.config_path.is_symlink():
            raise OSError("Configuration file must not be a symlink")
        if self.config_path.exists():
            os.chmod(self.config_path, stat.S_IRUSR | stat.S_IWUSR)
        # The default config directory is dedicated to private app data.
        if self.config_path == DEFAULT_CONFIG_FILE:
            self.config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.config_path.parent, stat.S_IRWXU)

    def load(self) -> AppConfig:
        if not self.config_path.exists():
            return AppConfig()
        try:
            self._secure_existing_config()
            raw = self.config_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                return AppConfig()
            valid_keys = AppConfig.__dataclass_fields__.keys()
            filtered = {key: value for key, value in data.items() if key in valid_keys}
            return AppConfig(**filtered)
        except Exception:
            return AppConfig()

    def save(self, config: AppConfig) -> None:
        if self.config_path.is_symlink():
            raise OSError("Configuration file must not be a symlink")

        if self.config_path == DEFAULT_CONFIG_FILE:
            if self.config_path.parent.is_symlink():
                raise OSError("Default configuration directory must not be a symlink")
            self.config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.config_path.parent, stat.S_IRWXU)
        else:
            self.config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

        data = {
            "provider": config.provider,
            "model": config.model,
            "api_key": config.api_key,
            "base_url": config.base_url,
            "default_folder": config.default_folder,
            "active_skill_name": config.active_skill_name,
            "active_skill_content": config.active_skill_content,
            "theme": config.theme,
            "multi_ai": config.multi_ai,
            "auto_confirm_dangerous": config.auto_confirm_dangerous,
        }
        encoded = json.dumps(data, indent=2)
        fd, temporary_path = tempfile.mkstemp(prefix=".config-", dir=self.config_path.parent)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
            else:
                os.chmod(temporary_path, stat.S_IRUSR | stat.S_IWUSR)
            with os.fdopen(fd, "w", encoding="utf-8") as config_file:
                config_file.write(encoded)
                config_file.flush()
                os.fsync(config_file.fileno())
            os.replace(temporary_path, self.config_path)
            os.chmod(self.config_path, stat.S_IRUSR | stat.S_IWUSR)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
            raise
