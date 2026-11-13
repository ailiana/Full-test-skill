#!/usr/bin/env python3
"""Run a small, explicit HTTP test plan with Python's standard library.

No redirect following, response-body logging, automatic retries, or TLS bypass.
This helper executes an agent-authored plan; it does not infer test coverage.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import http.client
import json
import math
import os
from pathlib import Path
import re
import socket
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

READ_METHODS = {"GET", "HEAD", "OPTIONS"}
METHODS = READ_METHODS | {"POST", "PUT", "PATCH", "DELETE"}
PRIORITIES = {"P0", "P1", "P2", "P3"}
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
JSON_PATH = re.compile(r"^\$(?:\.[A-Za-z_][A-Za-z0-9_-]*|\[\d+\])*$")
FORBIDDEN_HEADERS = {"host", "proxy-authorization", "content-length", "transfer-encoding", "connection"}


class PlanError(ValueError):
    """A validation error whose message does not include request contents."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def _number(value, label, minimum=0, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlanError(f"{label} must be a number")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite or value <= minimum or (maximum is not None and value > maximum):
        raise PlanError(f"{label} is outside the supported range")


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise PlanError(f"{label} must be a nonempty string")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise PlanError(f"{label} contains a control character")


def _keys(value, allowed, label):
    if not isinstance(value, dict):
        raise PlanError(f"{label} must be an object")
    if not set(value).issubset(allowed):
        raise PlanError(f"{label} contains an unsupported field")


def _base_url(value):
    _text(value, "base_url")
    try:
        parsed = urllib.parse.urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or "\\" in value
                or any(c.isspace() for c in value)):
            raise ValueError
        parsed.port  # Also validate malformed and out-of-range ports.
        value.encode("ascii")
    except (ValueError, UnicodeError):
        raise PlanError("base_url must be an ASCII HTTP(S) URL without credentials, query, or fragment") from None
    return value.rstrip("/")


def _headers(value, label):
    if not isinstance(value, dict):
        raise PlanError(f"{label} must be an object")
    seen = set()
    for name, content in value.items():
        if not isinstance(name, str) or not HEADER_NAME.fullmatch(name):
            raise PlanError(f"{label} has an invalid header name")
        normalized = name.lower()
        if normalized in seen or normalized in FORBIDDEN_HEADERS:
            raise PlanError(f"{label} has a duplicate or unsupported transport header")
        seen.add(normalized)
        if not isinstance(content, str) or any(ord(c) < 32 or ord(c) == 127 for c in content):
            raise PlanError(f"{label} has an invalid header value")
        try:
            content.encode("latin-1")
        except UnicodeError:
            raise PlanError(f"{label} values must be HTTP-compatible strings") from None


def _path(value):
    _text(value, "case.path")
    if not value.startswith("/") or value.startswith("//") or "\\" in value or any(c.isspace() for c in value):
        raise PlanError("case.path must start with exactly one slash")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        raise PlanError("case.path must be origin-relative without a fragment")
    try:
        value.encode("ascii")
    except UnicodeError:
        raise PlanError("case.path must be ASCII; percent-encode non-ASCII characters") from None


def _json_value(value, label):
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError):
        raise PlanError(f"{label} must contain finite JSON values") from None


