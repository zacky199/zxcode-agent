from typing import Dict, Any

class MCPRegistry:
    """Menyimpan daftar tool yang disediakan oleh server MCP eksternal."""
    def __init__(self):
        self.registered_tools: Dict[str, Dict[str, Any]] = {}

    def register_tool(self, name: str, schema: Dict[str, Any]) -> None:
        self.registered_tools[name] = schema

    def get_tool(self, name: str) -> Dict[str, Any]:
        return self.registered_tools.get(name, {})
