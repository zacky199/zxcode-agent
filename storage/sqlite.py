import sqlite3
import threading
from pathlib import Path
from typing import List, Dict, Any, Optional

class SQLiteClient:
    """Manajer koneksi SQLite yang thread-safe untuk penyimpanan lokal."""
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()

    @property
    def connection(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn"):
            self._local.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def execute(self, query: str, parameters: tuple = ()) -> int:
        cursor = self.connection.cursor()
        cursor.execute(query, parameters)
        self.connection.commit()
        return cursor.lastrowid or 0

    def fetch_all(self, query: str, parameters: tuple = ()) -> List[Dict[str, Any]]:
        cursor = self.connection.cursor()
        cursor.execute(query, parameters)
        return [dict(row) for row in cursor.fetchall()]

    def fetch_one(self, query: str, parameters: tuple = ()) -> Optional[Dict[str, Any]]:
        cursor = self.connection.cursor()
        cursor.execute(query, parameters)
        row = cursor.fetchone()
        return dict(row) if row else None

    def close(self):
        if hasattr(self._local, "conn"):
            self._local.conn.close()
            del self._local.conn