def validate_plan(raw, base_url=None):
    """Validate the complete plan without accessing environment values or network."""
    _keys(raw, {"schema_version", "base_url", "headers", "timeout_seconds", "cases"}, "plan")
    if "schema_version" in raw and (type(raw["schema_version"]) is not int or raw["schema_version"] != 1):
        raise PlanError("plan.schema_version must be 1")
    plan = copy.deepcopy(raw)
    plan["base_url"] = _base_url(base_url if base_url is not None else plan.get("base_url"))
    plan.setdefault("headers", {})
    _headers(plan["headers"], "plan.headers")
    plan.setdefault("timeout_seconds", 10)
    _number(plan["timeout_seconds"], "timeout_seconds", maximum=300)
    cases = plan.get("cases")
    if not isinstance(cases, list) or not cases:
        raise PlanError("plan.cases must be a nonempty array")
    seen = set()
    for case in cases:
        _keys(case, {"id", "title", "priority", "method", "path", "headers", "body", "expect"}, "case")
        for name in ("id", "title"):
            _text(case.get(name), f"case.{name}")
        if case["id"] in seen:
            raise PlanError("case IDs must be unique")
        seen.add(case["id"])
        case.setdefault("priority", "P1")
        if not isinstance(case["priority"], str) or case["priority"] not in PRIORITIES:
            raise PlanError("case.priority must be P0, P1, P2, or P3")
        if not isinstance(case.get("method"), str) or case["method"].upper() not in METHODS:
            raise PlanError("case.method is unsupported")
        case["method"] = case["method"].upper()
        _path(case.get("path"))
        case.setdefault("headers", {})
        _headers(case["headers"], "case.headers")
        if "body" in case:
            if case["method"] in READ_METHODS:
                raise PlanError("GET, HEAD, and OPTIONS bodies are unsupported")
            _json_value(case["body"], "case.body")
        expected = case.get("expect")
        _keys(expected, {"status", "json", "body_contains", "max_duration_ms"}, "case.expect")
        statuses = expected.get("status")
        statuses = statuses if isinstance(statuses, list) else [statuses]
        if not statuses or any(type(code) is not int or not 100 <= code <= 599 for code in statuses):
            raise PlanError("expect.status must be an HTTP status or a nonempty array of statuses")
        if "json" in expected:
            if not isinstance(expected["json"], dict):
                raise PlanError("expect.json must map JSON paths to expected values")
            for path, value in expected["json"].items():
                if not isinstance(path, str) or not JSON_PATH.fullmatch(path):
                    raise PlanError("expect.json supports only simple $.field and [index] paths")
                _json_value(value, "expect.json")
        if "body_contains" in expected and not isinstance(expected["body_contains"], str):
            raise PlanError("expect.body_contains must be a string")
        if "max_duration_ms" in expected:
            _number(expected["max_duration_ms"], "expect.max_duration_ms")
    return plan


def _expand(value, missing, secrets):
    if isinstance(value, str):
        def replace(match):
            name = match.group(1)
            if name not in os.environ:
                missing.add(name)
                return match.group(0)
            secret = os.environ[name]
            if secret:
                secrets.add(secret)
            return secret
        return ENV_PATTERN.sub(replace, value)
    if isinstance(value, list):
        return [_expand(item, missing, secrets) for item in value]
    if isinstance(value, dict):
        return {key: _expand(item, missing, secrets) for key, item in value.items()}
    return value


def _redact(text, secrets):
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    return text


def _expectation(expected):
    codes = expected["status"] if isinstance(expected["status"], list) else [expected["status"]]
    parts = ["HTTP " + "/".join(map(str, codes))]
    if expected.get("json"):
        parts.append(f"{len(expected['json'])} JSON assertion(s)")
    if "body_contains" in expected:
        parts.append("text assertion")
    if "max_duration_ms" in expected:
        parts.append("latency threshold")
    return "; ".join(parts)


def _case_result(case, status, actual, secrets, duration_ms=0, notes=None):
    return {
        "id": _redact(case["id"], secrets),
        "title": _redact(case["title"], secrets),
        "type": "api", "priority": case["priority"], "status": status,
        "duration_ms": round(duration_ms, 3),
        "expected": _expectation(case["expect"]),
        "actual": actual, "evidence": [], "notes": notes or [],
    }


def _lookup(document, path):
    current = document
    for field, index in re.findall(r"\.([A-Za-z_][A-Za-z0-9_-]*)|\[(\d+)\]", path[1:]):
        if field:
            if not isinstance(current, dict) or field not in current:
                raise LookupError
            current = current[field]
        else:
            if not isinstance(current, list) or int(index) >= len(current):
                raise LookupError
            current = current[int(index)]
    return current


