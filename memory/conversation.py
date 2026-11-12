from typing import List, Dict, Any
from utils.tokenizer import TokenizerUtils
from .summarizer import ConversationSummarizer

class ConversationMemory:
    """Mengelola riwayat percakapan dengan mekanisme sliding window dan peringkasan otomatis."""
    
    def __init__(self, max_tokens: int = 120000, model: str = "gpt-4o"):
        self.max_tokens = max_tokens
        self.tokenizer = TokenizerUtils(model)
        self.history: List[Dict[str, Any]] = []

    def add_message(self, role: str, content: str) -> None:
        self.history.append({"role": role, "content": content})

    def get_total_tokens(self) -> int:
        return sum(self.tokenizer.count_tokens(str(msg.get("content", ""))) for msg in self.history)

    def trim_history(self) -> None:
        """Jika batas token terlampaui, kompres pesan lama menjadi satu ringkasan sistem."""
        total = self.get_total_tokens()
        if total > self.max_tokens and len(self.history) > 6:
            recent_msgs = self.history[-10:]
            older_msgs = self.history[:-10]
            
            # Buat ringkasan menggunakan summarizer
            summary_text = ConversationSummarizer.summarize_block(older_msgs)
            summary_msg = {"role": "system", "content": summary_text}
            
            # Gabungkan ringkasan dengan pesan terbaru
            self.history = [summary_msg] + recent_msgs
