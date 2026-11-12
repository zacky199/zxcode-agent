from pathlib import Path

APP_NAME = "ZX Code"
APP_VERSION = "3.5.6"

# Directory & Path Constants
DEFAULT_CONFIG_DIR = Path.home() / ".zxagent"
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "config.json"
DEFAULT_SKILL_DIR = DEFAULT_CONFIG_DIR / "skills"
DEFAULT_PLUGIN_DIR = DEFAULT_CONFIG_DIR / "plugins"

# Built-in skills shipped with the repo (read-only).
BUILTIN_SKILL_DIR = Path(__file__).resolve().parent.parent / "skills"

# Workspace internals
CHECKPOINT_DIR_NAME = ".zx_checkpoints"

# Memory & Execution Defaults
DEFAULT_MAX_TOKENS = 120000
DEFAULT_TIMEOUT = 300.0
DEFAULT_MAX_WORKERS = 4

# Plan persistence
PLAN_DIR_NAME = ".zx_plans"
