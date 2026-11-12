import json
from pathlib import Path
from typing import Any, Dict, List

_MAX_MANIFEST_BYTES = 200_000

_JS_FRAMEWORKS = (
    ("next", "Next.js"), ("expo", "Expo"), ("react-native", "React Native"),
    ("react", "React"), ("vue", "Vue"), ("svelte", "Svelte"), ("@angular/core", "Angular"),
    ("express", "Express"), ("fastify", "Fastify"), ("vite", "Vite"), ("tailwindcss", "Tailwind CSS"),
)
_JS_LOCKFILES = (
    ("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"), ("bun.lockb", "bun"),
    ("bun.lock", "bun"), ("package-lock.json", "npm"),
)
_SCRIPT_PRIORITY = ("test", "typecheck", "lint", "build")


class ProjectMemory:
    """Mendeteksi dan menyimpan meta-informasi tentang struktur dan teknologi proyek."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.metadata: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    def _read_small(self, name: str) -> str:
        path = self.workspace / name
        try:
            if path.is_symlink() or not path.is_file():
                return ""
            with open(path, "rb") as handle:
                return handle.read(_MAX_MANIFEST_BYTES).decode("utf-8", errors="ignore")
        except OSError:
            return ""

    def _scan_node(self) -> None:
        raw = self._read_small("package.json")
        if not raw:
            return
        try:
            manifest = json.loads(raw)
        except ValueError:
            return
        if not isinstance(manifest, dict):
            return
        for lockfile, manager in _JS_LOCKFILES:
            if (self.workspace / lockfile).exists():
                self.metadata["js_package_manager"] = manager
                break
        manager = self.metadata.get("js_package_manager", "npm")

        deps: Dict[str, Any] = {}
        for key in ("dependencies", "devDependencies"):
            if isinstance(manifest.get(key), dict):
                deps.update(manifest[key])
        frameworks = [label for dep, label in _JS_FRAMEWORKS if dep in deps]
        if frameworks:
            self.metadata["frameworks"] = frameworks

        scripts = manifest.get("scripts")
        if isinstance(scripts, dict):
            run = "npm run" if manager == "npm" else manager
            commands = [f"{run} {name}" for name in _SCRIPT_PRIORITY if name in scripts]
            if "test" in scripts and manager == "npm":
                commands[commands.index("npm run test")] = "npm test"
            self.metadata["verify_commands"].extend(commands)

    def _scan_python(self) -> None:
        pytest_markers = ("pytest.ini", "conftest.py", "tests/conftest.py")
        pyproject = self._read_small("pyproject.toml")
        requirements = self._read_small("requirements.txt") + self._read_small("requirements-dev.txt")
        uses_pytest = (any((self.workspace / marker).exists() for marker in pytest_markers)
                       or "[tool.pytest" in pyproject or "pytest" in requirements.lower())
        if uses_pytest:
            self.metadata["verify_commands"].append("python -m pytest")

    def _scan_other(self) -> None:
        if (self.workspace / "Cargo.toml").exists():
            self.metadata["verify_commands"].append("cargo test")
        if (self.workspace / "go.mod").exists():
            self.metadata["verify_commands"].append("go test ./...")
        makefile = self._read_small("Makefile")
        if any(line.startswith("test:") for line in makefile.splitlines()):
            self.metadata["verify_commands"].append("make test")

    # ------------------------------------------------------------------
    def scan_project_meta(self) -> None:
        """Memindai root folder untuk mendeteksi framework dan alat yang digunakan."""
        self.metadata["path"] = str(self.workspace.resolve())
        self.metadata["has_git"] = (self.workspace / ".git").exists()

        # Deteksi ekosistem Node.js
        self.metadata["has_package_json"] = (self.workspace / "package.json").exists()

        # Deteksi ekosistem Python
        self.metadata["has_requirements"] = (self.workspace / "requirements.txt").exists()
        self.metadata["has_pyproject"] = (self.workspace / "pyproject.toml").exists()

        # Deteksi bahasa dominan
        if (self.workspace / "tsconfig.json").exists():
            self.metadata["dominant_language"] = "TypeScript"
        elif self.metadata["has_requirements"] or self.metadata["has_pyproject"]:
            self.metadata["dominant_language"] = "Python"
        elif (self.workspace / "Cargo.toml").exists():
            self.metadata["dominant_language"] = "Rust"
        elif (self.workspace / "go.mod").exists():
            self.metadata["dominant_language"] = "Go"
        elif self.metadata["has_package_json"]:
            self.metadata["dominant_language"] = "JavaScript"
        else:
            self.metadata["dominant_language"] = "Unknown"

        # Tooling hints: package manager, frameworks, and commands that verify work.
        for key in ("js_package_manager", "frameworks"):
            self.metadata.pop(key, None)
        self.metadata["verify_commands"] = []
        self._scan_node()
        self._scan_python()
        self._scan_other()
        seen: List[str] = []
        for command in self.metadata["verify_commands"]:
            if command not in seen:
                seen.append(command)
        self.metadata["verify_commands"] = seen
