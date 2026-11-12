import os
from pathlib import Path
from typing import List, Dict, Any, Optional
from .tree_sitter import TreeSitterEngine
from .cache import IndexCache
from utils.filesystem import FileSystemUtils
from utils.sensitive_files import is_sensitive_path

class SemanticIndexer:
    """Memindai file dalam workspace, mengekstrak simbol (Fungsi/Kelas) via Tree-sitter, 
    dan memanfaatkan cache untuk update inkremental secepat kilat."""
    
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.engine = TreeSitterEngine()
        self.cache = IndexCache(workspace)
        
        # Daftar direktori sampah yang wajib diabaikan agar scanning tidak lemot
        self.ignore_dirs = {
            '.git', 'node_modules', '__pycache__', 'venv', 'env', 
            'dist', 'build', '.zx_checkpoints', '.next', '.nuxt', 
            '.cache', 'coverage', 'out', 'tmp'
        }
        self.indexed_data: List[Dict[str, Any]] = []

    def scan_workspace(self) -> List[Dict[str, Any]]:
        """Mengekstrak informasi file valid beserta simbol kodenya secara inkremental."""
        if not self.engine.is_active or not self.engine.languages:
            return []

        has_changes = False
        valid_files_snapshot = []

        for root, dirs, files in os.walk(self.workspace):
            # Abaikan direktori yang masuk daftar hitam & folder tersembunyi
            dirs[:] = [
                d for d in dirs 
                if d not in self.ignore_dirs and not d.startswith('.')
                and not (Path(root) / d).is_symlink()
                and not is_sensitive_path(Path(root) / d)
            ]
            
            for file in files:
                filepath = Path(root) / file
                if (filepath.is_symlink() or is_sensitive_path(filepath)
                        or FileSystemUtils.is_hard_linked_regular_file(filepath)):
                    continue
                try:
                    resolved = filepath.resolve()
                    resolved.relative_to(Path(self.workspace).resolve())
                except (OSError, ValueError):
                    continue
                if is_sensitive_path(resolved):
                    continue
                ext = filepath.suffix.lower()
                
                # Batasi file berukuran < 300KB yang bahasanya didukung Tree-sitter
                if ext in self.engine.languages and filepath.stat().st_size < 300000:
                    rel_path = str(filepath.relative_to(self.workspace))
                    valid_files_snapshot.append(rel_path)
                    
                    # INCREMENTAL UPDATE LOGIC (Hanya diproses jika file berubah)
                    if self.cache.is_changed(filepath):
                        try:
                            content = filepath.read_bytes()
                            tree = self.engine.parse_file(content, ext)
                            if tree:
                                # Ekstrak simbol (Fungsi dan Kelas) dari AST
                                symbols = self._extract_symbols(tree.root_node, content, ext)
                                
                                # Update data di indeks memori
                                self._update_memory_index(rel_path, ext, tree.root_node.type, symbols)
                                self.cache.update_hash(filepath)
                                has_changes = True
                        except Exception:
                            pass

        # Cleanup: Hapus file dari memori indeks jika sudah dihapus dari workspace
        self.indexed_data = [d for d in self.indexed_data if d["path"] in valid_files_snapshot]
        
        # Simpan cache ke disk hanya jika ada perubahan fisik file
        if has_changes:
            self.cache.save_cache()
            
        return self.indexed_data

    def _extract_symbols(self, root_node: Any, content: bytes, ext: str) -> List[Dict[str, str]]:
        """Mengekstrak nama fungsi dan kelas secara cerdas menggunakan Tree-sitter."""
        symbols = []
        
        # Query sederhana untuk mendeteksi nama fungsi/kelas berdasarkan bahasa
        # Umumnya node AST untuk fungsi/kelas mengandung kata 'function', 'method', atau 'class'
        target_types = {'function_definition', 'class_definition', 'method_definition', 'function_declaration', 'class_declaration'}
        
        def traverse(node):
            if node.type in target_types:
                # Cari sub-node yang memuat nama identifier
                name_node = self._find_identifier_node(node)
                if name_node:
                    name = content[name_node.start_byte:name_node.end_byte].decode('utf-8', errors='ignore')
                    symbols.append({
                        "type": "class" if "class" in node.type else "function",
                        "name": name
                    })
            for child in node.children:
                traverse(child)

        try:
            traverse(root_node)
        except Exception:
            pass
            
        return symbols

    def _find_identifier_node(self, node: Any) -> Optional[Any]:
        """Mencari node identifier yang merepresentasikan nama fungsi/kelas."""
        if node.type in {'identifier', 'name', 'type_identifier'}:
            return node
        for child in node.children:
            result = self._find_identifier_node(child)
            if result:
                return result
        return None

    def _update_memory_index(self, path: str, ext: str, root_type: str, symbols: List[Dict[str, str]]) -> None:
        """Memperbarui atau menambah entri data indeks beserta simbolnya di memori."""
        for item in self.indexed_data:
            if item["path"] == path:
                item["ext"] = ext
                item["root_type"] = root_type
                item["symbols"] = symbols
                return
                
        self.indexed_data.append({
            "path": path,
            "ext": ext,
            "root_type": root_type,
            "symbols": symbols
        })
