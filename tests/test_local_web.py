import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "extension"))
sys.path.insert(0, str(ROOT / "web/local"))

from fastapi.testclient import TestClient
from citations.cli import OperatorContext
from citations_web.application import create_app

spec = importlib.util.spec_from_file_location("citation_benchmark", ROOT / "scripts/benchmark.py")
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


class LocalWebTests(unittest.TestCase):
    def test_gateway_only_paging_and_committed_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            identifiers = benchmark.fixture(OperatorContext(root), 1000)
            with TestClient(create_app(root, "synthetic-gateway-token")) as client:
                self.assertEqual(client.get("/health").status_code, 403)
                client.headers["x-hosaka-gateway-token"] = "synthetic-gateway-token"
                page = client.get("/api/wall?offset=950&limit=50").json()
                self.assertEqual(page["total"], 1000)
                self.assertEqual(len(page["items"]), 50)
                body = {"action": "edit", "params": {
                    "citation": identifiers[0], "expected_revision": 0, "description": "web update",
                }}
                self.assertEqual(client.post("/api/actions", json=body).status_code, 403)
                client.headers["x-hosaka-role"] = "organizer"
                self.assertEqual(client.post("/api/actions", json=body).status_code, 200)
                self.assertEqual(client.post("/api/actions", json=body).status_code, 409)
                self.assertEqual(client.post("/api/actions", json={"action": "export"}).status_code, 403)
                self.assertEqual(client.get("/api/wall?limit=1000").status_code, 422)
