"""Behavioral checks for result accounting, report safety and Playwright import."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "full-test" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import report
import import_playwright


def case(case_id="API-001", status="PASS", **changes):
    result = {"id": case_id, "title": "Health endpoint", "type": "api", "priority": "P1", "status": status,
              "duration_ms": 12, "expected": "200 with ok=true", "actual": "HTTP 200; assertion passed", "evidence": [], "notes": []}
    result.update(changes)
    return result


def run(cases):
    return {"schema_version": 1, "run_id": "test-run", "target": "http://127.0.0.1:12345", "started_at": "2026-01-01T00:00:00+00:00",
            "finished_at": "2026-01-01T00:00:01+00:00", "cases": cases, "notes": []}


def attempt(status, duration=10, **changes):
    result = {"status": status, "duration": duration, "startTime": "2026-01-01T00:00:00Z", "attachments": [], "errors": []}
    result.update(changes)
    return result


def pw_test(project="chromium", expected="passed", status="expected", results=None):
    return {"projectName": project, "expectedStatus": expected, "status": status, "results": [attempt("passed")] if results is None else results}


def pw_spec(title, tests, **changes):
    result = {"id": title, "file": "smoke.spec.ts", "line": 10, "title": title, "tests": tests}
    result.update(changes)
    return result


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def save(self, data, name="results.json"):
        path = self.root / name
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path

    def test_all_statuses_are_counted_and_junit_never_counts_blocked_as_pass(self):
        data = run([case("PASS", "PASS"), case("FAIL", "FAIL"), case("BLOCKED", "BLOCKED"), case("SKIPPED", "SKIPPED"), case("NOT_RUN", "NOT_RUN")])
        source = self.save(data)
        output = self.root / "report"
        summary = report.generate([source], output)
        self.assertEqual(summary["total"], 5)
        self.assertEqual(summary["executed"], 2)
        self.assertEqual(summary["pass_rate_percent"], 50)
        self.assertEqual(summary["overall"], "FAIL")
        self.assertEqual(summary["release_gate"], "BLOCKED")
        self.assertEqual(summary["counts"], {status: 1 for status in report.STATUSES})
        junit = ET.parse(output / "junit.xml").getroot()
        self.assertEqual(junit.attrib["tests"], "5")
        self.assertEqual(junit.attrib["failures"], "1")
        self.assertEqual(junit.attrib["skipped"], "3")
        self.assertEqual(len(junit.findall("testcase/skipped")), 3)
        for filename in ("report.html", "report.md", "summary.json", "junit.xml"):
            self.assertTrue((output / filename).is_file())

    def test_empty_and_partial_and_flaky_runs_are_incomplete(self):
        for cases in ([], [case(), case("blocked", "BLOCKED")], [case(flaky=True)], [case("skip", "SKIPPED")]):
            summary = report.summarize(cases, [], [])
            self.assertEqual(summary["overall"], "INCOMPLETE")
            self.assertNotEqual(summary["release_gate"], "READY")
        self.assertIsNone(report.summarize([], [], [])["pass_rate_percent"])
        self.assertEqual(report.summarize([case()], [], [])["overall"], "PASS")

    def test_malicious_text_and_credentials_are_not_active_in_outputs(self):
        malicious = '<script>alert("x")</script> ![x](javascript:alert(1))'
        secret = "Authorization: Bearer secret-123 password=hunter123 https://bob:pw@example.test/?token=hidden456&ok=1"
        source = self.save(run([case(title=malicious, actual=secret, notes=["api_key='keep-this-private'", malicious])]))
        output = self.root / "report"
        report.generate([source], output)
        for filename in ("report.html", "report.md", "junit.xml", "summary.json"):
            text = (output / filename).read_text(encoding="utf-8")
            for value in ("secret-123", "hunter123", "bob:pw", "hidden456", "keep-this-private"):
                self.assertNotIn(value, text)
        html = (output / "report.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>", html)
        self.assertNotIn('href="javascript:', html)
        self.assertIn("&lt;script&gt;", html)
        md = (output / "report.md").read_text(encoding="utf-8")
        self.assertNotIn("![x](javascript:", md)
        ET.parse(output / "junit.xml")

    def test_cookie_headers_are_redacted_as_whole_lines(self):
        headers = "Cookie: sid=session-private; auth=auth-private; harmless=also-private\nSet-Cookie: sid=response-private; HttpOnly; Secure\nX-Visible: retained"
        source = self.save(run([case(actual=headers, notes=['{"Cookie": "sid=json-private; auth=json-auth-private"}'])]))
        output = self.root / "report"
        report.generate([source], output)
        for filename in ("report.html", "report.md", "junit.xml", "summary.json"):
            text = (output / filename).read_text(encoding="utf-8")
            for secret in ("session-private", "auth-private", "also-private", "response-private", "json-private", "json-auth-private"):
                self.assertNotIn(secret, text)
        self.assertIn("retained", (output / "report.html").read_text(encoding="utf-8"))

    def test_expected_failure_flag_prevents_ready_even_if_input_says_pass(self):
        summary = report.summarize([case(expected_failure=True)], [], [])
        self.assertEqual(summary["overall"], "INCOMPLETE")
        self.assertEqual(summary["expected_failures"], 1)
        self.assertNotEqual(summary["release_gate"], "READY")
        with self.assertRaises(ValueError):
            report.validate_result(run([case(expected_failure="yes")]))

    def test_only_contained_safe_evidence_is_linked_and_text_is_redacted(self):
        (self.root / "console.log").write_text("token=private-value", encoding="utf-8")
        attachments = [{"kind": "log", "path": "console.log", "note": "console"}, {"kind": "file", "path": "../outside.txt"},
                       {"kind": "file", "path": "javascript:alert(1)"}, {"kind": "file", "path": "/etc/passwd"}]
        output = self.root / "report"
        report.generate([self.save(run([case(evidence=attachments)]))], output)
        files = list((output / "evidence").iterdir())
        self.assertEqual(len(files), 1)
        self.assertNotIn("private-value", files[0].read_text())
        html = (output / "report.html").read_text(encoding="utf-8")
        self.assertIn('href="evidence/', html)
        self.assertNotIn("../outside", html)
        self.assertIn("unsafe evidence link was omitted", html)

    def test_duplicate_ids_are_deduped_only_for_identical_cases(self):
        a = self.save(run([case()]), "one.json")
        b = self.save(run([case()]), "two.json")
        self.assertEqual(report.generate([a, b], self.root / "report")["total"], 1)
        self.save(run([case(status="FAIL")]), "two.json")
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            report.generate([a, b], self.root / "not-created")
        self.assertFalse((self.root / "not-created").exists())

    def test_invalid_result_is_rejected_before_writing(self):
        for change in ({"status": "OK"}, {"duration_ms": -1}, {"duration_ms": True}, {"duration_ms": 10 ** 400}, {"priority": "critical"}, {"type": "browser"}):
            with self.subTest(change=change):
                source = self.save(run([case(**change)]))
                with self.assertRaises(ValueError):
                    report.generate([source], self.root / "not-created")
                self.assertFalse((self.root / "not-created").exists())
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            report.validate_result(run([case(), case()]))

    def test_nonempty_output_is_preserved(self):
        output = self.root / "report"
        output.mkdir()
        (output / "existing.txt").write_text("preserve me")
        with self.assertRaisesRegex(ValueError, "fresh"):
            report.generate([self.save(run([case()]))], output)
        self.assertEqual((output / "existing.txt").read_text(), "preserve me")
        self.assertEqual(len(list(output.iterdir())), 1)

    def test_invalid_execution_timestamps_are_rejected(self):
        for start, end in (("not-a-date", "2026-01-01T00:00:00Z"), ("2026-01-01", "2026-01-01"),
                           ("2026-01-02T00:00:00Z", "2026-01-01T00:00:00Z")):
            data = run([case()])
            data.update(started_at=start, finished_at=end)
            with self.subTest(start=start), self.assertRaises(ValueError):
                report.validate_result(data)

    def test_cli_gates_and_runs_from_other_working_directory(self):
        for status, expected_exit in (("PASS", 0), ("FAIL", 1), ("BLOCKED", 2)):
            source = self.save(run([case(status=status)]))
            process = subprocess.run([sys.executable, str(SCRIPTS / "report.py"), "--input", str(source), "--out-dir", str(self.root / status)], cwd=self.root, capture_output=True, text=True)
            self.assertEqual(process.returncode, expected_exit, process.stderr)
            self.assertEqual(json.loads(process.stdout)["counts"][status], 1)


class PlaywrightImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def convert(self, data):
        return import_playwright.import_report(data, self.root / "playwright.json", self.root / "ui-results.json", "http://127.0.0.1:3000")

    def test_realistic_nested_projects_retries_and_nonexecution(self):
        data = {"suites": [{"title": "smoke.spec.ts", "suites": [{"title": "Checkout", "specs": [
            pw_spec("login", [pw_test(status="flaky", results=[attempt("failed", 10, errors=[{"message": "Bearer private-token"}]), attempt("passed", 20)]), pw_test("firefox")], tags=["@smoke"]),
            pw_spec("disabled", [pw_test(expected="skipped", status="skipped", results=[attempt("skipped", 0)])]),
            pw_spec("known failure", [pw_test(expected="failed", results=[attempt("failed", 11)])]),
            pw_spec("timeout", [pw_test(status="unexpected", results=[attempt("timedOut", 9)])]),
            pw_spec("interrupted", [pw_test(status="unexpected", results=[attempt("interrupted", 3)])]),
            pw_spec("missing", [pw_test(results=[])]),
        ]}]}], "errors": [{"message": "Worker setup could not start"}]}
        converted = self.convert(data)
        cases = converted["cases"]
        self.assertEqual(len(cases), 8)
        self.assertEqual(len({entry["id"] for entry in cases}), 8)
        self.assertEqual(cases[0]["duration_ms"], 30)
        self.assertEqual(cases[0]["priority"], "P0")
        self.assertTrue(cases[0]["flaky"])
        self.assertIn("Attempt 1: failed", " ".join(cases[0]["notes"]))
        self.assertNotIn("private-token", json.dumps(converted))
        self.assertEqual(cases[3]["status"], "SKIPPED")
        self.assertTrue(cases[3]["expected_failure"])
        self.assertIn("expected failure", cases[3]["actual"])
        self.assertEqual(cases[3]["attempts"][0]["status"], "failed")
        counts = report.summarize(cases, [], [])["counts"]
        self.assertEqual(counts, {"PASS": 2, "FAIL": 1, "BLOCKED": 2, "SKIPPED": 2, "NOT_RUN": 1})
        self.assertEqual([entry["id"] for entry in self.convert(copy.deepcopy(data))["cases"]], [entry["id"] for entry in cases])

    def test_flaky_only_run_is_not_an_overall_pass(self):
        converted = self.convert({"suites": [{"title": "smoke", "specs": [pw_spec("retry", [pw_test(status="flaky", results=[attempt("failed"), attempt("passed")])])]}]})
        self.assertEqual(converted["cases"][0]["status"], "PASS")
        self.assertEqual(report.summarize(converted["cases"], [], [])["overall"], "INCOMPLETE")

    def test_expected_failure_only_run_never_becomes_ready_or_functionally_passed(self):
        converted = self.convert({"suites": [{"specs": [pw_spec("known defect", [pw_test(expected="failed", results=[attempt("failed")])])]}]})
        summary = report.summarize(converted["cases"], [], [])
        self.assertEqual(summary["overall"], "INCOMPLETE")
        self.assertEqual(summary["counts"]["PASS"], 0)
        self.assertEqual(summary["expected_failures"], 1)
        self.assertIsNone(summary["pass_rate_percent"])
        suite = ET.fromstring(report.render_junit(converted["cases"]))
        self.assertEqual(suite.attrib["skipped"], "1")
        self.assertIn("expected failure", suite.find("testcase/skipped").attrib["message"])

    def test_priority_defaults_to_p1_and_preserves_highest_explicit_priority(self):
        self.assertEqual(import_playwright.priority_for([], []), "P1")
        self.assertEqual(import_playwright.priority_for(["@P3"], []), "P3")
        self.assertEqual(import_playwright.priority_for(["@P2"], [{"type": "priority", "description": "P0"}]), "P0")
        self.assertEqual(import_playwright.priority_for(["@smoke", "@P2"], []), "P0")
        self.assertEqual(import_playwright.priority_for(["@P1", "@P0"], []), "P0")
        converted = self.convert({"suites": [{"specs": [pw_spec("untagged", [pw_test()])]}]})
        self.assertEqual(converted["cases"][0]["priority"], "P1")

    def test_unbounded_or_overflowing_times_are_controlled_input_errors(self):
        for data in (
            {"suites": [{"specs": [pw_spec("huge", [pw_test(results=[attempt("passed", 1e100)])])]}]},
            {"suites": [], "stats": {"startTime": "2026-01-01T00:00:00Z", "duration": 1e100}},
            {"suites": [{"specs": [pw_spec("overflow", [pw_test(results=[attempt("passed", 10000, startTime="9999-12-31T23:59:59Z")])])]}]},
        ):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.convert(data)
        source = self.root / "playwright.json"
        source.write_text(json.dumps({"suites": [{"specs": [pw_spec("huge", [pw_test(results=[attempt("passed", 1e100)])])]}]}))
        output = self.root / "ui-results.json"
        process = subprocess.run([sys.executable, str(SCRIPTS / "import_playwright.py"), "--input", str(source), "--output", str(output)], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(process.returncode, 2)
        self.assertNotIn("Traceback", process.stderr)
        self.assertFalse(output.exists())

    def test_attachments_remain_relative_and_outside_attachments_are_omitted(self):
        screenshot = self.root / "screens" / "login.png"
        screenshot.parent.mkdir()
        screenshot.write_bytes(b"test-image-bytes")
        attachments = [{"name": "screenshot", "contentType": "image/png", "path": str(screenshot)},
                       {"name": "external", "path": "../outside.zip"}, {"name": "inline", "body": "private"},
                       {"name": "remote", "path": "https://example.test/trace.zip"}]
        converted = self.convert({"suites": [{"title": "UI", "specs": [pw_spec("login", [pw_test(results=[attempt("passed", attachments=attachments)])])]}]})
        evidence = converted["cases"][0]["evidence"]
        self.assertEqual(evidence, [{"kind": "screenshot", "path": "screens/login.png", "note": "screenshot"}])
        self.assertIn("3 inline", " ".join(converted["cases"][0]["notes"]))

    def test_empty_and_global_error_reports_do_not_fabricate_passes(self):
        empty = self.convert({"suites": []})
        self.assertEqual(empty["cases"], [])
        self.assertEqual(report.summarize(empty["cases"], [], [])["overall"], "INCOMPLETE")
        error = self.convert({"suites": [], "errors": [{"message": "setup failed"}]})
        self.assertEqual(error["cases"][0]["status"], "BLOCKED")

    def test_bad_report_is_rejected(self):
        malformed = [{}, {"suites": "not-a-list"}, {"suites": ["not-a-suite"]}, {"suites": [{"specs": [pw_spec("bad", [pw_test(results=[attempt("unknown")])])]}]},
                     {"suites": [{"specs": [pw_spec("bad", [pw_test(results=[attempt("passed", -1)])])]}]}]
        for data in malformed:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.convert(data)

    def test_cli_writes_failed_results_but_preserves_existing_file(self):
        source = self.root / "playwright.json"
        source.write_text(json.dumps({"suites": [{"specs": [pw_spec("failure", [pw_test(status="unexpected", results=[attempt("failed")])])]}]}))
        output = self.root / "ui-results.json"
        command = [sys.executable, str(SCRIPTS / "import_playwright.py"), "--input", str(source), "--output", str(output)]
        process = subprocess.run(command, cwd=self.root, capture_output=True, text=True)
        self.assertEqual(process.returncode, 1, process.stderr)
        original = output.read_text()
        process = subprocess.run(command, cwd=self.root, capture_output=True, text=True)
        self.assertEqual(process.returncode, 2)
        self.assertEqual(output.read_text(), original)


if __name__ == "__main__":
    unittest.main()
