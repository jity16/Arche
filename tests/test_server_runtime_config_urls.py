import os
import sys
import tempfile
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


@unittest.skipUnless(server is not None, f"server import failed: {_IMPORT_ERR}")
class RuntimeConfigUrlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_runtime_config_path = server.RUNTIME_CONFIG_PATH
        self.old_ui_config_enabled = server.UI_CONFIG_ENABLED
        self.client = server.app.test_client()
        server.RUNTIME_CONFIG_PATH = os.path.join(self.tmp.name, "runtime.json")
        server.UI_CONFIG_ENABLED = True
        self.old_env = {
            key: os.environ.get(key)
            for key in (
                "ARCHE_CHEM_BASE_URL",
                "GAUSSIAN_BASE_URL",
            )
        }
        os.environ["ARCHE_CHEM_BASE_URL"] = "https://expert.env.example/v1"
        os.environ["GAUSSIAN_BASE_URL"] = "https://gaussian.env.example/proxy/18081"

    def tearDown(self):
        server.RUNTIME_CONFIG_PATH = self.old_runtime_config_path
        server.UI_CONFIG_ENABLED = self.old_ui_config_enabled
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def test_config_endpoint_reads_and_writes_expert_and_gaussian_urls(self):
        response = self.client.get("/api/config")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["expertBaseUrl"], "https://expert.env.example/v1")
        self.assertEqual(payload["gaussianBaseUrl"], "https://gaussian.env.example/proxy/18081")

        update = self.client.put(
            "/api/config",
            json={
                "expertBaseUrl": "https://expert.runtime.example/v1",
                "gaussianBaseUrl": "https://gaussian.runtime.example/proxy/18081",
            },
        )
        self.assertEqual(update.status_code, 200)
        updated = update.get_json()
        self.assertEqual(updated["expertBaseUrl"], "https://expert.runtime.example/v1")
        self.assertEqual(updated["gaussianBaseUrl"], "https://gaussian.runtime.example/proxy/18081")

        overrides = server._runtime_env_overrides()
        self.assertEqual(overrides["ARCHE_CHEM_BASE_URL"], "https://expert.runtime.example/v1")
        self.assertEqual(overrides["GAUSSIAN_BASE_URL"], "https://gaussian.runtime.example/proxy/18081")

    def test_config_endpoint_falls_back_to_builtin_default_urls(self):
        os.environ.pop("ARCHE_CHEM_BASE_URL", None)
        os.environ.pop("GAUSSIAN_BASE_URL", None)

        response = self.client.get("/api/config")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(
            payload["expertBaseUrl"],
            "https://h.pjlab.org.cn/kapi/workspace.kubebrain.io/ailab-ai4chem/lyq-test-k62j9-13402-worker-0.liyuqiang/18081/v1",
        )
        self.assertEqual(
            payload["gaussianBaseUrl"],
            "https://h.pjlab.org.cn/kapi/workspace.kubebrain.io/ailab-ai4chem/lyq-test-r8488-25714-worker-0.liyuqiang/vscode/proxy/18081",
        )
