import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from paas.workspace_lock import WorkspaceLease


class WorkspaceLeaseTests(unittest.TestCase):
    def child(self, path):
        script = ('import sys; from paas.workspace_lock import WorkspaceLease; '
                  'lease=WorkspaceLease(sys.argv[1]); lease.__enter__(); lease.__exit__()')
        return subprocess.run([sys.executable, '-c', script, str(path)],
                              cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=10)

    def test_other_process_cannot_claim_workspace_until_original_releases(self):
        with tempfile.TemporaryDirectory(prefix='paas-lease-') as directory:
            with WorkspaceLease(directory):
                denied = self.child(directory)
                self.assertNotEqual(denied.returncode, 0)
                self.assertIn('已有运行中的服务', denied.stderr)
            accepted = self.child(directory)
            self.assertEqual(accepted.returncode, 0, accepted.stderr)

    def test_independent_roots_do_not_block_each_other_and_stale_marker_is_ignored(self):
        with tempfile.TemporaryDirectory(prefix='paas-lease-') as directory:
            first = Path(directory) / 'first'
            second = Path(directory) / 'second'
            second.mkdir()
            (second / '.server.lock').write_text('{"pid":99999999}')
            with WorkspaceLease(first):
                accepted = self.child(second)
                self.assertEqual(accepted.returncode, 0, accepted.stderr)
            self.assertEqual((second / '.server.lock').stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
