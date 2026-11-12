"""Policy for native workspace file mutations (``write_file`` / ``edit_file``).

Native file edits are **opt-in and off by default**. Only the user can turn
them on, through code that the model cannot reach:

* the ``--allow-edits`` CLI flag, or
* the ``/edits on`` slash command (typed by the user, with a confirmation).

The switch lives on the ``ToolEngine`` / ``BuiltinTools`` instance and is never
read from model-supplied tool arguments or Markdown blocks. When it is off the
two tools are removed from the schemas sent to the model, and any call that
still arrives (hallucinated, or via the Markdown fallback) fails closed before
a checkpoint is taken or a file is touched.

When it is on, every edit still goes through the task-scoped mutation journal
(pre-state record, atomic commit, pinned parent directories, rollback of a
failed operation). The journal is a recovery record, not an OS sandbox.
"""

# Default for new ToolEngine / BuiltinTools instances.
NATIVE_FILE_MUTATIONS_DEFAULT = False

# Tools governed by this policy.
FILE_MUTATION_TOOLS = ("write_file", "edit_file")

FILE_MUTATION_DISABLED_MESSAGE = (
    "Access denied: native file mutation is disabled for this session. "
    "The user can enable it by restarting with --allow-edits or by running /edits on."
)
