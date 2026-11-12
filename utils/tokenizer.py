try:
    import tiktoken
    HAS_TIKTOKEN = True
except ImportError:
    HAS_TIKTOKEN = False

class TokenizerUtils:
    def __init__(self, model_name: str = "gpt-4o"):
        self.model_name = model_name
        self.encoding = None
        
        if HAS_TIKTOKEN:
            try:
                self.encoding = tiktoken.encoding_for_model(model_name)
            except Exception:
                try:
                    self.encoding = tiktoken.get_encoding("cl100k_base")
                except Exception:
                    self.encoding = None

    def count_tokens(self, text: str) -> int:
        if not text:
            return 0
        if self.encoding is not None:
            try:
                return len(self.encoding.encode(text))
            except Exception:
                pass
        return len(text) // 4
