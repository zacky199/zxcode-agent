"""Tests for checkpointing and rollback semantics."""
import hashlib
import zipfile
from pathlib import Path

import pytest

from execution.checkpoint import CheckpointManager
from execution.rollback import RollbackManager


def trusted_digest(path):
    path = Path(path)
    return {path.resolve(): hashlib.sha256(path.read_bytes()).hexdigest()}


def test_create_snapshot_and_restore(workspace):
    (workspace / "keep.txt").write_text("original")
    mgr = CheckpointManager(workspace)
    snap = mgr.create_snapshot("pre_mutation")
    assert snap.exists()
    assert zipfile.is_zipfile(snap)

    (workspace / "keep.txt").write_text("modified")
    (workspace / "new.txt").write_text("new")

    RollbackManager(workspace, trusted_snapshots=mgr.trusted_snapshots).restore_snapshot(snap)
    assert (workspace / "keep.txt").read_text() == "original"
    assert not (workspace / "new.txt").exists()


def test_snapshot_excludes_git_and_checkpoints(workspace):
    (workspace / ".git").mkdir()
    (workspace / ".git" / "HEAD").write_text("ref")
    (workspace / "real.txt").write_text("x")
    mgr = CheckpointManager(workspace)

    snap = mgr.create_snapshot("s")
    with zipfile.ZipFile(snap) as zf:
        names = zf.namelist()
    assert "real.txt" in names
    assert not any(".git" in n or ".zx_checkpoints" in n for n in names)


def test_restore_missing_snapshot_raises(workspace):
    rb = RollbackManager(workspace)
    with pytest.raises(FileNotFoundError):
        rb.restore_snapshot(Path("/nonexistent.zip"))


@pytest.mark.parametrize("path_kind", ["traversal", "absolute"])
def test_restore_rejects_zip_paths_outside_workspace(workspace, path_kind):
    checkpoint_dir = workspace / ".zx_checkpoints"
    checkpoint_dir.mkdir()
    archive = checkpoint_dir / f"{workspace.name}-malicious.zip"
    outside = workspace.parent / f"outside-{workspace.name}.txt"
    outside.unlink(missing_ok=True)
    member_name = f"../{outside.name}" if path_kind == "traversal" else str(outside)
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("good.txt", "must not be written before validation")
        zf.writestr(member_name, "outside")

    rb = RollbackManager(workspace, trusted_snapshots=trusted_digest(archive))
    with pytest.raises(ValueError):
        rb.restore_snapshot(archive)

    assert not outside.exists()
    assert not (workspace / "good.txt").exists()


def test_restore_rejects_existing_symlink_destination(workspace):
    outside = workspace.parent / f"outside-{workspace.name}.txt"
    outside.write_text("keep", encoding="utf-8")
    link = workspace / "linked.txt"
    link.write_text("inside", encoding="utf-8")
    checkpoints = CheckpointManager(workspace)
    archive = checkpoints.create_snapshot("symlink")
    link.unlink()
    try:
        link.symlink_to(outside)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    rb = RollbackManager(workspace, trusted_snapshots=checkpoints.trusted_snapshots)
    with pytest.raises(ValueError):
        rb.restore_snapshot(archive)

    assert outside.read_text(encoding="utf-8") == "keep"


def test_snapshot_skips_symlinks_to_files_outside_workspace(workspace):
    outside = workspace.parent / f"secret-{workspace.name}.txt"
    outside.write_text("must not enter checkpoint", encoding="utf-8")
    link = workspace / "secret-link.txt"
    try:
        link.symlink_to(outside)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    snapshot = CheckpointManager(workspace).create_snapshot("symlink")

    with zipfile.ZipFile(snapshot) as zf:
        assert "secret-link.txt" not in zf.namelist()


def test_checkpoint_name_cannot_escape_workspace(workspace):
    mgr = CheckpointManager(workspace)
    before = set(workspace.parent.glob("outside_*.zip"))

    with pytest.raises(ValueError):
        mgr.create_snapshot("../outside")

    assert set(workspace.parent.glob("outside_*.zip")) == before


def test_checkpoint_directory_symlink_is_rejected(workspace):
    outside = workspace.parent / f"checkpoints-outside-{workspace.name}"
    outside.mkdir()
    checkpoint_dir = workspace / ".zx_checkpoints"
    try:
        checkpoint_dir.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(ValueError, match="symlink"):
        CheckpointManager(workspace)

    assert list(outside.iterdir()) == []


