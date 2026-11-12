"""Heuristic risk notes shown next to a shell command in the approval prompt.

These are *advice for the human reviewer*, not a security boundary: every shell
command still requires explicit approval, and a command that triggers no note
is not thereby safe.
"""
import re
from typing import List

_TEMPLATE_SUFFIXES = ("example", "sample", "template", "dist", "defaults")

_SENSITIVE_REF = re.compile(
    r"(?:^|[\s'\"=:/@<>(,])("
    r"\.env(?:\.[\w-]+)?|\.ssh|\.aws|\.gnupg|\.kube|\.netrc|\.npmrc|\.pypirc|\.git-credentials|"
    r"id_(?:rsa|dsa|ecdsa|ed25519)|[\w./-]*\.(?:pem|p12|pfx|key)|"
    r"credentials(?:\.json)?|secrets?\.(?:json|ya?ml)"
    r")(?=$|[\s'\"/;|&)])",
    re.IGNORECASE,
)
_PIPE_TO_SHELL = re.compile(
    r"\b(?:curl|wget)\b[^|;&]*\|\s*(?:sudo\s+)?(?:ba|z|da|k)?sh\b", re.IGNORECASE)
_OBFUSCATED_EXEC = re.compile(
    r"base64\s+(?:-d|--decode)[^|;&]*\|\s*(?:ba|z|da|k)?sh\b|\beval\b\s+[\"'$(]", re.IGNORECASE)
_NETWORK_TOOL = re.compile(r"\b(?:curl|wget|nc|ncat|netcat|scp|sftp|rsync|ftp)\b", re.IGNORECASE)
_SYSTEM_PATH = re.compile(
    r"(?:^|[\s'\"=])(?:/etc|/usr/(?:bin|sbin|lib)|/bin|/sbin|/boot|/root|/sys|/proc|~/|\$HOME)(?:/|\s|$|['\"])")


def _sensitive_refs(command: str) -> List[str]:
    found = []
    for match in _SENSITIVE_REF.finditer(command):
        name = match.group(1)
        if name.lower().rsplit(".", 1)[-1] in _TEMPLATE_SUFFIXES:
            continue
        if name not in found:
            found.append(name)
    return found


def assess_command(command: str) -> List[str]:
    """Return human-readable risk notes for ``command`` (empty list if none)."""
    if not isinstance(command, str) or not command.strip():
        return []
    notes: List[str] = []

    from tools.engine import ToolEngine  # local import: avoid a package import cycle
    if ToolEngine.is_command_dangerous(command):
        notes.append("Matches a known destructive or irreversible command pattern.")

    refs = _sensitive_refs(command)
    if refs:
        notes.append("Touches credential-like paths (" + ", ".join(refs[:4]) +
                     "). The agent's file tools block these; the shell does not.")
        if _NETWORK_TOOL.search(command):
            notes.append("Uses a network tool while referencing credential-like paths "
                         "(possible secret exfiltration).")
    if _PIPE_TO_SHELL.search(command):
        notes.append("Downloads a script and pipes it straight into a shell.")
    if _OBFUSCATED_EXEC.search(command):
        notes.append("Executes decoded or evaluated text, which hides what will really run.")
    if _SYSTEM_PATH.search(command):
        notes.append("Reaches system or home paths outside the workspace.")
    return notes
