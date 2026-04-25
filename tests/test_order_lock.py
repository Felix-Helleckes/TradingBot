import os
import sys
import time
import tempfile
import subprocess

import pytest
import sys


def _spawn_lock_holder(lock_path, sleep_s=2.0):
    # Run a short Python process that acquires the lock and sleeps
    code = r"""
import time, sys
import order_lock
order_lock.LOCK_PATH = sys.argv[1]
with order_lock.acquire_order_lock(timeout_seconds=5.0) as locked:
    if not locked:
        sys.exit(2)
    time.sleep(float(sys.argv[2]))
"""
    return subprocess.Popen([sys.executable, "-c", code, lock_path, str(sleep_s)])


@pytest.mark.skipif(sys.platform == 'win32', reason='fcntl-based locking not available on Windows; CI runs this on Linux')
def test_order_lock_blocks_across_processes(tmp_path):
    lock_file = str(tmp_path / "test_order.lock")

    # start a process that holds the lock for a moment
    p = _spawn_lock_holder(lock_file, sleep_s=2.0)
    try:
        # give the child a moment to acquire
        time.sleep(0.3)

        # in this process, point to the same lock file
        import order_lock
        order_lock.LOCK_PATH = lock_file

        # immediate attempt should fail (timeout short)
        with order_lock.acquire_order_lock(timeout_seconds=0.5, poll_seconds=0.05) as locked:
            assert locked is False

        # after the child exits, we should be able to acquire
        p.wait(timeout=5)
        with order_lock.acquire_order_lock(timeout_seconds=1.0) as locked2:
            assert locked2 is True
    finally:
        try:
            p.kill()
        except Exception:
            pass
