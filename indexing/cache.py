import json
import hashlib
from pathlib import Path
from typing import Dict
from config.constants import CHECKPOINT_DIR_NAME

class IndexCache:
    """
    Sistem cache persisten berbasis Hash MD5. 
    Memastikan re-indexing hanya terjadi pada file yang mengalami modifikasi.
    """
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.cache_dir = workspace / CHECKPOINT_DIR_NAME
        self.cache_file = self.cache_dir / "index_cache.json"
        self.file_hashes: Dict[str, str] = self._load_cache()
        
    def _load_cache(self) -> Dict[str, str]:
        if self.cache_file.exists():
            try:
                return json.loads(self.cache_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def save_cache(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.cache_file.write_text(json.dumps(self.file_hashes, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _compute_hash(self, filepath: Path) -> str:
        hasher = hashlib.md5()
        try:
            with open(filepath, 'rb') as f:
                buf = f.read(65536)
                while len(buf) > 0:
                    hasher.update(buf)
                    buf = f.read(65536)
            return hasher.hexdigest()
        except Exception:
            return ""

    def is_changed(self, filepath: Path) -> bool:
        """Mengembalikan True jika file baru atau isinya berubah (berdasarkan hash)."""
        rel_path = str(filepath.relative_to(self.workspace))
        current_hash = self._compute_hash(filepath)
        
        if current_hash == "":
            return False # Error membaca file (misal: permission denied)
            
        if rel_path not in self.file_hashes or self.file_hashes[rel_path] != current_hash:
            return True
            
        return False
        
    def update_hash(self, filepath: Path) -> None:
        """Memperbarui hash di memori setelah file sukses di-index."""
        rel_path = str(filepath.relative_to(self.workspace))
        new_hash = self._compute_hash(filepath)
        if new_hash:
            self.file_hashes[rel_path] = new_hash
