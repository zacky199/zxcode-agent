# Architecture Audit — ZX Code 3.5.6

Audit target: `master` at `677645fcf0d2421e482cd51601cf28337076579c`.

## Verified baseline

- Working tree was clean before this audit and packaging work.
- Regression suite: **383 passed, 11 warnings** in 12.40 seconds.
- Warnings originate from the explicit unsafe host-execution warning in `execution/sandbox.py`; they are not test failures.
- No `pyproject.toml` existed at audit start. The application exposed `async def main()` and supported `python main.py`, but no installed console-script entry point.
- `tools/builtin.py` is approximately 17.9 KB; `ui/console.py` approximately 19.9 KB; `core/agent.py` approximately 71.2 KB; `execution/journal.py` approximately 72.2 KB.

## Corrections to the initial roadmap diagnosis

### Cache modules are not duplicates

- `indexing/cache.py` implements a workspace file-content hash cache persisted as JSON under the checkpoint directory. It supports incremental source indexing.
- `storage/cache.py` implements a process-local in-memory key/value cache with optional TTL.
- They have different persistence, lifecycle, and invalidation semantics. Do not delete or merge either without identifying concrete callers and defining replacement semantics.

### MCP is currently a scaffold, not an integrated MCP execution path

- `mcp/client.py` sends an HTTP POST to `/tools/{tool_name}` and suppresses all exceptions by returning `None`.
- `mcp/manager.py` stores clients and exposes registry names, but does not discover tools or invoke them.
- `mcp/registry.py` stores name/schema dictionaries only.
- `mcp/adapters/` contains only an empty `__init__.py`.
- No application code references `MCPManager`, `MCPClient`, or the MCP package outside the package itself.
- The current code does not demonstrate protocol negotiation, capability discovery, transport lifecycle, schema validation, or integration with the native tool authorization/journal path.
- Treat MCP integration as a design and security project, not as a small adapter-only task.

### Memory and vector store are basic/in-memory

- `storage/vector.py` is a pure-Python lexical term-frequency scorer, not an embedding-backed vector database.
- `memory/semantic.py` subclasses that store without adding persistence.
- `memory/retrieval.py` returns document IDs from the simple scorer.
- `memory/manager.py` builds a basic project + retrieved-knowledge context.
- Do not describe this as persistent semantic vector memory without implementing and testing that behavior.

## Priority findings

1. Packaging and installability are a low-risk, independently verifiable improvement.
2. MCP must be designed around protocol correctness and a fail-closed authorization boundary before implementation.
3. Memory/indexing/storage responsibilities need explicit contracts; current cache classes should remain separate unless caller analysis proves otherwise.
4. Fast-path routing must retain the existing security, journaling, and verification gates for mutations.
5. Add benchmark and cost telemetry only after provider usage reporting and per-task accounting semantics are defined.

## Baseline limitations

This audit is based on source inspection and the local test suite. It does not claim production-grade crash durability under power loss, complete MCP protocol compliance, measured token cost accuracy, or comparative task-success benchmarks.
