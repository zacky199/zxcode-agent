from typing import List, Dict, Any

class ConversationSummarizer:
    """Pemampat teks untuk mencegah jendela konteks membengkak."""
    
    @staticmethod
    def summarize_block(messages: List[Dict[str, Any]]) -> str:
        """
        Dalam skala enterprise yang lebih lanjut, ini bisa memanggil LLM murah (seperti Haiku/Flash) 
        untuk meringkas. Namun untuk sekarang, kita menggunakan ekstraksi deterministik.
        """
        text_corpus = " ".join([str(m.get("content", "")) for m in messages if m.get("role") != "system"])
        
        # Batasi hingga 500 karakter sebagai ringkasan darurat (extractive summary)
        truncated = text_corpus[:500].replace('\n', ' ')
        return f"[Rolled Summary of previous events]: {truncated}..."
