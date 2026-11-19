"""Keep one PaaS server process in charge of each local writable workspace."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path


class WorkspaceLease:
    """Hold before opening Store so recovery cannot interrupt a live server.

    The operating system releases the lock if the process exits or crashes.
    The marker is informational; stale marker contents never grant ownership.
    Test-client factories can continue using isolated temporary workspaces.
    """
    def __init__(self, root):
        self.root = Path(root).resolve()
        self._fd = None

    def __enter__(self):
        if self._fd is not None:
            raise RuntimeError('工作区租约已持有')
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)
        path = self.root / '.server.lock'
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            raise RuntimeError('该PaaS工作区已有运行中的服务，请使用独立工作区或先停止原服务') from None
        except BaseException:
            os.close(descriptor)
            raise
        try:
            os.fchmod(descriptor, 0o600)
            os.ftruncate(descriptor, 0)
            os.write(descriptor, json.dumps({'pid': os.getpid(), 'workspace': str(self.root)}).encode())
            os.fsync(descriptor)
        except BaseException:
            os.close(descriptor)
            raise
        self._fd = descriptor
        return self

    def __exit__(self, *args):
        if self._fd is not None:
            descriptor, self._fd = self._fd, None
            os.close(descriptor)

