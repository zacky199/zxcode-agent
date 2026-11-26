"""Validated registry for discovered MCP tool schemas."""
from typing import Any, Dict


class MCPRegistry:
    def __init__(self):
        self.registered_tools: Dict[str, Dict[str, Any]] = {}

    def register_tool(self, name: str, schema: Dict[str, Any]) -> None:
        if not isinstance(name, str) or not name or not isinstance(schema, dict):
            raise ValueError("Tool name and schema must be valid")
        self.registered_tools[name] = schema

    def get_tool(self, name: str) -> Dict[str, Any]:
        return self.registered_tools.get(name, {})

    def remove_server(self, server_name: str) -> None:
        self.registered_tools = {
            name: schema for name, schema in self.registered_tools.items()
            if schema.get("server") != server_name
        }
