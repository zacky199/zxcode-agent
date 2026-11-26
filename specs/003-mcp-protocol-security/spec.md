# Specification: MCP Protocol & Security Hardening

## Objective
Integrate standards-compliant MCP servers into ZX Code without treating remote
servers, tool metadata, arguments, or tool results as trusted by default.

## Functional requirements
- Use the official MCP SDK and support Streamable HTTP plus stdio transports.
- Discover tool metadata through MCP protocol methods, not custom REST paths.
- Require explicit server configuration and per-server tool allowlists.
- Validate remote JSON Schema and all tool arguments before invocation.
- Require a trusted approval callback for every remote tool call.
- Expose namespaced MCP tool schemas through ToolEngine and ZXAgentPro.
- Bound transport timeout, input size, response size, endpoint schemes, and headers.
- Keep remote side effects outside the local mutation journal and state this limitation.

## Security requirements
- Deny by default when no approval callback or allowlisted tool exists.
- Require HTTPS for remote HTTP transport; allow plain HTTP only on loopback.
- Reject endpoint URL credentials, query strings, fragments, non-public IP literals,
  forbidden headers, external JSON Schema references, and oversized schemas.
- Treat descriptions and results from MCP servers as untrusted external data.
- Do not log tool arguments, result bodies, endpoint credentials, or authorization headers.

## Acceptance criteria
- Real SDK stdio and Streamable HTTP round trips pass.
- Unapproved, non-allowlisted, malformed, oversized, or invalid-schema calls do not execute.
- Tool schemas appear in the agent only after successful discovery and allowlisting.
- Existing ZX Code regression suite remains green.
