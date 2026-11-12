from typing import Callable, Dict, Any

class ToolRegistry:
    """Registry tunggal untuk mendaftarkan fungsi tool primitif bagi agen."""
    def __init__(self):
        self.tools: Dict[str, Callable] = {}

    def register(self, name: str, func: Callable) -> None:
        self.tools[name] = func

    def execute(self, name: str, args: Dict[str, Any]) -> Any:
        if name in self.tools:
            return self.tools[name](**args)
        raise ValueError(f"Tool tidak ditemukan: {name}")
