from typing import Dict, List
from .client import MCPClient
from .registry import MCPRegistry

class MCPManager:
    """Mengelola banyak koneksi server MCP secara terpusat."""
    def __init__(self):
        self.registry = MCPRegistry()
        self.clients: Dict[str, MCPClient] = {}

    def add_server(self, name: str, url: str) -> None:
        self.clients[name] = MCPClient(url)

    def get_available_tools(self) -> List[str]:
        return list(self.registry.registered_tools.keys())