def test_rollback_preserves_dotfiles_and_excluded_directories(workspace):
    (workspace / ".env").write_text("before", encoding="utf-8")
    (workspace / "credentials.json").write_text("credential-before", encoding="utf-8")
    (workspace / "server.key").write_text("key-before", encoding="utf-8")
    (workspace / "service-account.json").write_text("account-before", encoding="utf-8")
    private_dir = workspace / ".private"
    private_dir.mkdir()
    (private_dir / "config").write_text("before", encoding="utf-8")
    venv_dir = workspace / "venv"
    venv_dir.mkdir()
    (venv_dir / "cache").write_text("before", encoding="utf-8")

    checkpoints = CheckpointManager(workspace)
    snapshot = checkpoints.create_snapshot("preserve_excluded")

    (workspace / ".env").write_text("changed", encoding="utf-8")
    (workspace / "credentials.json").write_text("credential-changed", encoding="utf-8")
    (workspace / "server.key").write_text("key-changed", encoding="utf-8")
    (workspace / "service-account.json").write_text("account-changed", encoding="utf-8")
    (workspace / ".created-after").write_text("preserve", encoding="utf-8")
    (private_dir / "config").write_text("changed", encoding="utf-8")
    (venv_dir / "cache").write_text("changed", encoding="utf-8")
    (workspace / "visible-after.txt").write_text("remove", encoding="utf-8")

    RollbackManager(workspace, trusted_snapshots=checkpoints.trusted_snapshots).restore_snapshot(snapshot)

    assert (workspace / ".env").read_text(encoding="utf-8") == "changed"
    assert (workspace / "credentials.json").read_text(encoding="utf-8") == "credential-changed"
    assert (workspace / "server.key").read_text(encoding="utf-8") == "key-changed"
    assert (workspace / "service-account.json").read_text(encoding="utf-8") == "account-changed"
    assert (workspace / ".created-after").read_text(encoding="utf-8") == "preserve"
    assert (private_dir / "config").read_text(encoding="utf-8") == "changed"
    assert (venv_dir / "cache").read_text(encoding="utf-8") == "changed"
    assert not (workspace / "visible-after.txt").exists()


def test_restore_rejects_hidden_path_not_in_checkpoint_format(workspace):
    (workspace / ".env").write_text("keep", encoding="utf-8")
    checkpoint_dir = workspace / ".zx_checkpoints"
    checkpoint_dir.mkdir()
    archive = checkpoint_dir / "malicious.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("visible.txt", "should not be written")
        zf.writestr(".env", "overwrite")

    rb = RollbackManager(workspace, trusted_snapshots=trusted_digest(archive))
    with pytest.raises(ValueError, match="hidden paths"):
        rb.restore_snapshot(archive)

    assert (workspace / ".env").read_text(encoding="utf-8") == "keep"
    assert not (workspace / "visible.txt").exists()


def test_restore_rejects_untrusted_zip_and_source_symlinks(workspace):
    (workspace / "keep.txt").write_text("keep", encoding="utf-8")
    checkpoints = CheckpointManager(workspace)
    trusted = checkpoints.create_snapshot("trusted")
    outside = workspace.parent / f"outside-{workspace.name}.zip"
    with zipfile.ZipFile(outside, "w"):
        pass
    rb = RollbackManager(workspace, trusted_snapshots=checkpoints.trusted_snapshots)

    with pytest.raises(ValueError, match="checkpoint directory"):
        rb.restore_snapshot(outside)

    alias = checkpoints.checkpoint_dir / "alias.zip"
    try:
        alias.symlink_to(trusted)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    with pytest.raises(ValueError, match="symlink"):
        rb.restore_snapshot(alias)

    assert (workspace / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_restore_rejects_unregistered_empty_archive_without_deleting(workspace):
    keep = workspace / "visible.txt"
    keep.write_text("keep", encoding="utf-8")
    checkpoints = CheckpointManager(workspace)
    rogue = checkpoints.checkpoint_dir / "pre_mutation_99999999_999999_999999.zip"
    with zipfile.ZipFile(rogue, "w"):
        pass

    rb = RollbackManager(workspace, trusted_snapshots=checkpoints.trusted_snapshots)
    with pytest.raises(ValueError, match="active checkpoint manager"):
        rb.restore_snapshot(rogue)

    assert keep.read_text(encoding="utf-8") == "keep"



def test_checkpoint_excludes_credential_like_files(workspace):
    sensitive = {
        "credentials.json": "credential",
        "server.key": "private-key",
        "service-account.json": "service-account",
        ".env": "environment",
    }
    for filename, contents in sensitive.items():
        (workspace / filename).write_text(contents, encoding="utf-8")
    (workspace / "safe.txt").write_text("safe", encoding="utf-8")

    snapshot = CheckpointManager(workspace).create_snapshot("sensitive")

    with zipfile.ZipFile(snapshot) as archive:
        names = set(archive.namelist())
    assert "safe.txt" in names
    assert not (names & set(sensitive))


def test_existing_checkpoint_directory_is_tightened_to_private_mode(workspace):
    import stat

    checkpoint_dir = workspace / ".zx_checkpoints"
    checkpoint_dir.mkdir()
    checkpoint_dir.chmod(0o755)

    CheckpointManager(workspace)

    assert stat.S_IMODE(checkpoint_dir.stat().st_mode) == 0o700


def test_checkpoint_archive_is_private_even_with_permissive_umask(workspace):
    import os
    import stat

    previous_umask = os.umask(0)
    try:
        checkpoints = CheckpointManager(workspace)
        snapshot = checkpoints.create_snapshot("private")
    finally:
        os.umask(previous_umask)

    assert stat.S_IMODE(snapshot.stat().st_mode) == 0o600


@pytest.mark.parametrize("secret_name", [".env", "server.key"])
def test_checkpoint_skips_hard_link_alias_and_matches_filtered_workspace(workspace, secret_name):
    import os

    secret = workspace / secret_name
    sentinel = "HARDLINK_CHECKPOINT_SECRET_SENTINEL"
    secret.write_text(sentinel + "\n", encoding="utf-8")
    alias = workspace / "notes.md"
    try:
        os.link(secret, alias)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"hard links unavailable: {exc}")
    (workspace / "safe.txt").write_text("safe", encoding="utf-8")

    checkpoints = CheckpointManager(workspace)
    snapshot = checkpoints.create_snapshot("hardlink")
    rollback = RollbackManager(workspace, trusted_snapshots=checkpoints.trusted_snapshots)

    with zipfile.ZipFile(snapshot) as archive:
        names = set(archive.namelist())
        contents = b"\n".join(archive.read(name) for name in names)
    assert "safe.txt" in names
    assert "notes.md" not in names
    assert sentinel.encode() not in contents
    assert rollback.workspace_matches_snapshot(snapshot) is True


