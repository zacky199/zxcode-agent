# ZX Code v3.5.6 — Test Report

## Local test results

- Full pytest suite on Python 3.11: **383 passed, 11 warnings** (12.47s).
- Full pytest suite on Python 3.12 with a clean virtual environment and installed `requirements.txt`: **383 passed, 11 warnings** (13.25s).
- Focused Recovery Doctor, task-state, recovery-integrity and mutation-journal suite: **48 passed**.
- Python `compileall`: passed on Python 3.11 and Python 3.12.
- Import smoke: 9 core modules passed on Python 3.11.
- CLI smoke: `python main.py --version` reports `ZX Code v3.5.6`; `--help` exits successfully.
- Release ZIP CRC: passed.
- Original prepared patch passed clean application against a v3.5.5 source copy; resulting source tree matched v3.5.6 (191 files).

## GitHub Actions verification

- Workflow run: https://github.com/zacky199/zxcode-agent/actions/runs/43263725701
- Result: **success**; all three jobs completed.
- Python 3.10: **383 passed, 11 warnings**; compileall passed.
- Python 3.12: **383 passed, 11 warnings**; compileall passed.
- Release build and publish: passed.
- The repository's v3.5.6 tag is its first reachable version tag and points to a root commit. The workflow therefore generated the published PATCH against Git's canonical empty tree. Future version tags will use the newest earlier reachable version tag as the patch base.
- Published ZIP and PATCH were downloaded from the Release page. Both SHA-256 entries matched the published SHA256SUMS file; ZIP integrity check passed.
- Release: https://github.com/zacky199/zxcode-agent/releases/tag/v3.5.6

## Warnings and unavailable checks

- The 11 pytest warnings are existing `RuntimeWarning` notices that unsafe host execution inherits the agent process privileges when no OS sandbox is active. They are not test failures; default container execution remains fail-closed.
- Docker CLI is installed, but access to `/var/run/docker.sock` is denied. Docker image build and container E2E were not run.
- No physical power-loss, kernel crash, or VM hard-reset test was performed.
- No exactly-once guarantee is made for external commands, APIs, plugins, or effects outside the workspace.
- GitHub emitted non-fatal runner/action deprecation notices (Node.js 20 compatibility and the upcoming `ubuntu-latest` image migration); they did not affect this successful run.

## Release decision

**ZX Code v3.5.6 is published on GitHub.** The release workflow was corrected, committed to `master`, and exercised successfully through manual dispatch against the existing `v3.5.6` tag. Both supported CI interpreter versions passed the full test suite, and the published release assets passed checksum and ZIP integrity verification.
