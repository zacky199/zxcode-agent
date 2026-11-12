from typing import Callable, Awaitable
from .task_graph import TaskNode

class PlanExecutor:
    """Mengeksekusi node tunggal menggunakan callback ke Core Agent."""
    def __init__(self, action_callback: Callable[[str], Awaitable[str]]):
        self.action_callback = action_callback

    async def execute_node(self, node: TaskNode) -> str:
        try:
            # Memanggil fungsi stream LLM di core agent
            result = await self.action_callback(node.description)
            return result
        except Exception as e:
            return f"SYSTEM_ERROR: Failed to execute task. Details: {str(e)}"