def test_restore_atomically_replaces_hard_link_target_without_changing_secret(workspace):
    import os

    target = workspace / "notes.md"
    target.write_text("RESTORE_TARGET_CONTENT\n", encoding="utf-8")
    checkpoints = CheckpointManager(workspace)
    snapshot = checkpoints.create_snapshot("restore-hardlink")
    secret = workspace / ".env"
    original_secret = b"RESTORE_HARDLINK_SECRET_SENTINEL\n"
    secret.write_bytes(original_secret)
    target.unlink()
    try:
        os.link(secret, target)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"hard links unavailable: {exc}")
    assert os.path.samefile(secret, target)

    rollback = RollbackManager(workspace, trusted_snapshots=checkpoints.trusted_snapshots)
    rollback.restore_snapshot(snapshot)

    assert secret.read_bytes() == original_secret
    assert target.read_text(encoding="utf-8") == "RESTORE_TARGET_CONTENT\n"
    assert not os.path.samefile(secret, target)


def test_rollback_preserves_hard_link_alias_excluded_from_snapshot(workspace):
    import os

    safe = workspace / "safe.txt"
    safe.write_text("before", encoding="utf-8")
    checkpoints = CheckpointManager(workspace)
    snapshot = checkpoints.create_snapshot("filtered-hardlink")
    secret = workspace / ".env"
    original_secret = b"PRESERVE_HARDLINK_SECRET_SENTINEL\n"
    secret.write_bytes(original_secret)
    alias = workspace / "notes.md"
    try:
        os.link(secret, alias)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"hard links unavailable: {exc}")
    safe.write_text("after", encoding="utf-8")

    rollback = RollbackManager(workspace, trusted_snapshots=checkpoints.trusted_snapshots)
    assert rollback.workspace_matches_snapshot(snapshot) is False
    rollback.restore_snapshot(snapshot)

    assert safe.read_text(encoding="utf-8") == "before"
    assert secret.read_bytes() == original_secret
    assert alias.exists()
    assert os.path.samefile(secret, alias)



def test_restore_restores_file_mode_from_checkpoint(workspace):
    import os
    import stat

    if os.name == "nt":
        pytest.skip("POSIX permission bits are not portable to Windows")

    target = workspace / "mode.txt"
    target.write_text("original\n", encoding="utf-8")
    target.chmod(0o640)
    checkpoints = CheckpointManager(workspace)
    snapshot = checkpoints.create_snapshot("restore-mode")

    target.write_text("modified\n", encoding="utf-8")
    target.chmod(0o600)
    rollback = RollbackManager(workspace, trusted_snapshots=checkpoints.trusted_snapshots)
    rollback.restore_snapshot(snapshot)

    assert target.read_text(encoding="utf-8") == "original\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
