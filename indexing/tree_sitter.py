from typing import Any

try:
    from tree_sitter import Language, Parser
    HAS_TREESITTER = True
except ImportError:
    HAS_TREESITTER = False

class TreeSitterEngine:
    """Wrapper untuk Tree-sitter dengan Graceful Degradation."""
    
    def __init__(self):
        self.is_active = HAS_TREESITTER
        self.languages = {}
        self.parser = None

        if self.is_active:
            self.parser = Parser()
            self._load_available_languages()

    def _load_available_languages(self) -> None:
        # Load Python
        try:
            import tree_sitter_python as tspython
            self.languages[".py"] = Language(tspython.language())
        except ImportError: pass

        # Load JavaScript
        try:
            import tree_sitter_javascript as tsjavascript
            self.languages[".js"] = Language(tsjavascript.language())
            self.languages[".jsx"] = Language(tsjavascript.language())
        except ImportError: pass

        # Load TypeScript
        try:
            import tree_sitter_typescript as tstypescript
            self.languages[".ts"] = Language(tstypescript.language_typescript())
            self.languages[".tsx"] = Language(tstypescript.language_tsx())
        except ImportError: pass

    def parse_file(self, content: bytes, extension: str) -> Any:
        """Mengembalikan objek AST jika bahasa didukung."""
        if not self.is_active or extension not in self.languages:
            return None
        self.parser.language = self.languages[extension]
        return self.parser.parse(content)
