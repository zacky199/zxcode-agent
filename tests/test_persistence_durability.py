"""Regression tests for atomic checkpoint publication and journal blob durability."""
import os
import stat
import zipfile
from pathlib import Path

import pytest

from execution.checkpoint import CheckpointManager
from execution.journal import MutationJournal


def test_checkpoint_is_published_only_after_complete_archive(workspace, monkeypatch):
    (workspace / "payload.txt").write_text("durable payload", encoding="utf-8")
    manager = CheckpointManager(workspace)
    original_write = zipfile.ZipFile.write

    def interrupted_write(self, *args, **kwargs):
        raise OSError("injected interruption during archive population")

    monkeypatch.setattr(zipfile.ZipFile, "write", interrupted_write)
    with pytest.raises(OSError, match="injected interruption"):
        manager.create_snapshot("interrupted")

    checkpoint_dir = workspace / ".zx_checkpoints"
    assert not list(checkpoint_dir.glob("interrupted_*.zip"))
    assert not list(checkpoint_dir.glob(".checkpoint-*.tmp"))

    monkeypatch.setattr(zipfile.ZipFile, "write", original_write)
    snapshot = manager.create_snapshot("complete")
    assert zipfile.is_zipfile(snapshot)
    with zipfile.ZipFile(snapshot) as archive:
        assert archive.read("payload.txt") == b"durable payload"


def test_checkpoint_fsyncs_directory_after_atomic_publish(workspace, monkeypatch):
    (workspace / "file.txt").write_text("x", encoding="utf-8")
    real_fsync = os.fsync
    directory_fds = []

    def observe_fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            directory_fds.append(fd)
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", observe_fsync)
    snapshot = CheckpointManager(workspace).create_snapshot("durable")
    assert snapshot.is_file()
    assert directory_fds, "checkpoint publication must fsync its containing directory"


def test_journal_blob_rename_fsyncs_blob_directory(workspace, monkeypatch):
    source = workspace / "preimage.txt"
    source.write_text("before", encoding="utf-8")
    journal = MutationJournal(workspace, owner_id="durability-test")
    journal._ensure_ready()
    observed = []
    real_fsync_directory = journal._fsync_directory

    def observe(directory):
        observed.append(Path(directory))
        return real_fsync_directory(directory)

    monkeypatch.setattr(journal, "_fsync_directory", observe)
    try:
        digest, backup = journal._blob_for(source, source.stat())
        assert Path(backup).is_file()
        assert Path(backup).read_text(encoding="utf-8") == "before"
        assert digest in Path(backup).name
        assert observed == [journal._blob_dir]
    finally:
        journal.close()


def test_journal_directory_fsync_failure_is_not_silently_ignored(workspace, monkeypatch):
    source = workspace / "preimage.txt"
    source.write_text("before", encoding="utf-8")
    journal = MutationJournal(workspace, owner_id="durability-failure")
    journal._ensure_ready()

    def fail_directory_sync(_directory):
        raise OSError("injected directory fsync failure")

    monkeypatch.setattr(journal, "_fsync_directory", fail_directory_sync)
    try:
        with pytest.raises(Exception, match="could not checkpoint"):
            journal._blob_for(source, source.stat())
        # Rename may already have happened. The operation is reported as uncertain,
        # and the content-addressed blob is left intact for conservative recovery.
        assert list(journal._blob_dir.glob("*.bin"))
    finally:
        journal.close()
