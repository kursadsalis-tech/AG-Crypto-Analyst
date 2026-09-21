import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from runtime.paper_realtime_worker import _atomic_json_write


class AtomicJsonWriteTests(unittest.TestCase):
    def test_parallel_writers_publish_complete_json(self):
        writers = 8
        barrier = threading.Barrier(writers)
        replace = os.replace

        def synchronized_replace(source, destination):
            barrier.wait(timeout=10)
            replace(source, destination)

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "health.json"
            payloads = [{"writer": i, "data": "x" * 4096} for i in range(writers)]
            with patch("runtime.paper_realtime_worker.os.replace", synchronized_replace):
                with ThreadPoolExecutor(max_workers=writers) as pool:
                    futures = [pool.submit(_atomic_json_write, target, p) for p in payloads]
                    for future in futures:
                        future.result(timeout=20)
            self.assertIn(json.loads(target.read_text()), payloads)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_failed_replace_preserves_target_and_cleans_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "health.json"
            _atomic_json_write(target, {"previous": True})
            with patch("runtime.paper_realtime_worker.os.replace", side_effect=OSError("failed")):
                with self.assertRaisesRegex(OSError, "failed"):
                    _atomic_json_write(target, {"next": True})
            self.assertEqual(json.loads(target.read_text()), {"previous": True})
            self.assertEqual(list(Path(directory).iterdir()), [target])
