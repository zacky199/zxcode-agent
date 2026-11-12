from typing import Optional, Dict, Any

from .anthropic import AnthropicProvider
from .openai_compat import OpenAICompatProvider

# Provider catalog. `type` selects the client implementation,
# `api_key_env` (optional) allows auto-discovery from environment variables.
PROVIDERS: Dict[str, Dict[str, Any]] = {
    "claude": {
        "name": "Anthropic Claude",
        "base_url": "https://api.anthropic.com/v1/messages",
        "default_model": "claude-sonnet-4-6",
        "models": ["claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
        "type": "anthropic",
        "key_hint": "sk-ant-...",
        "docs": "https://console.anthropic.com",
    },
    "lapakrouter": {
        "name": "Lapak Router (LapakVIP)",
        "base_url": "https://router.lapakvip.com/api/v1/chat/completions",
        "default_model": "lv/minimax-m2.7",
        "models": [
            "lv/qwen3-coder-next",
            "lv/claude-haiku-4.5-agentic",
            "lv/claude-haiku-4.5-thinking-agentic",
            "lv/minimax-m2.7",
            "lv/claude-sonnet-4.5-thinking-agentic",
        ],
        "type": "openai_compat",
        "key_hint": "lv-...",
        "docs": "https://router.lapakvip.com",
    },
    "nararouter": {
        "name": "NaraRouter (Multi-AI)",
        "base_url": "https://router.bynara.id/v1/chat/completions",
        "default_model": "mimo-v2.5-pro-free",
        "models": [
            "mimo-v2.5-pro-free",
            "claude-haiku-4.5",
            "deepseek-v4-flash",
            "deepseek-3.2",
            "minimax-m3",
            "mistral-large",
            "qwen3.7-max",
        ],
        "type": "openai_compat",
        "key_hint": "sk-nry-...",
        "docs": "https://router.bynara.id/docs",
    },
    "9router": {
        "name": "9 Router (Local Multi-AI)",
        "base_url": "http://localhost:20128/v1/chat/completions",
        "default_model": "mimo/mimo-v2.5-pro",
        "models": ["mimo/mimo-v2.5-pro", "mimo/mimo-v2.5"],
        "type": "openai_compat",
        "key_hint": "bebas/dummy",
        "docs": "http://localhost:20128",
    },
    "deepseek": {
        "name": "DeepSeek V4",
        "base_url": "https://api.deepseek.com/v1/chat/completions",
        "default_model": "deepseek-v4-flash",
        "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
        "type": "openai_compat",
        "key_hint": "sk-...",
        "docs": "https://platform.deepseek.com",
    },
    "openai": {
        "name": "OpenAI / ChatGPT",
        "base_url": "https://api.openai.com/v1/chat/completions",
        "default_model": "gpt-4o",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o1", "o3-mini"],
        "type": "openai_compat",
        "key_hint": "sk-...",
        "docs": "https://platform.openai.com",
    },
    "grok": {
        "name": "Grok (xAI)",
        "base_url": "https://api.x.ai/v1/chat/completions",
        "default_model": "grok-3",
        "models": ["grok-3", "grok-3-mini", "grok-2"],
        "type": "openai_compat",
        "key_hint": "xai-...",
        "docs": "https://console.x.ai",
    },
    "minimax": {
        "name": "MiniMax",
        "base_url": "https://api.minimax.chat/v1/text/chatcompletion_v2",
        "default_model": "MiniMax-Text-01",
        "models": ["MiniMax-Text-01", "abab6.5s-chat"],
        "type": "openai_compat",
        "key_hint": "eyJ...",
        "docs": "https://platform.minimaxi.com",
    },
    "gemini": {
        "name": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "default_model": "gemini-2.0-flash",
        "models": ["gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash"],
        "type": "openai_compat",
        "key_hint": "AIza...",
        "docs": "https://aistudio.google.com",
    },
    "ollama": {
        "name": "Ollama (Local/Free)",
        "base_url": "http://localhost:11434/v1/chat/completions",
        "default_model": "llama3.2",
        "models": ["llama3.2", "mistral", "qwen2.5-coder", "deepseek-r1"],
        "type": "openai_compat",
        "key_hint": "ollama",
        "docs": "https://ollama.com",
    },
    "qwen": {
        "name": "Qwen (Alibaba)",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "default_model": "qwen-plus",
        "models": ["qwen-plus", "qwen-max", "qwen-coder-plus"],
        "type": "openai_compat",
        "key_hint": "sk-...",
        "docs": "https://dashscope.aliyun.com",
    },
}


def get_provider(provider_id: str, api_key: str, model: Optional[str] = None,
                 base_url: Optional[str] = None):
    """Factory that builds the correct provider client from the catalog."""
    if provider_id not in PROVIDERS:
        raise ValueError(f"Provider '{provider_id}' tidak dikenal. "
                         f"Gunakan salah satu: {', '.join(PROVIDERS.keys())}")

    config = PROVIDERS[provider_id]
    target_model = model or config["default_model"]
    target_url = base_url or config["base_url"]

    if config["type"] == "anthropic":
        return AnthropicProvider(api_key, target_url, target_model)
    elif config["type"] == "openai_compat":
        return OpenAICompatProvider(api_key, target_url, target_model)
    else:
        raise NotImplementedError(f"Tipe provider '{config['type']}' belum diimplementasi.")


def provider_names() -> str:
    return ", ".join(PROVIDERS.keys())