def _json_equal(actual, expected):
    # JSON booleans are not numbers, even though bool subclasses int in Python.
    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    if isinstance(actual, dict) and isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(_json_equal(actual[k], expected[k]) for k in actual)
    if isinstance(actual, list) and isinstance(expected, list):
        return len(actual) == len(expected) and all(_json_equal(a, b) for a, b in zip(actual, expected))
    return actual == expected


def _declared_length(headers, method, status):
    # These responses have no message body; Content-Length can describe a
    # hypothetical GET representation (HEAD and 304), not bytes sent here.
    if method == "HEAD" or status in {204, 304} or 100 <= status < 200:
        return None
    if headers.get("Transfer-Encoding"):
        return None  # Transfer-Encoding takes precedence over Content-Length.
    lengths = headers.get_all("Content-Length", [])
    if not lengths:
        return None
    tokens = [part.strip() for value in lengths for part in value.split(",")]
    if any(not re.fullmatch(r"[0-9]+", token) for token in tokens):
        raise ValueError("Invalid response Content-Length")
    lengths = [int(token) for token in tokens]
    if len(set(lengths)) != 1:
        raise ValueError("Conflicting response Content-Length values")
    return lengths[0]


def _reject_json_constant(value):
    raise ValueError("Non-finite JSON constant")


def _execute(opener, plan, case, secrets):
    headers = {name.lower(): value for name, value in plan["headers"].items()}
    headers.update({name.lower(): value for name, value in case["headers"].items()})
    data = None
    if "body" in case:
        data = json.dumps(case["body"], ensure_ascii=False, allow_nan=False).encode("utf-8")
        headers.setdefault("content-type", "application/json")
    request = urllib.request.Request(plan["base_url"] + case["path"], data=data, headers=headers, method=case["method"])
    started = time.perf_counter()
    try:
        try:
            response = opener.open(request, timeout=plan["timeout_seconds"])
        except urllib.error.HTTPError as error:
            response = error  # Error and redirect responses still have testable status codes.
        with response:
            code = response.code
            declared_length = _declared_length(response.headers, case["method"], code)
            body = response.read(MAX_RESPONSE_BYTES + 1)
            charset = response.headers.get_content_charset() or "utf-8"
        elapsed = (time.perf_counter() - started) * 1000
    except (urllib.error.URLError, OSError, socket.timeout, http.client.HTTPException, ValueError):
        return _case_result(case, "BLOCKED", "Request unavailable; no response assertions completed", secrets,
                            (time.perf_counter() - started) * 1000,
                            ["Check endpoint reachability, TLS, proxy configuration, and request timeout."])
    if len(body) > MAX_RESPONSE_BYTES:
        return _case_result(case, "BLOCKED", f"HTTP {code}; response exceeded the 2 MiB assertion limit", secrets, elapsed)
    if declared_length is not None and len(body) != declared_length:
        return _case_result(case, "BLOCKED", f"HTTP {code}; incomplete response body; no response assertions completed", secrets, elapsed)
    expected = case["expect"]
    codes = expected["status"] if isinstance(expected["status"], list) else [expected["status"]]
    checks = [("HTTP status", code in codes)]
    if expected.get("json"):
        try:
            document = json.loads(body, parse_constant=_reject_json_constant)
        except (ValueError, UnicodeError):
            checks.append(("JSON response decoding", False))
        else:
            for number, (path, value) in enumerate(expected["json"].items(), 1):
                try:
                    actual = _lookup(document, path)
                    equal = _json_equal(actual, value)
                except LookupError:
                    equal = False
                checks.append((f"JSON assertion {number}", equal))
    if "body_contains" in expected:
        try:
            decoded = body.decode(charset)
            matches = expected["body_contains"] in decoded
        except (LookupError, UnicodeError):
            matches = False
        checks.append(("Body text assertion", matches))
    if "max_duration_ms" in expected:
        checks.append(("Latency threshold", elapsed <= expected["max_duration_ms"]))
    passed = sum(success for _, success in checks)
    notes = [f"Assertion failed: {name}" for name, success in checks if not success]
    return _case_result(case, "PASS" if passed == len(checks) else "FAIL",
                        f"HTTP {code}; {passed}/{len(checks)} assertions passed", secrets, elapsed, notes)


