"""Default-deny path classification for files likely to contain credentials."""
from pathlib import Path


SENSITIVE_DIRECTORIES = {
    ".ssh", ".aws", ".azure", ".gnupg", ".kube", ".docker",
    ".terraform", ".pulumi", ".vault", "credentials", "secrets",
    "private_keys",
}
SENSITIVE_FILENAMES = {
    ".npmrc", ".pypirc", ".netrc", "netrc", ".git-credentials",
    "git-credentials", "credentials", "credentials.json", "credentials.yml",
    "credentials.yaml", "secrets.json", "secret.json", "secrets.yml",
    "secrets.yaml", "auth.json", "token.json", "tokens.json",
    "access_tokens.db", "application_default_credentials.json",
    "access_token.json", "refresh_token.json", "client_secret.json",
    "private_key", "private-key", "id_ed25519_sk",
    "terraform.tfstate", "terraform.tfstate.backup",
}
PRIVATE_KEY_SUFFIXES = {
    ".key", ".pem", ".p12", ".pfx", ".p8", ".jks", ".keystore",
    ".tfstate", ".tfvars",
}
ENV_TEMPLATE_SUFFIXES = {"example", "sample", "template", "dist", "defaults"}


def is_sensitive_path(path) -> bool:
    """Return True for common secret stores, env files, and private key material."""
    parts = [part.casefold() for part in Path(path).parts if part not in ("", ".")]
    if not parts:
        return False
    if any(part in SENSITIVE_DIRECTORIES for part in parts):
        return True

    name = parts[-1]
    if name in SENSITIVE_FILENAMES:
        return True
    if name == ".envrc":
        return True
    if name == ".env" or name.startswith(".env."):
        suffix = name.rsplit(".", 1)[-1] if "." in name else ""
        if suffix not in ENV_TEMPLATE_SUFFIXES:
            return True
    if name.endswith(tuple(PRIVATE_KEY_SUFFIXES)):
        return True
    if name in {"id_rsa", "id_ecdsa", "id_ed25519", "id_dsa"}:
        return True
    if name.startswith(("service-account", "serviceaccount")) and name.endswith(".json"):
        return True
    if name.endswith(("credentials.json", "credentials.yml", "credentials.yaml")):
        return True
    return False
