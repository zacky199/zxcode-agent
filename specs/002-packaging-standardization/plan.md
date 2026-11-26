# Implementation Plan

1. Add `pyproject.toml` with setuptools build metadata, runtime dependencies, `dev` extra, package discovery, pytest configuration, and CLI entry point.
2. Add a synchronous `main.cli()` wrapper around the existing async `main()`; preserve `python main.py` behavior.
3. Update CI and release workflows to install `.[dev]` so package metadata is exercised.
4. Add focused tests for CLI entry-point availability and metadata consistency.
5. Validate editable installation, installed CLI, source CLI, compileall, and the full regression suite.
