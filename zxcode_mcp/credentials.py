"""Optional OS-backed credential vault for MCP HTTP headers."""
from __future__ import annotations

SERVICE_NAME = "ZXCode-MCP"


class CredentialVaultError(RuntimeError):
    """Vault unavailable or using an unsafe backend; secrets must not fall back."""


def _get_keyring():
    try:
        import keyring
    except ImportError as exc:
        raise CredentialVaultError("Credential vault unavailable; install ZX Code with the MCP vault extra") from exc
    backend = keyring.get_keyring()
    backend_name = type(backend).__module__ + "." + type(backend).__name__
    allowed = ("SecretService", "Keychain", "WinVault", "WindowsVault")
    if not any(marker.lower() in backend_name.lower() for marker in allowed):
        raise CredentialVaultError("Unsafe or unsupported credential backend; refusing plaintext fallback")
    return keyring



def _validate_name(name: str) -> None:
    import re
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name):
        raise CredentialVaultError("Invalid credential identifier")


def set_secret(name: str, secret: str) -> None:
    _validate_name(name)
    if not isinstance(secret, str) or not secret or len(secret) > 8192 or "\x00" in secret:
        raise CredentialVaultError("Invalid secret value")
    try:
        _get_keyring().set_password(SERVICE_NAME, name, secret)
    except CredentialVaultError:
        raise
    except Exception as exc:
        raise CredentialVaultError(f"Could not store vault entry ({type(exc).__name__})") from exc


def get_secret(name: str) -> str:
    _validate_name(name)
    try:
        secret = _get_keyring().get_password(SERVICE_NAME, name)
    except CredentialVaultError:
        raise
    except Exception as exc:
        raise CredentialVaultError(f"Could not read vault entry ({type(exc).__name__})") from exc
    if not isinstance(secret, str) or not secret:
        raise CredentialVaultError(f"Credential not found in OS vault: {name}")
    return secret


def delete_secret(name: str) -> None:
    _validate_name(name)
    try:
        _get_keyring().delete_password(SERVICE_NAME, name)
    except Exception as exc:
        raise CredentialVaultError(f"Could not delete vault entry ({type(exc).__name__})") from exc
