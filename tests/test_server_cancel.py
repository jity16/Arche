import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


def find_project_root() -> Path:
    current = Path(__file__).resolve()
    for parent in [current.parent, *current.parents]:
        if (parent / "server.py").exists():
            return parent
    raise RuntimeError("Cannot infer ARCHE project root")


PROJECT_ROOT = find_project_root()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    import server

    _IMPORT_ERR = None
except Exception as exc:  # noqa: BLE001
    server = None  # type: ignore[assignment]
    _IMPORT_ERR = exc


class FakeLongRunningProcess:
    instances = []
    started = threading.Event()

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.returncode = None
        self._killed = threading.Event()
        self.stdout = self
        FakeLongRunningProcess.instances.append(self)
        FakeLongRunningProcess.started.set()

    def __iter__(self):
        return self

    def __next__(self):
        if self._killed.is_set():
            raise StopIteration
        time.sleep(0.02)
        return "heartbeat\n"

    def poll(self):
        return self.returncode

    def terminate(self):
        self.kill()

    def kill(self):
        self.returncode = -9
        self._killed.set()

    def wait(self, timeout=None):
        if self.returncode is None:
            self._killed.wait(timeout if timeout is not None else 1)
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


@unittest.skipUnless(server is not None, f"server import failed: {_IMPORT_ERR}")
class RunCancelEndpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_history = server.HISTORY_PATH
        self.old_popen = server.subprocess.Popen
        self.old_timeout = server.RUN_TIMEOUT
        server.HISTORY_PATH = os.path.join(self.tmp.name, "history.jsonl")
        server.RUN_TIMEOUT = 30
        server.subprocess.Popen = FakeLongRunningProcess
        FakeLongRunningProcess.instances.clear()
        FakeLongRunningProcess.started.clear()
        if hasattr(server, "_active_runs"):
            with server._active_runs_lock:
                server._active_runs.clear()
        self.client = server.app.test_client()

    def tearDown(self):
        for proc in FakeLongRunningProcess.instances:
            proc.kill()
        if hasattr(server, "_active_runs"):
            with server._active_runs_lock:
                server._active_runs.clear()
        server.subprocess.Popen = self.old_popen
        server.HISTORY_PATH = self.old_history
        server.RUN_TIMEOUT = self.old_timeout
        self.tmp.cleanup()

    def test_cancel_stream_run_terminates_process_and_marks_history_cancelled(self):
        response = self.client.post("/api/run/stream", json={"question": "cancel me"}, buffered=False)
        try:
            start = json.loads(next(response.response).decode("utf-8"))
            self.assertEqual(start["type"], "start")
            run_id = start["id"]
            self.assertTrue(FakeLongRunningProcess.started.wait(1.0))

            cancel_response = self.client.post(f"/api/runs/{run_id}/cancel")
            cancel_status = cancel_response.status_code
            cancel_payload = cancel_response.get_json(silent=True) or {}
        finally:
            for proc in FakeLongRunningProcess.instances:
                proc.kill()

        self.assertEqual(cancel_status, 200)
        self.assertEqual(cancel_payload.get("cancelled"), True)
        self.assertTrue(FakeLongRunningProcess.instances[-1]._killed.is_set())

        deadline = time.time() + 1.5
        record = None
        while time.time() < deadline:
            records = server._read_history()
            record = records[-1] if records else None
            if record and record.get("status") == "cancelled":
                break
            time.sleep(0.03)
        self.assertIsNotNone(record)
        self.assertEqual(record.get("status"), "cancelled")
        self.assertNotEqual(record.get("status"), "running")
        self.assertIn("cancelled", record.get("stderr", "").lower())
        response.close()


if __name__ == "__main__":
    unittest.main()
