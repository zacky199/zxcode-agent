# ZX Code v3.5.4 — Recovery Integrity

- Upgraded mutation journal manifests to schema v2 with canonical payload SHA-256 digests.
- Added a durable append-only SHA-256 audit chain for each journal owner.
- Recovery now verifies sequence continuity, previous-hash links, entry hashes, and manifest-to-chain-tail consistency before trusting journal state.
- Missing, legacy, truncated, malformed, or tampered journal evidence fails closed to manual reconciliation.
- Added regression coverage for valid chains, manifest tampering, chain truncation, and legacy journals.
- Previous v3.5.3 release artifacts and source directory remain untouched.
