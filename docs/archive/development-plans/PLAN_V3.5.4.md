# ZX Code v3.5.4 — Recovery Integrity

## Objective
Make persisted mutation evidence tamper-evident and make recovery fail closed when the journal history cannot be verified.

## Implemented
1. Journal manifests use schema version 2 and include a canonical SHA-256 payload digest.
2. Every durable manifest update appends a sequence-numbered audit-chain entry containing the prior entry hash and current payload hash; the chain is fsynced before the atomic manifest replacement.
3. Recovery validates every chain link, sequence, previous hash, final chain head, and canonical manifest payload digest before considering journal status.
4. Missing, truncated, malformed, modified, legacy, or mismatched journal evidence is classified as unknown and blocks resume.
5. Legacy v1 journals remain inspectable as legacy/unknown rather than being silently trusted.
6. Added integrity regression tests for valid journals, manifest tampering, chain truncation, and legacy journals.

## Safety boundaries
- The chain is tamper-evident against accidental or partial modification when the attacker cannot rewrite the entire chain and manifest consistently. It is not digitally signed and does not authenticate against a privileged attacker able to rewrite all local files.
- Hash chaining does not provide exactly-once execution or prove external side effects.
- Audit verification is bounded by file-size and entry-count limits.
