import httpx
from typing import Dict, Any, Optional

class MCPClient:
    """Klien protokol MCP untuk berkomunikasi dengan server eksternal."""
    def __init__(self, server_url: str):
        self.server_url = server_url

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self.server_url}/tools/{tool_name}",
                    json=arguments
                )
                if response.status_code == 200:
                    return response.json()
        except Exception:
            pass
        return None
