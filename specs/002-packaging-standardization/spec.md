# Spec 002: Python Packaging Standardization

## Status

Implementation in progress. Target baseline: ZX Code 3.5.6.

## Problem

The project currently relies on `requirements.txt` and direct execution of `main.py`. It has no package metadata or installed `zxcode` command, and pytest configuration is kept separately in `pytest.ini`.

## Goals

- Define standard PEP 517/518 build metadata and Python support (`>=3.10`).
- Provide a `zxcode` console command without changing the existing `python main.py` workflow.
- Separate runtime dependencies from development/test dependencies.
- Keep the existing import/package layout intact.
- Make CI validate installation from project metadata, not only direct source execution.

## Non-goals

- No module moves or import-path changes.
- No dependency version pinning/lockfile in this change.
- No runtime behavior, provider, storage, planner, MCP, or execution changes.
- No deletion of `requirements.txt`; it remains a compatibility installation path during migration.

## Acceptance criteria

1. `pip install -e ".[dev]"` succeeds on Python 3.10 and 3.12.
2. `zxcode --version` prints `ZX Code v3.5.6` from an installed environment.
3. `python main.py --version` continues to work.
4. The complete test suite passes after installation.
5. CI and release validation install the package using the declared metadata.
6. Runtime dependency declarations match the runtime section of `requirements.txt`.
