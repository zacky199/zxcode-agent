from enum import Enum
from typing import Optional

class AgentState(Enum):
    IDLE = "idle"
    PLANNING = "planning"
    EXECUTING = "executing"
    AWAITING_USER = "awaiting_user"
    ERROR = "error"

class AgentLifecycle:
    """State Machine (Mesin Status) untuk mengontrol siklus hidup agen."""
    def __init__(self):
        self.state: AgentState = AgentState.IDLE
        self.current_task: Optional[str] = None

    def transition_to(self, new_state: AgentState) -> None:
        self.state = new_state

    def is_busy(self) -> bool:
        return self.state in (AgentState.PLANNING, AgentState.EXECUTING)
