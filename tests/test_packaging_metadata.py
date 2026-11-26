from pathlib import Path
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility
    import tomli as tomllib

import main
from config.constants import APP_VERSION


def test_console_entrypoint_is_available():
    assert callable(main.cli)


def test_project_metadata_matches_runtime_version_and_dependencies():
    root = Path(__file__).resolve().parents[1]
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    project = metadata["project"]

    assert project["name"] == "zxcode-agent"
    assert project["version"] == APP_VERSION
    assert project["requires-python"] == ">=3.10"
    assert project["scripts"]["zxcode"] == "main:cli"
    assert "pytest>=7.4.0" in project["optional-dependencies"]["dev"]

    requirements = [
        line.strip()
        for line in (root / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
        and not line.strip().startswith(("pytest", "pytest-asyncio"))
    ]
    assert set(project["dependencies"]) == set(requirements)


def test_pytest_configuration_is_declared_in_pyproject():
    root = Path(__file__).resolve().parents[1]
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    pytest_config = metadata["tool"]["pytest"]["ini_options"]

    assert pytest_config["testpaths"] == ["tests"]
    assert pytest_config["asyncio_mode"] == "auto"
