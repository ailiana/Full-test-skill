"""Behavioral tests using only an ephemeral localhost server."""
from __future__ import annotations

import contextlib
import copy
import http.server
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "full-test" / "scripts" / "run_api.py"
if not SCRIPT.exists():  # Allow the isolated staging directory during authoring.
    SCRIPT = Path(__file__).with_name("run_api.py")
SPEC = importlib.util.spec_from_file_location("full_test_run_api", SCRIPT)
api = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(api)


class Handler(http.server.BaseHTTPRequestHandler):
    hits = []
    redirect_to = ""

    def log_message(self, *args):
        pass

    def do_GET(self):
        Handler.hits.append((self.command, self.path, self.headers.get("Authorization")))
        if self.path == "/slow":
            time.sleep(0.15)
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", Handler.redirect_to)
            self.end_headers()
            return
        if self.path == "/large":
            payload = b"x" * (api.MAX_RESPONSE_BYTES + 1)
        elif self.path == "/truncated":
            payload = b'{"ok": true}'
        elif self.path == "/nan":
            payload = b'{"ok": true, "value": NaN}'
        elif self.path == "/infinity":
            payload = b'{"ok": true, "value": Infinity}'
        elif self.path == "/negative-infinity":
            payload = b'{"ok": true, "value": -Infinity}'
        elif self.path in {"/empty204", "/not-modified"}:
            payload = b""
        elif self.path == "/number":
            payload = b'{"ok": 1}'
        elif self.path == "/bad-json":
            payload = b"not JSON"
        else:
            payload = json.dumps({"ok": True, "count": 1, "nested": [{"name": "demo"}],
                                  "echo": self.headers.get("Authorization", "")}).encode()
        status = {"/not-found": 404, "/empty204": 204, "/not-modified": 304}.get(self.path, 200)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        declared = len(payload) + 20 if self.path in {"/truncated", "/empty204", "/not-modified"} else len(payload)
        self.send_header("Content-Length", str(declared))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except OSError:
            pass

    def do_HEAD(self):
        Handler.hits.append((self.command, self.path, self.headers.get("Authorization")))
        self.send_response(200)
        self.send_header("Content-Length", "1024")
        self.end_headers()

    def do_POST(self):
        Handler.hits.append((self.command, self.path, self.headers.get("Authorization")))
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        payload = json.dumps({"ok": body.get("ok"), "secret": body.get("secret")}).encode()
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class ApiRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:" + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        Handler.hits = []
        self.no_proxy = mock.patch.dict(os.environ, {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
        self.no_proxy.start()
        self.addCleanup(self.no_proxy.stop)

    def plan(self, **case_changes):
        case = {"id": "API-001", "title": "Health is ready", "priority": "P0", "method": "GET", "path": "/ok",
                "expect": {"status": 200, "json": {"$.ok": True, "$.nested[0].name": "demo"}}}
        case.update(case_changes)
        return {"base_url": self.base, "timeout_seconds": 2, "cases": [case]}

    def test_pass_checks_json_and_text_with_evidence_safe_summary(self):
        plan = self.plan()
        plan["cases"][0]["expect"].update({"body_contains": "nested", "max_duration_ms": 2000})
        result = api.run_plan(plan)
        self.assertEqual(result["cases"][0]["status"], "PASS")
        self.assertIn("5/5", result["cases"][0]["actual"])
        self.assertEqual(result["cases"][0]["evidence"], [])
        self.assertEqual(api.exit_code(result), 0)

    def test_http_error_is_testable_and_expected_404_passes(self):
        result = api.run_plan(self.plan(path="/not-found", expect={"status": [404, 410]}))
        self.assertEqual(result["cases"][0]["status"], "PASS")

    def test_failed_status_and_missing_json_path_are_failures(self):
        result = api.run_plan(self.plan(path="/not-found", expect={"status": 200, "json": {"$.missing": True}}))
        self.assertEqual(result["cases"][0]["status"], "FAIL")
        self.assertEqual(len(result["cases"][0]["notes"]), 2)
        self.assertEqual(api.exit_code(result), 1)

    def test_json_numbers_do_not_pass_boolean_assertions(self):
        result = api.run_plan(self.plan(path="/number", expect={"status": 200, "json": {"$.ok": True}}))
        self.assertEqual(result["cases"][0]["status"], "FAIL")

    def test_invalid_json_is_failed_without_echoing_the_body(self):
        result = api.run_plan(self.plan(path="/bad-json"))
        self.assertEqual(result["cases"][0]["status"], "FAIL")
        self.assertNotIn("not JSON", json.dumps(result))

    def test_nonstandard_json_constants_are_failed_even_when_unasserted(self):
        for path in ("/nan", "/infinity", "/negative-infinity"):
            with self.subTest(path=path):
                result = api.run_plan(self.plan(path=path, expect={"status": 200, "json": {"$.ok": True}}))
                self.assertEqual(result["cases"][0]["status"], "FAIL")
                self.assertIn("JSON response decoding", result["cases"][0]["notes"][0])

    def test_truncated_valid_json_is_blocked_before_assertions(self):
        result = api.run_plan(self.plan(path="/truncated", expect={"status": 200, "json": {"$.ok": True}}))
        self.assertEqual(result["cases"][0]["status"], "BLOCKED")
        self.assertIn("incomplete response body", result["cases"][0]["actual"])

    def test_bodyless_http_semantics_do_not_fail_length_checks(self):
        cases = [("HEAD", "/ok", 200), ("GET", "/empty204", 204), ("GET", "/not-modified", 304)]
        for method, path, status in cases:
            with self.subTest(method=method, path=path):
                result = api.run_plan(self.plan(method=method, path=path, expect={"status": status}))
                self.assertEqual(result["cases"][0]["status"], "PASS")

    def test_dry_run_has_no_network_and_is_not_pass(self):
        with mock.patch.object(api.urllib.request, "build_opener", side_effect=AssertionError("Network prepared")):
            result = api.run_plan(self.plan(), dry_run=True)
        self.assertEqual(result["cases"][0]["status"], "NOT_RUN")
        self.assertEqual(Handler.hits, [])
        self.assertEqual(api.exit_code(result), 2)

    def test_write_guard_blocks_entire_mixed_plan_before_reads(self):
        plan = self.plan()
        plan["cases"].append({"id": "API-002", "title": "Create synthetic record", "method": "POST", "path": "/items",
                              "body": {"ok": True}, "expect": {"status": 201}})
        result = api.run_plan(plan)
        self.assertEqual({c["status"] for c in result["cases"]}, {"BLOCKED"})
        self.assertEqual(Handler.hits, [])

    def test_explicit_write_flag_allows_json_body(self):
        result = api.run_plan(self.plan(method="POST", path="/items", body={"ok": True},
                                        expect={"status": 201, "json": {"$.ok": True}}), allow_write=True)
        self.assertEqual(result["cases"][0]["status"], "PASS")
        self.assertEqual(Handler.hits[0][0], "POST")

    def test_missing_environment_blocks_before_any_requests(self):
        plan = self.plan()
        plan["headers"] = {"Authorization": "Bearer ${FULL_TEST_MISSING_TOKEN}"}
        with mock.patch.dict(os.environ, {}, clear=True):
            result = api.run_plan(plan)
        self.assertEqual(result["cases"][0]["status"], "BLOCKED")
        self.assertEqual(Handler.hits, [])

    def test_secret_headers_and_body_are_used_but_not_logged(self):
        secret = "test-secret-DO-NOT-LOG-813"
        plan = self.plan(method="POST", path="/items", body={"ok": True, "secret": "${FULL_TEST_TOKEN}"},
                         expect={"status": 201, "json": {"$.secret": "${FULL_TEST_TOKEN}"}})
        plan["headers"] = {"Authorization": "Bearer ${FULL_TEST_TOKEN}"}
        with mock.patch.dict(os.environ, {"FULL_TEST_TOKEN": secret}):
            result = api.run_plan(plan, allow_write=True)
        self.assertEqual(result["cases"][0]["status"], "PASS")
        self.assertEqual(Handler.hits[0][2], "Bearer " + secret)
        self.assertNotIn(secret, json.dumps(result))
        self.assertNotIn("Authorization", json.dumps(result))

    def test_redirect_is_not_followed_and_auth_never_reaches_other_origin(self):
        sink_hits = []

        class Sink(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                sink_hits.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.end_headers()

        sink = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Sink)
        thread = threading.Thread(target=sink.serve_forever, daemon=True)
        thread.start()
        try:
            Handler.redirect_to = f"http://127.0.0.1:{sink.server_port}/capture"
            plan = self.plan(path="/redirect", expect={"status": 302})
            plan["headers"] = {"Authorization": "Bearer LOCAL-SYNTHETIC-ONLY"}
            result = api.run_plan(plan)
            self.assertEqual(result["cases"][0]["status"], "PASS")
            self.assertEqual(sink_hits, [])
        finally:
            sink.shutdown()
            sink.server_close()
            thread.join(timeout=2)

    def test_timeout_is_blocked_and_does_not_claim_assertions(self):
        plan = self.plan(path="/slow")
        plan["timeout_seconds"] = 0.02
        result = api.run_plan(plan)
        self.assertEqual(result["cases"][0]["status"], "BLOCKED")
        self.assertIn("no response assertions", result["cases"][0]["actual"])

    def test_refused_connection_is_blocked(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        result = api.run_plan(self.plan(), base_url=f"http://127.0.0.1:{port}")
        self.assertEqual(result["cases"][0]["status"], "BLOCKED")

    def test_oversize_response_is_blocked(self):
        result = api.run_plan(self.plan(path="/large", expect={"status": 200}))
        self.assertEqual(result["cases"][0]["status"], "BLOCKED")

    def test_invalid_later_case_prevents_earlier_request(self):
        plan = self.plan()
        invalid = copy.deepcopy(plan["cases"][0])
        invalid.update({"id": "API-002", "path": "https://example.invalid/"})
        plan["cases"].append(invalid)
        with self.assertRaises(api.PlanError):
            api.run_plan(plan)
        self.assertEqual(Handler.hits, [])

    def test_invalid_inputs_are_rejected_before_network(self):
        plans = []
        for bad_path in ("//example.invalid/", "/\\example.invalid", "/ok#fragment", "/ok\r\nX: y", "/space here"):
            plans.append(self.plan(path=bad_path))
        for bad_base in ("http://user:password@127.0.0.1", "file:///tmp", "http://127.0.0.1?token=secret"):
            plan = self.plan()
            plan["base_url"] = bad_base
            plans.append(plan)
        plans.extend([self.plan(priority="critical"), self.plan(expect={"status": True}), self.plan(method="TRACE"),
                      self.plan(headers={"Host": "example.invalid"}), self.plan(headers={"X-A": "one", "x-a": "two"})])
        plan = self.plan()
        plan["cases"] = []
        plans.append(plan)
        plan = self.plan()
        plan["cases"].append(copy.deepcopy(plan["cases"][0]))
        plans.append(plan)
        for plan in plans:
            with self.subTest(plan=plan), self.assertRaises(api.PlanError):
                api.run_plan(plan)
        self.assertEqual(Handler.hits, [])

    def test_very_large_integer_timeout_and_latency_are_controlled_plan_errors(self):
        for name in ("timeout_seconds", "max_duration_ms"):
            plan = self.plan()
            if name == "timeout_seconds":
                plan[name] = 10 ** 400
            else:
                plan["cases"][0]["expect"][name] = 10 ** 400
            with self.subTest(name=name), self.assertRaises(api.PlanError):
                api.run_plan(plan)
        self.assertEqual(Handler.hits, [])

    def test_cli_rejects_overflowing_numeric_inputs_with_exit_two(self):
        with tempfile.TemporaryDirectory() as temp:
            plan_path, output = Path(temp) / "plan.json", Path(temp) / "result.json"
            for name in ("timeout_seconds", "max_duration_ms"):
                plan = self.plan()
                if name == "timeout_seconds":
                    plan[name] = 10 ** 400
                else:
                    plan["cases"][0]["expect"][name] = 10 ** 400
                plan_path.write_text(json.dumps(plan), encoding="utf-8")
                completed = subprocess.run([sys.executable, str(SCRIPT), "--plan", str(plan_path), "--output", str(output)],
                                           capture_output=True, text=True, timeout=10)
                with self.subTest(name=name):
                    self.assertEqual(completed.returncode, 2)
                    self.assertIn("Invalid plan:", completed.stderr)
                    self.assertNotIn("Traceback", completed.stderr)
                    self.assertFalse(output.exists())
        self.assertEqual(Handler.hits, [])

    def test_env_cannot_inject_headers_or_change_the_origin(self):
        plan = self.plan(headers={"X-Test": "${FULL_TEST_TOKEN}"})
        with mock.patch.dict(os.environ, {"FULL_TEST_TOKEN": "bad\r\nHeader: injected"}), self.assertRaises(api.PlanError):
            api.run_plan(plan)
        plan = self.plan(path="/${FULL_TEST_TOKEN}")
        with mock.patch.dict(os.environ, {"FULL_TEST_TOKEN": "/example.invalid"}), self.assertRaises(api.PlanError):
            api.run_plan(plan)
        self.assertEqual(Handler.hits, [])

    def test_cli_writes_utf8_json_and_returns_correct_exit_codes(self):
        with tempfile.TemporaryDirectory() as temp:
            plan_path = Path(temp) / "plan.json"
            output = Path(temp) / "result.json"
            plan_path.write_text(json.dumps(self.plan(title="Synthetic login health")), encoding="utf-8")
            completed = subprocess.run([sys.executable, str(SCRIPT), "--plan", str(plan_path), "--output", str(output)],
                                       capture_output=True, text=True, timeout=10)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["cases"][0]["status"], "PASS")
            dry_output = Path(temp) / "dry-run.json"
            completed = subprocess.run([sys.executable, str(SCRIPT), "--plan", str(plan_path), "--output", str(dry_output), "--dry-run"],
                                       capture_output=True, text=True, timeout=10)
            self.assertEqual(completed.returncode, 2, completed.stderr)
            self.assertEqual(json.loads(dry_output.read_text(encoding="utf-8"))["cases"][0]["status"], "NOT_RUN")

    def test_existing_output_is_preserved_and_no_request_is_sent(self):
        with tempfile.TemporaryDirectory() as temp:
            plan_path, output = Path(temp) / "plan.json", Path(temp) / "existing.json"
            plan_path.write_text(json.dumps(self.plan()), encoding="utf-8")
            original = b"prior run must stay byte-for-byte intact\n"
            output.write_bytes(original)
            with contextlib.redirect_stderr(io.StringIO()):
                code = api.main(["--plan", str(plan_path), "--output", str(output)])
            self.assertEqual(code, 2)
            self.assertEqual(output.read_bytes(), original)
            self.assertEqual(Handler.hits, [])

    def test_output_creation_race_does_not_replace_the_other_run(self):
        with tempfile.TemporaryDirectory() as temp:
            plan_path, output = Path(temp) / "plan.json", Path(temp) / "concurrent.json"
            plan_path.write_text(json.dumps(self.plan()), encoding="utf-8")
            original = b"a concurrently completed run\n"
            real_run = api.run_plan

            def concurrent_run(*args, **kwargs):
                result = real_run(*args, **kwargs)
                output.write_bytes(original)
                return result

            with mock.patch.object(api, "run_plan", side_effect=concurrent_run), contextlib.redirect_stderr(io.StringIO()):
                code = api.main(["--plan", str(plan_path), "--output", str(output)])
            self.assertEqual(code, 2)
            self.assertEqual(output.read_bytes(), original)
            self.assertEqual(len(Handler.hits), 1)
            self.assertEqual(list(Path(temp).glob("*.tmp")), [])

    def test_cli_invalid_plan_does_not_expose_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            plan = self.plan()
            secret = "secret-in-invalid-header-941"
            plan["headers"] = {"Host": secret}
            plan_path = Path(temp) / "plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            completed = subprocess.run([sys.executable, str(SCRIPT), "--plan", str(plan_path), "--output", str(Path(temp) / "result.json")],
                                       capture_output=True, text=True, timeout=10)
            self.assertEqual(completed.returncode, 2)
            self.assertNotIn(secret, completed.stdout + completed.stderr)
            self.assertFalse((Path(temp) / "result.json").exists())


if __name__ == "__main__":
    unittest.main()
