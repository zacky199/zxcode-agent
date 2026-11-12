from typing import Dict, Any

class RepositoryGraph:
    """Membangun representasi grafikal dari repositori berdasarkan hasil semantic indexing."""
    
    def __init__(self, indexer: Any):
        # Indexer adalah instance dari SemanticIndexer yang kita buat di Poin 1
        self.indexer = indexer

    def generate_overview(self) -> str:
        """Menghasilkan overview struktur proyek beserta simbol penting untuk disuntikkan ke LLM."""
        indexed_files = getattr(self.indexer, 'indexed_data', [])
        
        if not indexed_files:
            return "### REPOSITORY SYMBOL GRAPH ###\n(Tidak ada file yang terindeks atau Tree-sitter tidak aktif.)\n"

        graph_output = "### REPOSITORY SYMBOL GRAPH ###\n"
        graph_output += "Berikut adalah peta struktur file proyek beserta simbol (Fungsi/Kelas) di dalamnya:\n"
        graph_output += "Gunakan peta ini untuk menemukan letak kode yang tepat sebelum mengedit.\n\n"

        # 1. Kelompokkan path linear menjadi struktur bersarang (Tree/Hierarchy)
        tree_structure: Dict[str, Any] = {}
        for item in indexed_files:
            path = item.get("path", "")
            parts = path.split("/")
            current = tree_structure
            
            # Buat direktori
            for part in parts[:-1]:
                if part not in current:
                    current[part] = {}
                current = current[part]
            
            # Masukkan data file di ujung daun (leaf)
            current[parts[-1]] = item

        # 2. Fungsi rekursif untuk merender struktur Tree menjadi teks ASCII
        def render_tree(node: Dict[str, Any], prefix: str = "") -> str:
            result = ""
            items = list(node.items())
            
            for i, (key, value) in enumerate(items):
                is_last = (i == len(items) - 1)
                connector = "└── " if is_last else "├── "
                
                # Jika value adalah dictionary dan tidak punya key 'ext', berarti ini Folder
                if isinstance(value, dict) and "ext" not in value:
                    result += f"{prefix}{connector}📁 {key}/\n"
                    extension = "    " if is_last else "│   "
                    result += render_tree(value, prefix + extension)
                else: 
                    # Ini adalah File
                    result += f"{prefix}{connector}📄 {key}\n"
                    
                    # Ekstrak simbol di dalam file (jika ada)
                    symbols = value.get("symbols", [])
                    if symbols:
                        sym_prefix = prefix + ("    " if is_last else "│   ")
                        for j, sym in enumerate(symbols):
                            sym_is_last = (j == len(symbols) - 1)
                            sym_connector = "└── " if sym_is_last else "├── "
                            
                            # Ikon pembeda: Kotak untuk Kelas, Petir untuk Fungsi
                            icon = "📦" if sym.get("type") == "class" else "⚡"
                            result += f"{sym_prefix}{sym_connector}{icon} {sym.get('name')}\n"
            
            return result

        graph_output += render_tree(tree_structure)
        return graph_output
