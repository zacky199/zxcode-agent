# Implementation Plan

1. Replace the custom REST placeholder with the official MCP SDK adapter.
2. Rename the internal package to `zxcode_mcp` to avoid shadowing the SDK namespace.
3. Add server configuration, transport validation, allowlists, schema validation,
   argument validation, approval gating, output limits, and safe audit logging.
4. Connect discovered MCP tools to ToolEngine and the ZXAgentPro constructor.
5. Add real local-server integration tests for stdio and Streamable HTTP.
6. Run the full regression suite, compile checks, and wheel/package verification.

## Compatibility

The core remains Python >=3.10. The MCP optional extra is pinned to
`mcp>=1.30,<2` because the v1 SDK line supports Python 3.10. SDK v2 migration
is a separate decision requiring a Python minimum-version review.
