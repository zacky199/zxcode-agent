import stat
from pathlib import Path

from utils.filesystem import FileSystemUtils


class DiffEngine:
    """Precise SEARCH/REPLACE patch engine for modifying existing files."""

    def __init__(self, workspace: Path):
        self.workspace = workspace

    def _locate(self, content: str, search: str):
        """Return (start, end) of the unique search block, or (-1, -1) if ambiguous."""
        idx = content.find(search)
        if idx == -1:
            return -1, -1
        if content.find(search, idx + 1) != -1:
            return -2, -2  # multiple occurrences
        return idx, idx + len(search)

    def apply_patch(self, filepath: str, search_block: str, replace_block: str,
                    before_replace=None, retain_displaced=None,
                    parent_anchor=None) -> str:
        target_file = self.workspace / filepath

        try:
            if parent_anchor is not None:
                parent_anchor.assert_target(target_file)
                content, mode = FileSystemUtils.read_text_at(parent_anchor)
            else:
                if not target_file.exists():
                    return f"Error: File '{filepath}' tidak ditemukan di workspace."
                content = target_file.read_text(encoding="utf-8")
                mode = stat.S_IMODE(target_file.stat().st_mode) & 0o777
        except UnicodeDecodeError:
            return f"Error: File '{filepath}' tampaknya biner dan tidak bisa di-patch."
        except FileNotFoundError:
            return f"Error: File '{filepath}' tidak ditemukan di workspace."
        except Exception as e:
            return f"Error membaca file '{filepath}': {e}"

        # Normalize line endings so cross-OS searches always match.
        content_norm = content.replace("\r\n", "\n")
        search_norm = search_block.replace("\r\n", "\n").strip("\n")
        replace_norm = replace_block.replace("\r\n", "\n").strip("\n")

        if not search_norm:
            return f"Error: Blok SEARCH kosong pada '{filepath}'."

        start, end = self._locate(content_norm, search_norm)
        if start == -1:
            hint = self._closest_hint(content_norm, search_norm)
            return (
                f"Error: Gagal melakukan patch pada '{filepath}'.\n"
                f"Blok SEARCH tidak ditemukan di dalam file.\n"
                f"Petunjuk: blok yang dicari harus sama persis (termasuk indentasi).\n"
                f"{hint}"
            )
        if start == -2:
            return (
                f"Error: Patch ambigu pada '{filepath}'.\n"
                "Blok SEARCH muncul lebih dari satu kali. Tambahkan baris konteks "
                "di sekitar blok agar unik."
            )

        updated = content_norm[:start] + replace_norm + content_norm[end:]
        try:
            FileSystemUtils.atomic_write(
                target_file, updated, mode=mode, before_replace=before_replace,
                retain_displaced=retain_displaced, parent_anchor=parent_anchor)
        except Exception as e:
            return f"Error menulis file '{filepath}': {e}"

        return f"Success: Patch diterapkan pada '{filepath}'."

    def _closest_hint(self, content: str, search: str, window: int = 60) -> str:
        """Best-effort hint: find the most similar line in the file."""
        search_lines = [ln.strip() for ln in search.splitlines() if ln.strip()]
        if not search_lines:
            return ""
        target = search_lines[0]
        best_line, best_score = "", -1
        for ln in content.splitlines():
            score = self._similarity(target, ln.strip())
            if score > best_score:
                best_score, best_line = score, ln.strip()
        if best_score > 0.5:
            return f"Baris mirip terdekat:\n  {best_line[:window]}"
        return ""

    @staticmethod
    def _similarity(a: str, b: str) -> float:
        """Simple token-overlap similarity in [0, 1]."""
        if not a or not b:
            return 0.0
        a_tokens = set(a.split())
        b_tokens = set(b.split())
        if not a_tokens or not b_tokens:
            return 0.0
        return len(a_tokens & b_tokens) / max(len(a_tokens | b_tokens), 1)
