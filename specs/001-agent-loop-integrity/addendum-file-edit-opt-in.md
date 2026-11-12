# Addendum: native file edits become an explicit user opt-in

**Amends:** acceptance criterion 5 and the Goal of `spec.md`.

The original criterion disabled `write_file`/`edit_file` outright while no OS-level
isolation was active. That conflicted with the Goal ("without removing autonomous
edits inside the selected workspace") and pushed edits onto the shell path, which
has weaker journaling (shell deltas are always `CONFLICT/AMBIGUOUS`, never rolled back).

## Amended behaviour

1. Native file edits are controlled per `ToolEngine`/`BuiltinTools` instance
   (`allow_file_mutations`, default `False`).
2. Only trusted code can change it: the `--allow-edits` flag and the `/edits on|off`
   command, both confirmed by the user. Model arguments and Markdown text cannot.
3. While off: the tools are omitted from `get_tool_schemas()`, the system prompt says
   they are unavailable, and any call or Markdown `file`/`patch` block fails closed
   before a checkpoint or file change (all prior fail-closed tests still apply).
4. While on: the existing journal gate, dirfd pinning, sensitive-path denial, and
   no-parent-creation rules apply unchanged. The journal is a recovery record, not a sandbox.
5. Shell and plugin behaviour is unchanged (per-command approval, process privileges).
