#!/usr/bin/env python3
"""Convert Playwright's JSON reporter output into Full Test result schema v1."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys

# Sibling import works from any current working directory after skill installation.
from report import clean, read_json, scrub, validate_result

RESULT_STATUSES = {"passed", "failed", "timedOut", "skipped", "interrupted"}
TEST_STATUSES = {"expected", "unexpected", "flaky", "skipped"}
MAX_DURATION_MS = 31 * 24 * 60 * 60 * 1000


def iso(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except ValueError:
        return None


def milliseconds(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 or value > MAX_DURATION_MS or not math.isfinite(value):
        raise ValueError("Playwright duration must be finite, nonnegative and at most 31 days")
    return value


def add_duration(start: datetime, elapsed: float) -> datetime:
    try:
        return start + timedelta(milliseconds=milliseconds(elapsed))
    except (OverflowError, ValueError):
        raise ValueError("Playwright execution timestamp or duration is out of range") from None


def priority_for(tags: list[str], annotations: list[dict]) -> str:
    priorities = []
    values = tags + [str(item.get("description", "")) for item in annotations if isinstance(item, dict) and item.get("type") in ("tag", "priority")]
    for value in values:
        if value.lower() == "@smoke":
            priorities.append("P0")
        else:
            match = re.fullmatch(r"@?(P[0-3])", value, flags=re.IGNORECASE)
            if match:
                priorities.append(match[1].upper())
    return min(priorities) if priorities else "P1"


def evidence_entries(attachments: object, source_root: Path, output_root: Path) -> tuple[list[dict], int]:
    """Keep contained paths; unsupported/inline or out-of-root attachments are omitted."""
    if not isinstance(attachments, list):
        raise ValueError("Playwright attachments must be a list")
    entries, omitted = [], 0
    for attachment in attachments:
        if not isinstance(attachment, dict):
            raise ValueError("Malformed Playwright attachment")
        raw = attachment.get("path")
        if not isinstance(raw, str) or not raw:
            omitted += 1
            continue
        if "\x00" in raw or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw):
            omitted += 1
            continue
        # Absolute runner paths may be accepted only when still under output_root.
        path = Path(raw)
        if not path.is_absolute():
            if any(part == ".." for part in path.parts):
                omitted += 1
                continue
            path = source_root / path
        try:
            path = path.resolve()
            relative = path.relative_to(output_root.resolve())
        except (ValueError, OSError, RuntimeError):
            omitted += 1
            continue
        if not relative.parts or ":" in relative.as_posix():
            omitted += 1
            continue
        content_type = str(attachment.get("contentType", ""))
        name = str(attachment.get("name", ""))
        kind = "screenshot" if content_type.startswith("image/") else "trace" if name == "trace" or path.suffix == ".zip" else "log" if content_type.startswith("text/") else "file"
        entries.append({"kind": kind, "path": relative.as_posix(), "note": scrub(name)})
    return entries, omitted


def import_report(data: object, source: Path, output: Path, target: str = "") -> dict:
    if not isinstance(data, dict) or not isinstance(data.get("suites"), list):
        raise ValueError("Input must be a Playwright JSON reporter object with suites")
    cases, ids, all_times = [], set(), []
    errors = data.get("errors", [])
    if not isinstance(errors, list):
        raise ValueError("Playwright root errors must be a list")
    notes = []

    def visit(suites: list, parents: list[str]) -> None:
        for suite in suites:
            if not isinstance(suite, dict):
                raise ValueError("Malformed Playwright suite")
            suite_title = suite.get("title", "")
            if not isinstance(suite_title, str):
                raise ValueError("Playwright suite title must be a string")
            titles = parents + ([suite_title] if suite_title else [])
            specs = suite.get("specs", [])
            nested = suite.get("suites", [])
            if not isinstance(specs, list) or not isinstance(nested, list):
                raise ValueError("Playwright suites/specs must be lists")
            for spec in specs:
                if not isinstance(spec, dict) or not isinstance(spec.get("title"), str) or not isinstance(spec.get("tests"), list):
                    raise ValueError("Malformed Playwright spec")
                for test in spec["tests"]:
                    if not isinstance(test, dict) or not isinstance(test.get("results", []), list):
                        raise ValueError("Malformed Playwright test")
                    project = test.get("projectName", "default")
                    if not isinstance(project, str):
                        raise ValueError("Playwright projectName must be a string")
                    identity = json.dumps([spec.get("id", ""), spec.get("file", suite.get("file", "")), spec.get("line", 0), titles, spec["title"], project, test.get("repeatEachIndex", 0)], ensure_ascii=True, sort_keys=True)
                    case_id = "PW-" + hashlib.sha256(identity.encode()).hexdigest()[:16]
                    if case_id in ids:
                        raise ValueError("Duplicate Playwright test identity")
                    ids.add(case_id)
                    expected = test.get("expectedStatus", "passed")
                    if expected not in ("passed", "failed", "skipped"):
                        raise ValueError("Unknown Playwright expectedStatus")
                    aggregate = test.get("status")
                    if aggregate is not None and (not isinstance(aggregate, str) or aggregate not in TEST_STATUSES):
                        raise ValueError("Unknown Playwright aggregate status")
                    annotations = test.get("annotations", [])
                    tags = spec.get("tags", [])
                    if not isinstance(annotations, list) or not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
                        raise ValueError("Playwright annotations/tags must be lists")
                    results = test.get("results", [])
                    observations, evidence, case_notes, attempt_records, duration = [], [], [], [], 0.0
                    for index, result in enumerate(results):
                        if not isinstance(result, dict) or not isinstance(result.get("status"), str) or result["status"] not in RESULT_STATUSES:
                            raise ValueError("Unknown Playwright result status")
                        elapsed = milliseconds(result.get("duration", 0))
                        duration = milliseconds(duration + elapsed)
                        status = result["status"]
                        retry = result.get("retry", index)
                        if type(retry) is not int or retry < 0:
                            raise ValueError("Playwright retry must be a nonnegative integer")
                        attempt_record = {"retry": retry, "status": status, "duration_ms": elapsed}
                        observations.append(status)
                        case_notes.append(f"Attempt {index + 1}: {status} ({elapsed:g} ms)")
                        start = iso(result.get("startTime"))
                        if start:
                            attempt_record["started_at"] = start.isoformat()
                            all_times.extend([start, add_duration(start, elapsed)])
                        attempt_records.append(attempt_record)
                        entries, omitted = evidence_entries(result.get("attachments", []), source.parent, output.parent)
                        evidence.extend(entries)
                        if omitted:
                            case_notes.append(f"{omitted} inline, external or out-of-run attachment(s) omitted.")
                        result_errors = result.get("errors", [])
                        if not isinstance(result_errors, list):
                            raise ValueError("Playwright errors must be a list")
                        if result.get("error"):
                            result_errors = result_errors + [result["error"]]
                        for error in result_errors:
                            message = error.get("message", "") if isinstance(error, dict) else str(error)
                            if message:
                                case_notes.append(scrub(message)[:4000])
                    flaky = aggregate == "flaky" or len(observations) > 1 and any(value != expected for value in observations[:-1]) and observations[-1] == expected
                    expected_failure = False
                    if not observations:
                        status = "SKIPPED" if aggregate == "skipped" else "NOT_RUN"
                        actual = "No execution results were recorded."
                    elif observations[-1] == "skipped":
                        status = "SKIPPED"
                        actual = "Playwright skipped this test."
                    elif observations[-1] == "interrupted":
                        status = "BLOCKED"
                        actual = "Playwright run was interrupted before a conclusion."
                    elif aggregate == "unexpected" or observations[-1] != expected:
                        status = "FAIL"
                        actual = f"Final result {observations[-1]}; expected {expected}."
                    elif expected == "failed":
                        status = "SKIPPED"
                        expected_failure = True
                        actual = "Observed Playwright expected failure; this does not establish functional correctness."
                        case_notes.append("EXPECTED FAILURE: excluded from functional passes and requires review before release.")
                    else:
                        status = "PASS"
                        actual = f"Observed expected {expected} outcome."
                    if flaky:
                        case_notes.append("FLAKY: an earlier attempt did not meet the expected outcome; final success is not a stable regression signal.")
                    priority = priority_for(tags, annotations)
                    cases.append({"id": case_id, "title": scrub(" › ".join(titles + [spec["title"]]) + f" [{project}]"), "type": "ui", "priority": priority,
                                  "status": status, "duration_ms": duration, "expected": f"Playwright expectedStatus={expected}", "actual": actual,
                                  "evidence": evidence, "notes": case_notes, "flaky": flaky, "expected_failure": expected_failure, "attempts": attempt_records})
            visit(nested, titles)

    visit(data["suites"], [])
    # Global setup/worker/reporter errors may exist with zero failed test records.
    for index, error in enumerate(errors, 1):
        message = error.get("message", "Playwright global error") if isinstance(error, dict) else str(error)
        cases.append({"id": f"PW-GLOBAL-{index:03d}", "title": "Playwright global execution error", "type": "ui", "priority": "P0", "status": "BLOCKED",
                      "duration_ms": 0, "expected": "Global setup and workers complete without errors", "actual": scrub(message)[:4000], "evidence": [], "notes": []})
    stats = data.get("stats", {})
    now = datetime.now(timezone.utc)
    stats_start = iso(stats.get("startTime")) if isinstance(stats, dict) else None
    if all_times:
        started, finished = min(all_times), max(all_times)
    elif stats_start:
        started = stats_start
        finished = add_duration(stats_start, stats.get("duration", 0))
    else:
        started = finished = now
        notes.append("Source report omitted execution timestamps; timestamps here indicate import time.")
    if not cases:
        notes.append("No Playwright tests were recorded; this run cannot establish a passing result.")
    fingerprint = hashlib.sha256(json.dumps(data, ensure_ascii=True, sort_keys=True).encode()).hexdigest()[:12]
    result = clean({"schema_version": 1, "run_id": "playwright-" + fingerprint, "target": target,
                    "started_at": started.isoformat(), "finished_at": finished.isoformat(), "cases": cases, "notes": notes})
    return validate_result(result)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="JSON emitted by Playwright's JSON reporter")
    parser.add_argument("--output", type=Path, required=True, help="New Full Test results JSON file; put it in the artifact run directory")
    parser.add_argument("--target", default="", help="Optional tested base URL, shown as text and redacted in reports")
    args = parser.parse_args(argv)
    try:
        if args.output.exists() or args.output.is_symlink():
            raise ValueError("Output already exists; choose a new run file")
        result = import_report(read_json(args.input), args.input, args.output, args.target)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
    except (ValueError, OSError, OverflowError) as exc:
        print("Import error: " + scrub(exc), file=sys.stderr)
        return 2
    counts = {status: sum(case["status"] == status for case in result["cases"]) for status in ("PASS", "FAIL", "BLOCKED", "SKIPPED", "NOT_RUN")}
    print(json.dumps({"imported": len(result["cases"]), "counts": counts}))
    return 1 if counts["FAIL"] else 2 if counts["BLOCKED"] or counts["NOT_RUN"] or counts["SKIPPED"] or not result["cases"] or any(case.get("flaky") for case in result["cases"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
