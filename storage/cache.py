import time
from typing import Any, Dict, Optional

class KeyValueCache:
    """Sistem in-memory cache sederhana dengan dukungan TTL (Time-To-Live)."""
    def __init__(self):
        self._store: Dict[str, Dict[str, Any]] = {}

    def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
        expires_at = time.time() + ttl_seconds if ttl_seconds else None
        self._store[key] = {"value": value, "expires_at": expires_at}

    def get(self, key: str) -> Optional[Any]:
        if key not in self._store:
            return None
            
        record = self._store[key]
        if record["expires_at"] and time.time() > record["expires_at"]:
            del self._store[key]
            return None
            
        return record["value"]

    def delete(self, key: str) -> None:
        if key in self._store:
            del self._store[key]

    def clear(self) -> None:
        self._store.clear()
