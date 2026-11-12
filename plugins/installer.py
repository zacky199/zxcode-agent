import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PureWindowsPath
from typing import Callable, Dict, List, Tuple


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject unsafe redirect destinations before urllib issues the next request."""

    def __init__(self, validate_url, deadline=None):
        super().__init__()
        self._validate_url = validate_url
        self._deadline = deadline

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        absolute_url = urllib.parse.urljoin(req.full_url, newurl)
        self._validate_url(absolute_url)
        remaining = None if self._deadline is None else self._deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            raise TimeoutError("Plugin download exceeded the absolute deadline")
        if remaining is not None:
            req.timeout = min(getattr(req, "timeout", remaining) or remaining, remaining)
        redirected = super().redirect_request(req, fp, code, msg, headers, absolute_url)
        if redirected is not None and remaining is not None:
            redirected.timeout = req.timeout
        return redirected


class UniversalInstaller:
    """Install bounded Markdown archives from GitHub/GitLab-hosted ZIP URLs."""

    PRIORITY_MAP = {
        "claude.md": 1,
        "agent.md": 2,
        "system.md": 3,
        "skill.md": 4,
        "readme.md": 99,
    }
    MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024
    MAX_ARCHIVE_FILES = 1000
    MAX_MEMBER_BYTES = 5 * 1024 * 1024
    MAX_TOTAL_UNCOMPRESSED_BYTES = 20 * 1024 * 1024
    MAX_COMPRESSION_RATIO = 100
    MAX_MARKDOWN_FILES = 500
    DOWNLOAD_TIMEOUT_SECONDS = 15
    DOWNLOAD_DEADLINE_SECONDS = 60
    READ_CHUNK_BYTES = 64 * 1024
    MAX_REVIEW_CONTENT_BYTES = 64 * 1024
    ALLOWED_DOWNLOAD_HOSTS = frozenset({
        "github.com", "codeload.github.com", "gitlab.com",
    })
    _SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")

    def __init__(self, plugin_dir: Path):
        self.plugin_dir = Path(plugin_dir)
        if self.plugin_dir.is_symlink():
            raise ValueError("Plugin root must not be a symlink")
        self.plugin_dir.mkdir(parents=True, exist_ok=True)
        self._plugin_root = self.plugin_dir.resolve()

    @classmethod
    def _validate_name(cls, name: str) -> str:
        if (not isinstance(name, str) or not cls._SAFE_NAME.fullmatch(name)
                or name in (".", "..")):
            raise ValueError(f"Unsafe plugin name: {name!r}")
        return name

    @staticmethod
    def _validate_https_url(url: str) -> urllib.parse.SplitResult:
        parsed = urllib.parse.urlsplit(url)
        hostname = (parsed.hostname or "").lower()
        if (parsed.scheme.lower() != "https" or not hostname
                or parsed.username or parsed.password):
            raise ValueError("Only HTTPS plugin URLs without embedded credentials are allowed")
        if hostname not in UniversalInstaller.ALLOWED_DOWNLOAD_HOSTS:
            raise ValueError(f"Unsupported plugin download host: {hostname}")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("Invalid plugin download port") from exc
        if port not in (None, 443):
            raise ValueError("Plugin downloads must use the standard HTTPS port")
        return parsed

    def _redirect_handler(self, deadline=None):
        return _SafeRedirectHandler(self._validate_https_url, deadline=deadline)

    def _open_url(self, request, timeout):
        deadline = getattr(request, "_zx_absolute_deadline", None)
        opener = urllib.request.build_opener(self._redirect_handler(deadline=deadline))
        return opener.open(request, timeout=timeout)

    @classmethod
    def _parse_repo_url(cls, url: str) -> Tuple[List[str], str]:
        if not isinstance(url, str) or not url.strip():
            raise ValueError("A valid HTTPS repository URL is required")
        parsed = cls._validate_https_url(url.strip())
        decoded_path = urllib.parse.unquote(parsed.path)
        if "\\" in decoded_path or any(part in (".", "..") for part in decoded_path.split("/")):
            raise ValueError("Plugin URL path traversal is not allowed")
        path_parts = [part for part in decoded_path.strip("/").split("/") if part]
        host = parsed.hostname.lower()
        if decoded_path.lower().endswith(".zip"):
            repo_name = cls._validate_name(Path(path_parts[-1]).stem if path_parts else "")
            return [url.strip()], repo_name
        if host == "github.com":
            if len(path_parts) != 2:
                raise ValueError("GitHub URL must identify exactly one owner and repository")
            owner, repo = path_parts
            if repo.endswith(".git"):
                repo = repo[:-4]
            repo_name = cls._validate_name(repo)
            cls._validate_name(owner)
            return [
                f"https://github.com/{owner}/{repo}/archive/refs/heads/main.zip",
                f"https://github.com/{owner}/{repo}/archive/refs/heads/master.zip",
            ], repo_name
        if host == "gitlab.com":
            if len(path_parts) != 2:
                raise ValueError("GitLab URL must identify exactly one owner and repository")
            owner, repo = path_parts
            repo = repo[:-4] if repo.endswith(".git") else repo
            repo_name = cls._validate_name(repo)
            cls._validate_name(owner)
            return [
                f"https://gitlab.com/{owner}/{repo}/-/archive/main/{repo}-main.zip",
                f"https://gitlab.com/{owner}/{repo}/-/archive/master/{repo}-master.zip",
            ], repo_name

        raise ValueError("URL must be a GitHub/GitLab repository or a direct HTTPS ZIP")

    def _target_plugin_dir(self, repo_name: str) -> Path:
        name = self._validate_name(repo_name)
        target = self.plugin_dir / name
        if target.is_symlink():
            raise ValueError(f"Plugin destination may not be a symlink: {name}")
        try:
            resolved = target.resolve(strict=False)
            resolved.relative_to(self._plugin_root)
        except (OSError, ValueError) as exc:
            raise ValueError(f"Plugin destination escapes plugin root: {name}") from exc
        if target.exists() and not target.is_dir():
            raise ValueError(f"Plugin destination is not a directory: {name}")
        return target

    @staticmethod
    def _validate_archive_name(info: zipfile.ZipInfo) -> Path | None:
        name = info.filename
        if not name or "\\" in name:
            raise ValueError(f"Unsafe archive path: {name!r}")
        windows_path = PureWindowsPath(name)
        if name.startswith("/") or windows_path.is_absolute() or windows_path.drive:
            raise ValueError(f"Absolute archive path is not allowed: {name!r}")
        parts = name[:-1].split("/") if info.is_dir() and name.endswith("/") else name.split("/")
        if not parts or any(part in ("", ".", "..") or ":" in part for part in parts):
            raise ValueError(f"Archive path traversal is not allowed: {name!r}")
        mode = info.external_attr >> 16
        kind = stat.S_IFMT(mode)
        if kind and kind not in (stat.S_IFREG, stat.S_IFDIR):
            raise ValueError(f"Archive contains an unsupported special file: {name!r}")
        return None if info.is_dir() else Path(*parts)

    def _validated_archive(self, archive: zipfile.ZipFile) -> Dict[Path, zipfile.ZipInfo]:
        infos = archive.infolist()
        if len(infos) > self.MAX_ARCHIVE_FILES:
            raise ValueError("Plugin archive has too many entries")
        entries: Dict[Path, zipfile.ZipInfo] = {}
        total_size = 0
        for info in infos:
            relative = self._validate_archive_name(info)
            if relative is None:
                continue
            if info.file_size < 0 or info.file_size > self.MAX_MEMBER_BYTES:
                raise ValueError(f"Plugin archive member is too large: {info.filename}")
            if info.file_size > max(info.compress_size, 1) * self.MAX_COMPRESSION_RATIO:
                raise ValueError(f"Plugin archive member exceeds the compression ratio limit: {info.filename}")
            total_size += info.file_size
            if total_size > self.MAX_TOTAL_UNCOMPRESSED_BYTES:
                raise ValueError("Plugin archive expands beyond the allowed size")
            if relative in entries:
                raise ValueError(f"Duplicate plugin archive path: {relative}")
            entries[relative] = info

        paths = set(entries)
        for relative in paths:
            for parent in relative.parents:
                if parent != Path(".") and parent in paths:
                    raise ValueError(f"Conflicting plugin archive paths: {parent} and {relative}")
        return entries

    @staticmethod
    def _set_response_timeout(response, timeout: float) -> None:
        file_pointer = getattr(response, "fp", None)
        raw = getattr(file_pointer, "raw", None)
        sock = getattr(raw, "_sock", None)
        if sock is not None:
            sock.settimeout(max(0.01, timeout))

    def _download(self, url: str, destination: Path, deadline: float = None) -> None:
        destination = Path(destination)
        deadline = deadline if deadline is not None else time.monotonic() + self.DOWNLOAD_DEADLINE_SECONDS
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Plugin download exceeded the absolute deadline")
        request = urllib.request.Request(url, headers={"User-Agent": "ZXAgent-PluginInstaller/1.0"})
        request._zx_absolute_deadline = deadline
        total = 0
        fd, temporary_name = tempfile.mkstemp(prefix=".download-", dir=destination.parent)
        try:
            with os.fdopen(fd, "wb") as output:
                with self._open_url(request, timeout=min(self.DOWNLOAD_TIMEOUT_SECONDS, remaining)) as response:
                    final_url = response.geturl() if hasattr(response, "geturl") else url
                    self._validate_https_url(final_url)
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError("Plugin download exceeded the absolute deadline")
                        self._set_response_timeout(response, min(self.DOWNLOAD_TIMEOUT_SECONDS, remaining))
                        chunk = response.read(self.READ_CHUNK_BYTES)
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Plugin download exceeded the absolute deadline")
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > self.MAX_DOWNLOAD_BYTES:
                            raise ValueError("Plugin download exceeds the allowed size")
                        output.write(chunk)
            os.replace(temporary_name, destination)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

    def _extract_validated(self, archive: zipfile.ZipFile, entries: Dict[Path, zipfile.ZipInfo],
                           extract_dir: Path) -> None:
        root = extract_dir.resolve()
        for info in archive.infolist():
            if info.is_dir():
                relative = self._validate_archive_name(info)
                # Directory paths are validated even though they are not returned as file entries.
                directory_parts = info.filename[:-1].split("/") if info.filename.endswith("/") else info.filename.split("/")
                target_dir = extract_dir.joinpath(*directory_parts)
                target_dir.mkdir(parents=True, exist_ok=True)
                target_dir.resolve().relative_to(root)
                continue
            relative = self._validate_archive_name(info)
            if relative not in entries:
                continue
            target = extract_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.parent.resolve().relative_to(root)
            if target.exists() or target.is_symlink():
                raise ValueError(f"Duplicate extraction target: {relative}")
            with archive.open(info) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)

    @staticmethod
    def _rank_markdown_file(filepath: Path, extract_dir: Path) -> int:
        filename = filepath.name.lower()
        rel_path = filepath.relative_to(extract_dir).parts
        if filename in UniversalInstaller.PRIORITY_MAP:
            return UniversalInstaller.PRIORITY_MAP[filename]
        if "prompts" in rel_path or "skill" in rel_path:
            return 10
        if "docs" in rel_path:
            return 20
        return 50

    def install_from_url(self, url: str,
                         approval_callback: Callable[[str], bool] = None) -> str:
        zip_urls, repo_name = self._parse_repo_url(url)
        target_plugin_dir = self._target_plugin_dir(repo_name)
        source = urllib.parse.urlsplit(url)
        safe_source_url = urllib.parse.urlunsplit((source.scheme, source.netloc, source.path, "", ""))

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            zip_path = temp_path / "repo.zip"
            extract_path = temp_path / "extracted"
            downloaded = False
            last_error = None
            download_deadline = time.monotonic() + self.DOWNLOAD_DEADLINE_SECONDS
            for zip_url in zip_urls:
                try:
                    self._download(zip_url, zip_path, deadline=download_deadline)
                    downloaded = True
                    break
                except urllib.error.HTTPError as exc:
                    last_error = exc
                    continue
                except urllib.error.URLError as exc:
                    last_error = exc
                    continue
            if not downloaded:
                raise ConnectionError(f"Gagal mengunduh ZIP dari repositori: {safe_source_url}") from last_error

            extract_path.mkdir()
            with zipfile.ZipFile(zip_path, "r") as archive:
                entries = self._validated_archive(archive)
                self._extract_validated(archive, entries, extract_path)

            markdown_files = []
            for root, _, files in os.walk(extract_path, followlinks=False):
                for filename in files:
                    filepath = Path(root) / filename
                    if filepath.is_symlink() or not filepath.is_file():
                        continue
                    if filepath.suffix.lower() == ".md":
                        markdown_files.append((self._rank_markdown_file(filepath, extract_path), filepath))
                        if len(markdown_files) > self.MAX_MARKDOWN_FILES:
                            raise ValueError("Plugin archive has too many Markdown files")
            if not markdown_files:
                raise FileNotFoundError("Tidak ditemukan file Markdown (.md) di dalam repository ini.")

            markdown_files.sort(key=lambda item: (item[0], str(item[1])))
            pieces = [f"--- AUTOMATICALLY ASSEMBLED SKILL: {repo_name} ---\n"]
            assembled_files = []
            for _, filepath in markdown_files:
                try:
                    content = filepath.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    continue
                pieces.append(f"\n\n=== SOURCE: {filepath.name} ===\n{content}")
                assembled_files.append(filepath.name)
            if not assembled_files:
                raise FileNotFoundError("Tidak ada file Markdown UTF-8 yang dapat dibaca.")

            target_plugin_dir = self._target_plugin_dir(repo_name)
            prompt_file = target_plugin_dir / "prompt.md"
            manifest_file = target_plugin_dir / "manifest.json"
            for output_file in (prompt_file, manifest_file):
                if output_file.is_symlink():
                    raise ValueError(f"Plugin output file may not be a symlink: {output_file.name}")
                try:
                    output_file.resolve(strict=False).relative_to(target_plugin_dir.resolve())
                except (OSError, ValueError) as exc:
                    raise ValueError(f"Plugin output escapes destination: {output_file.name}") from exc

            assembled_content = "".join(pieces)
            encoded_content = assembled_content.encode("utf-8")
            if len(encoded_content) > self.MAX_REVIEW_CONTENT_BYTES:
                raise ValueError("Assembled skill exceeds the complete preview size limit")
            if approval_callback is None:
                raise PermissionError("Explicit user approval is required before saving a skill")
            content_digest = hashlib.sha256(encoded_content).hexdigest()
            preview = (
                f"Skill installation review\nName: {repo_name}\nSource: {safe_source_url}\n"
                f"Assembled Markdown SHA-256: {content_digest}\n"
                "The complete content below will be saved as the skill prompt:\n"
                f"--- BEGIN SKILL CONTENT ---\n{assembled_content}\n--- END SKILL CONTENT ---"
            )
            if approval_callback(preview) is not True:
                raise PermissionError("Skill installation was not approved")

            manifest = {
                "name": repo_name,
                "source_url": safe_source_url,
                "entry_point": "prompt.md",
                "assembled_files": assembled_files,
                "status": "installed",
            }
            target_plugin_dir = self._target_plugin_dir(repo_name)
            target_plugin_dir.mkdir(parents=True, exist_ok=True)
            self._atomic_write_text(prompt_file, "".join(pieces))
            self._atomic_write_text(manifest_file, json.dumps(manifest, indent=2))
            return f"Plugin '{repo_name}' berhasil diinstal! Dirakit dari {len(assembled_files)} file Markdown."

    @staticmethod
    def _atomic_write_text(output_path: Path, content: str) -> None:
        fd, temporary_path = tempfile.mkstemp(prefix=f".{output_path.name}-", dir=output_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_path, output_path)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
            raise
