# ZX Code v3.5.6 — Test Report

## Local test results

- Full pytest suite on Python 3.11: **383 passed, 11 warnings** (12.47s).
- Full pytest suite on Python 3.12 with a clean virtual environment and installed `requirements.txt`: **383 passed, 11 warnings** (13.25s).
- Focused Recovery Doctor, task-state, recovery-integrity and mutation-journal suite: **48 passed**.
- Python `compileall`: passed on Python 3.11 and Python 3.12.
- Import smoke: 9 core modules passed on Python 3.11.
- CLI smoke: `python main.py --version` reports `ZX Code v3.5.6`; `--help` exits successfully.
- GitHub Actions workflow YAML files parse successfully.
- Release ZIP CRC: passed; includes tests.
- Release patch: clean application check and apply against a clean v3.5.5 source copy passed; resulting source tree matched v3.5.6 (191 files).

## Warnings and unavailable checks

- The 11 pytest warnings are existing `RuntimeWarning` notices that unsafe host execution inherits the agent process privileges when no OS sandbox is active. They are not test failures; default container execution remains fail-closed.
- Python 3.10 was not installed locally, so the minimum supported interpreter remains unverified on this VM. GitHub Actions is configured to run the suite on Python 3.10 and 3.12.
- Docker CLI is installed, but access to `/var/run/docker.sock` is denied. Docker image build and container E2E were not run.
- No physical power-loss, kernel crash, or VM hard-reset test was performed.
- No exactly-once guarantee is made for external commands, APIs, plugins, or effects outside the workspace.

## Release decision

The local source, test suite, CLI smoke checks, archive and patch integrity checks passed. This is a **release candidate prepared for GitHub**. Publication should remain gated on the GitHub Actions run for the actual repository/tag and a final review of the generated release assets.
