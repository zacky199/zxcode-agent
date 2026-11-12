import multiprocessing
import time

import pytest

from execution.workspace_lock import WorkspaceBusyError, WorkspaceLock


def _hold_lock(path, ready, release):
    lock = WorkspaceLock(path).acquire()
    ready.set()
    release.wait(5)
    lock.release()


def test_workspace_lock_rejects_second_process_and_releases_after_owner_exit(tmp_path):
    ready = multiprocessing.Event()
    release = multiprocessing.Event()
    process = multiprocessing.Process(target=_hold_lock, args=(tmp_path, ready, release))
    process.start()
    try:
        assert ready.wait(3)
        with pytest.raises(WorkspaceBusyError):
            WorkspaceLock(tmp_path).acquire()
    finally:
        release.set()
        process.join(5)
        if process.is_alive():
            process.terminate()
            process.join()
    assert process.exitcode == 0
    lock = WorkspaceLock(tmp_path).acquire()
    lock.release()


def test_workspace_lock_is_reentrant_only_after_release(tmp_path):
    first = WorkspaceLock(tmp_path).acquire()
    with pytest.raises(WorkspaceBusyError):
        WorkspaceLock(tmp_path).acquire()
    first.release()
    second = WorkspaceLock(tmp_path).acquire()
    second.release()


def _crash_while_holding_lock(path):
    import os
    WorkspaceLock(path).acquire()
    os._exit(73)


def test_kernel_releases_workspace_lock_after_process_crash(tmp_path):
    process = multiprocessing.Process(target=_crash_while_holding_lock, args=(tmp_path,))
    process.start()
    process.join(5)
    assert process.exitcode == 73
    lock = WorkspaceLock(tmp_path).acquire()
    lock.release()
