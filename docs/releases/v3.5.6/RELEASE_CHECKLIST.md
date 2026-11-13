# GitHub Release Checklist — v3.5.6

## Source and local validation
- [x] Recovery Doctor implementation and read-only contract tests
- [x] Full pytest suite: 383 passed, 11 existing unsafe-host warnings
- [x] Python compileall
- [x] Import smoke test (9 modules)
- [x] CLI `--version` reports v3.5.6
- [x] CLI `--help` smoke test
- [x] Python 3.12 full test suite (383 passed)
- [x] ZIP CRC, patch application and source-tree equivalence (original prepared artifacts)
- [x] SHA-256 checksums recorded

## GitHub publication and post-release verification
- [x] Confirm repository `zacky199/zxcode-agent` and default branch `master`
- [x] Push v3.5.6 source commit
- [x] Confirm tag `v3.5.6` points to the release source commit
- [x] Run GitHub Actions test matrix on Python 3.10 and 3.12
- [x] Confirm both test suites: 383 passed, 11 warnings each
- [x] Build release ZIP, PATCH and SHA256SUMS.txt
- [x] Publish/update GitHub Release `v3.5.6`
- [x] Download published assets and verify SHA-256 checksums
- [x] Verify ZIP archive integrity

## Workflow hardening
- [x] Support first release when no earlier version tag exists
- [x] Add Python 3.10/3.12 test matrix
- [x] Add manual workflow dispatch for repairing/rebuilding an existing release
- [x] Upload with `--clobber` when the GitHub Release already exists
- [x] Validate workflow end-to-end using manual dispatch for `v3.5.6`

Workflow verification run: https://github.com/zacky199/zxcode-agent/actions/runs/43263725701

Published release: https://github.com/zacky199/zxcode-agent/releases/tag/v3.5.6
