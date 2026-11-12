"""
Plugin Ecosystem for ZX Agent Pro.
Handles downloading, assembling, and managing external agent personas/skills.
"""

from .installer import UniversalInstaller
from .manager import PluginManager
from .loader import PluginLoader
from .registry import PluginRegistry
from .sdk import BasePlugin, PluginContext

__all__ = ["UniversalInstaller", "PluginManager", "PluginLoader",
           "PluginRegistry", "BasePlugin", "PluginContext"]
