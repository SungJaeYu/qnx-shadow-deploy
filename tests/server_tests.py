"""Check the actual local HTTP surface without a browser dependency."""
import http.client
import importlib.util
import json
from pathlib import Path
import sys
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
BUILD = Path(sys.argv[1]).resolve()
sys.argv = [sys.argv[0]]
sys.path.insert(0, str(ROOT / "tools"))
spec = importlib.util.spec_from_file_location("workbench_server", ROOT / "tools" / "serve.py")
server_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server_module)


class WorkbenchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = server_module.ThreadingHTTPServer(("127.0.0.1", 0), server_module.Handler)
        cls.server.build_dir = BUILD
        # This suite verifies HTTP wiring, not a 10 ms bound on a shared CI host.
        # Tight deadline boundaries are deterministic policy tests; target timing
        # requires separate evidence. The live workbench keeps its 20/10 ms defaults.
        cls.server.demo_options = ["--samples", "12", "--period-ms", "100", "--deadline-ms", "75"]
        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join()
        cls.server.server_close()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        connection.request(method, path, body, headers or {})
        response = connection.getresponse()
        status, payload = response.status, response.read()
        connection.close()
        return status, payload

    def test_static_assets(self):
        for path in ["/", "/app.js", "/styles.css"]:
            status, body = self.request("GET", path)
            self.assertEqual(status, 200)
            self.assertGreater(len(body), 100)

    def test_rejects_foreign_origins_and_paths(self):
        self.assertEqual(self.request("GET", "/", headers={"Host": "attacker.invalid"})[0], 403)
        self.assertEqual(self.request("GET", "/", headers={"Origin": "https://attacker.invalid"})[0], 403)
        self.assertEqual(self.request("GET", "/../README.md")[0], 404)
        self.assertEqual(self.request("POST", "/api/run", "{}", {"Content-Type": "text/plain"})[0], 400)

    def test_run_returns_real_process_evidence(self):
        status, payload = self.request("POST", "/api/run", json.dumps({"scenario": "reject-units"}), {"Content-Type": "application/json"})
        self.assertEqual(status, 200)
        rows = [json.loads(line) for line in payload.splitlines()]
        evidence = BUILD / "test-artifacts"
        evidence.mkdir(exist_ok=True)
        (evidence / "workbench-api.jsonl").write_bytes(payload)
        decisions = [row for row in rows if row.get("event")]
        diagnostics = json.dumps({"summary": rows[-1], "decisions": decisions}, indent=2)
        self.assertEqual(rows[0]["backend"], "host-posix-pipes")
        self.assertEqual(rows[-1]["phase"], "rejected", diagnostics)
        self.assertEqual(rows[-1]["samples"], 12, diagnostics)
        self.assertEqual(rows[0]["deadline_ms"], 75)
        self.assertIn("candidate_rejected", [row.get("event") for row in rows])


if __name__ == "__main__":
    unittest.main(verbosity=2)
