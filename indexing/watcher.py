from pathlib import Path

class FileWatcher:
    """
    Mendeteksi perubahan file di workspace secara pasif menggunakan timestamp.
    Ini digunakan untuk memberi tahu agen apakah ia perlu memicu SemanticIndexer.scan_workspace().
    """
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.last_mtime: float = 0.0
        self.ignore_dirs = {'.git', 'node_modules', '__pycache__', 'venv', '.zx_checkpoints'}

    def check_for_updates(self) -> bool:
        """
        Memeriksa st_mtime tertinggi di dalam workspace. 
        Berjalan sangat cepat karena tidak menghitung hash isi file.
        """
        latest_mtime = 0.0
        try:
            # Scan dangkal untuk efisiensi (1 level ke bawah), cocok untuk pendeteksian cepat
            for item in self.workspace.iterdir():
                if item.name in self.ignore_dirs or item.name.startswith('.'):
                    continue
                    
                mtime = item.stat().st_mtime
                if mtime > latest_mtime:
                    latest_mtime = mtime
                    
            if latest_mtime > self.last_mtime:
                self.last_mtime = latest_mtime
                return True
        except Exception:
            pass
            
        return False
