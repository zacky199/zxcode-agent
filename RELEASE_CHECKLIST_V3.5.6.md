# GitHub Release Checklist — v3.5.6

## Local validation
- [x] Recovery Doctor implementation and read-only contract tests
- [x] Full pytest suite: 383 passed, 11 existing unsafe-host warnings
- [x] Python compileall
- [x] Import smoke test (9 modules)
- [x] CLI `--version` reports v3.5.6
- [x] CLI `--help` smoke test
- [x] Python 3.12 full test suite (383 passed) and GitHub Actions YAML parse
- [x] ZIP CRC, patch application and source-tree equivalence (see final release report)
- [x] SHA-256 checksums recorded

## Before publishing
- [ ] Confirm GitHub repository and default branch are the intended target
- [ ] Confirm GitHub Actions test matrix passes on Python 3.10 and 3.12
- [ ] Review the generated diff and release notes
- [ ] Push the v3.5.6 commit
- [ ] Create and push annotated tag `v3.5.6`
- [ ] Confirm GitHub Actions `Publish GitHub Release` passes
- [ ] Verify uploaded ZIP, patch and SHA256SUMS.txt from the GitHub Releases page

Publishing is intentionally not performed by this local release-preparation task.