def run_plan(raw, base_url=None, allow_write=False, dry_run=False):
    """Return a v1 result bundle. Invalid plans raise PlanError before requests."""
    started = _now()
    plan = validate_plan(raw, base_url)
    secrets = set()
    missing = set()
    plan["headers"] = _expand(plan["headers"], missing, secrets)
    for case in plan["cases"]:
        for name in ("headers", "path", "body", "expect"):
            if name in case:
                case[name] = _expand(case[name], missing, secrets)
    # Environment values can introduce invalid URLs or header line breaks.
    plan = validate_plan(plan)
    if missing:
        reason = "Missing environment variables: " + ", ".join(sorted(missing))
        cases = [_case_result(case, "BLOCKED", "Plan blocked before requests", secrets, notes=[reason]) for case in plan["cases"]]
    elif not allow_write and any(case["method"] not in READ_METHODS for case in plan["cases"]):
        reason = "Plan includes write methods; rerun with --allow-write only within the user's authorized scope."
        cases = [_case_result(case, "BLOCKED", "Plan blocked before requests", secrets, notes=[reason]) for case in plan["cases"]]
    elif dry_run:
        cases = [_case_result(case, "NOT_RUN", "Dry run; no HTTP requests sent", secrets) for case in plan["cases"]]
    else:
        opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
        cases = [_execute(opener, plan, case, secrets) for case in plan["cases"]]
    return {
        "schema_version": 1, "run_id": "api-" + uuid.uuid4().hex[:12],
        "target": _redact(plan["base_url"], secrets),
        "started_at": started, "finished_at": _now(), "cases": cases,
        "notes": ["Sequential requests; no redirects followed or automatic retries performed.",
                  "Request/response bodies and headers are deliberately omitted from evidence."],
    }


def exit_code(report):
    states = {case["status"] for case in report["cases"]}
    if states & {"BLOCKED", "NOT_RUN", "SKIPPED"}:
        return 2
    return 1 if "FAIL" in states else 0


def _write_json(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as handle:
            temp_path = Path(handle.name)
            json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        # Publish a completed file without replacing any existing destination.
        # A hard link is atomic and exclusive on supported local filesystems;
        # if unavailable, fail rather than fall back to a destructive rename.
        os.link(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path, help="Path to a JSON API test plan")
    parser.add_argument("--output", required=True, type=Path, help="Path for the v1 JSON result bundle")
    parser.add_argument("--base-url", help="Override the plan's HTTP(S) base URL")
    parser.add_argument("--allow-write", action="store_true", help="Allow POST/PUT/PATCH/DELETE within already-authorized test scope")
    parser.add_argument("--dry-run", action="store_true", help="Validate and emit NOT_RUN cases without sending requests")
    args = parser.parse_args(argv)
    try:
        if args.plan.resolve() == args.output.resolve():
            raise PlanError("plan and output paths must differ")
        if args.output.exists() or args.output.is_symlink():
            raise PlanError("output already exists; choose a fresh run file")
        with args.plan.open(encoding="utf-8-sig") as handle:
            raw = json.load(handle)
        report = run_plan(raw, args.base_url, args.allow_write, args.dry_run)
        _write_json(args.output, report)
    except PlanError as error:
        print(f"Invalid plan: {error}", file=sys.stderr)
        return 2
    except (OSError, ValueError, RecursionError):
        print("Unable to read the plan or write results; check paths and JSON syntax.", file=sys.stderr)
        return 2
    counts = {status: sum(case["status"] == status for case in report["cases"])
              for status in ("PASS", "FAIL", "BLOCKED", "SKIPPED", "NOT_RUN")}
    print(" ".join(f"{status}={count}" for status, count in counts.items()))
    return exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
