from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional


@dataclass
class ProviderInfo:
    name: str
    base_url: str
    default_model: str
    models: List[str]
    type: str
    key_hint: str
    docs: str


@dataclass
class AppConfig:
    provider: str = "claude"
    model: str = "claude-sonnet-4-6"
    api_key: str = ""
    base_url: str = ""
    default_folder: Optional[str] = None
    active_skill_name: Optional[str] = None
    active_skill_content: Optional[str] = None
    theme: str = "aurora"
    # Multi-AI routing: {role: {"provider": ..., "model": ..., "api_key": ...}}
    multi_ai: Dict[str, Dict[str, str]] = field(default_factory=dict)
    auto_confirm_dangerous: bool = False


@dataclass
class AgentResponse:
    text: str
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0
