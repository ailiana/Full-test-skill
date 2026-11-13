#!/usr/bin/env python3
"""Render evidence-backed Full Test results using only the Python standard library."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import html
import json
import math
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
from urllib.parse import quote
import xml.etree.ElementTree as ET

STATUSES = ("PASS", "FAIL", "BLOCKED", "SKIPPED", "NOT_RUN")
TYPES = ("api", "ui", "manual")
PRIORITIES = ("P0", "P1", "P2", "P3")
EVIDENCE_KINDS = ("screenshot", "trace", "log", "file", "http")
SENSITIVE = r"(?:authorization|proxy-authorization|access[_-]?token|refresh[_-]?token|token|password|passwd|client[_-]?secret|secret|api[_-]?key|session[_-]?(?:id|token))"


def scrub(value: object) -> str:
    """Redact conventional credential forms; never read or print request secrets."""
    text = str(value)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]", "", text)
    # Cookie names are application-defined: scrub the complete header value rather
    # than trying to guess which cookies (sid, auth, etc.) carry a session.
    text = re.sub(r"(?im)(\b(?:set-cookie|cookie)[\"']?\s*[:=]\s*)[^\r\n]*", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(\b(?:proxy-)?authorization\s*[:=]\s*)(?:Bearer|Basic)\s+[^\s<>\"',;]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)\b(?:Bearer|Basic)\s+[A-Za-z0-9_+/=.:~-]+", "[REDACTED]", text)
    text = re.sub(r"(?is)([\"']?" + SENSITIVE + r"[\"']?\s*[:=]\s*)([\"'])(.*?)\2", lambda m: m[1] + m[2] + "[REDACTED]" + m[2], text)
    text = re.sub(r"(?i)(\b" + SENSITIVE + r"\s*[:=]\s*)[^\s&<>\"',;}]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)([?&]" + SENSITIVE + r"=)[^&#\s<>\"']*", r"\1[REDACTED]", text)
    return re.sub(r"(?i)(https?://)[^/\s@]+@", r"\1[REDACTED]@", text)


def clean(value: object) -> object:
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, list):
        return [clean(item) for item in value]
    if isinstance(value, dict):
        return {scrub(key): clean(item) for key, item in value.items()}
    return value


def read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError("non-finite JSON number")))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"Cannot read JSON input {path.name}: {type(exc).__name__}") from None


def string_field(data: dict, name: str, *, allow_empty: bool = False) -> str:
    value = data.get(name)
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f"{name} must be a {'possibly empty ' if allow_empty else 'nonempty '}string")
    return value


def validate_result(data: object) -> dict:
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ValueError("Results must be an object with schema_version 1")
    for field in ("run_id", "target", "started_at", "finished_at"):
        string_field(data, field, allow_empty=field == "target")
    times = []
    for field in ("started_at", "finished_at"):
        try:
            timestamp = datetime.fromisoformat(data[field].replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"{field} must be an ISO 8601 timestamp with timezone") from None
        if "T" not in data[field] or timestamp.tzinfo is None:
            raise ValueError(f"{field} must be an ISO 8601 timestamp with timezone")
        times.append(timestamp)
    if times[1] < times[0]:
        raise ValueError("finished_at must not precede started_at")
    if not isinstance(data.get("cases"), list):
        raise ValueError("cases must be a list")
    if not isinstance(data.get("notes", []), list) or any(not isinstance(x, str) for x in data.get("notes", [])):
        raise ValueError("notes must be a list of strings")
    ids = set()
    for case in data["cases"]:
        if not isinstance(case, dict):
            raise ValueError("Every case must be an object")
        case_id = string_field(case, "id")
        if case_id in ids:
            raise ValueError("Duplicate case id in one input")
        ids.add(case_id)
        for field in ("title", "expected", "actual"):
            string_field(case, field, allow_empty=field != "title")
        for field, allowed in (("status", STATUSES), ("type", TYPES), ("priority", PRIORITIES)):
            if not isinstance(case.get(field), str) or case[field] not in allowed:
                raise ValueError(f"Invalid {field} in case")
        duration = case.get("duration_ms")
        try:
            valid_duration = not isinstance(duration, bool) and isinstance(duration, (int, float)) and math.isfinite(duration) and duration >= 0
        except OverflowError:
            valid_duration = False
        if not valid_duration:
            raise ValueError("duration_ms must be a finite nonnegative number")
        for flag in ("flaky", "expected_failure"):
            if flag in case and not isinstance(case[flag], bool):
                raise ValueError(f"{flag} must be a boolean")
        if not isinstance(case.get("notes", []), list) or any(not isinstance(x, str) for x in case.get("notes", [])):
            raise ValueError("Case notes must be a list of strings")
        if not isinstance(case.get("evidence", []), list):
            raise ValueError("Case evidence must be a list")
        for evidence in case.get("evidence", []):
            if not isinstance(evidence, dict) or evidence.get("kind") not in EVIDENCE_KINDS:
                raise ValueError("Invalid evidence entry")
            string_field(evidence, "path", allow_empty=True)
            if "note" in evidence and not isinstance(evidence["note"], str):
                raise ValueError("Evidence note must be a string")
    return data


def safe_evidence(path: str, root: Path) -> Path | None:
    """Accept existing ordinary files contained by the result's own directory."""
    if not path or "\\" in path or ":" in path or "\x00" in path:
        return None
    relative = PurePosixPath(path)
    if relative.is_absolute() or any(part in ("..", ".") for part in relative.parts) or "?" in path or "#" in path:
        return None
    try:
        resolved = (root / path).resolve(strict=True)
        resolved.relative_to(root.resolve())
        if resolved.is_file() and not (root / path).is_symlink():
            return resolved
    except (OSError, ValueError, RuntimeError):
        pass
    return None


def merge_inputs(paths: list[Path]) -> tuple[list[dict], list[dict], list[str]]:
    cases, runs, notes = [], [], []
    seen = {}
    for path in paths:
        result = validate_result(read_json(path))
        runs.append({key: scrub(result[key]) for key in ("run_id", "target", "started_at", "finished_at")})
        notes.extend(scrub(note) for note in result.get("notes", []))
        for raw_case in result["cases"]:
            case_id = raw_case["id"]
            if case_id in seen:
                if seen[case_id] != raw_case:
                    raise ValueError("Conflicting case IDs across inputs; use unique IDs or import separate runs")
                continue
            seen[case_id] = raw_case
            case = clean(raw_case)
            case["_evidence_files"] = []
            case["evidence"] = []
            for entry in raw_case.get("evidence", []):
                file_path = safe_evidence(entry["path"], path.parent)
                if file_path:
                    case["_evidence_files"].append((file_path, clean(entry)))
                else:
                    case.setdefault("notes", []).append("An unavailable or unsafe evidence link was omitted.")
            cases.append(case)
    # IDs remain unique after credential redaction as well.
    if len({case["id"] for case in cases}) != len(cases):
        raise ValueError("Case IDs collide after redaction")
    return cases, runs, notes


def summarize(cases: list[dict], runs: list[dict], notes: list[str]) -> dict:
    counts = {status: 0 for status in STATUSES}
    counts.update(Counter(case["status"] for case in cases))
    executed = counts["PASS"] + counts["FAIL"]
    flaky = sum(bool(case.get("flaky")) for case in cases)
    expected_failures = sum(bool(case.get("expected_failure")) for case in cases)
    overall = "FAIL" if counts["FAIL"] else "INCOMPLETE" if not cases or flaky or expected_failures or executed != len(cases) else "PASS"
    blockers = [case["id"] for case in cases if case["status"] == "FAIL" and case["priority"] in ("P0", "P1")]
    return {
        "schema_version": 1, "overall": overall, "release_gate": "BLOCKED" if blockers else "READY" if overall == "PASS" else "REVIEW_REQUIRED",
        "total": len(cases), "executed": executed, "counts": counts, "flaky": flaky, "expected_failures": expected_failures,
        "pass_rate_percent": round(counts["PASS"] * 100 / executed, 2) if executed else None,
        "pass_rate_denominator": "PASS + FAIL", "release_blocking_cases": blockers,
        "runs": runs, "notes": notes,
    }


def attach_evidence(cases: list[dict], out_dir: Path) -> None:
    """Copy only safe image/trace attachments, never arbitrary logs with credentials."""
    for index, case in enumerate(cases, 1):
        for number, (source, entry) in enumerate(case.pop("_evidence_files"), 1):
            suffix = source.suffix.lower()
            # Text files are sanitized; screenshots and trace archives remain original evidence.
            if suffix not in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".zip", ".txt", ".log", ".json") or source.stat().st_size > 20 * 1024 * 1024:
                case.setdefault("notes", []).append("An unsupported or oversized evidence attachment was omitted.")
                continue
            target = out_dir / "evidence" / f"case-{index:04d}-{number:02d}{suffix}"
            target.parent.mkdir(exist_ok=True)
            if suffix in (".txt", ".log", ".json"):
                try:
                    target.write_text(scrub(source.read_text(encoding="utf-8-sig")), encoding="utf-8")
                except (OSError, UnicodeError):
                    case.setdefault("notes", []).append("An unreadable text evidence attachment was omitted.")
                    continue
            else:
                shutil.copyfile(source, target)
            entry["path"] = target.relative_to(out_dir).as_posix()
            case["evidence"].append(entry)


def markdown_text(value: object) -> str:
    text = html.escape(scrub(value), quote=False)
    text = re.sub(r"([\\`*_{}\[\]()#+.!|])", r"\\\1", text)
    return text.replace("\n", "<br>").replace("\r", "")


def render_markdown(cases: list[dict], summary: dict) -> str:
    rate = "N/A" if summary["pass_rate_percent"] is None else f"{summary['pass_rate_percent']}%"
    lines = ["# Full Test Report", "", f"**Overall: {summary['overall']}** · Release gate: **{summary['release_gate']}**", "",
             f"Total: {summary['total']} · Executed: {summary['executed']} · Flaky: {summary['flaky']} · Expected failures: {summary['expected_failures']}",
             f"Pass rate: {rate} (PASS / (PASS + FAIL); not-executed cases excluded)", "",
             " | ".join(f"{key}: {value}" for key, value in summary["counts"].items()), "",
             "| ID | Type | Priority | Status | Title | Duration ms |", "|---|---|---|---|---|---:|"]
    for case in cases:
        lines.append("| " + " | ".join(markdown_text(case[key]) for key in ("id", "type", "priority", "status", "title", "duration_ms")) + " |")
    for case in cases:
        lines.extend(["", f"## {markdown_text(case['id'])}: {markdown_text(case['title'])}", "",
                      f"Expected: {markdown_text(case['expected'])}", "", f"Actual: {markdown_text(case['actual'])}"])
        for note in case.get("notes", []):
            lines.extend(["", f"- {markdown_text(note)}"])
        for evidence in case["evidence"]:
            lines.extend(["", f"- [{markdown_text(evidence['kind'])}]({quote(evidence['path'], safe='/')}) {markdown_text(evidence.get('note', ''))}"])
    lines.extend(["", "## Run context", ""])
    for run in summary["runs"]:
        lines.append(f"- {markdown_text(run['run_id'])}: {markdown_text(run['target'])}; {markdown_text(run['started_at'])} → {markdown_text(run['finished_at'])}")
    lines.extend(f"- {markdown_text(note)}" for note in summary["notes"])
    return "\n".join(lines) + "\n"


def render_html(cases: list[dict], summary: dict) -> str:
    e = lambda value: html.escape(scrub(value), quote=True)
    rows = []
    details = []
    for case in cases:
        rows.append("<tr>" + "".join(f"<td>{e(case[key])}</td>" for key in ("id", "type", "priority", "status", "title", "duration_ms")) + "</tr>")
        notes = "".join(f"<li>{e(note)}</li>" for note in case.get("notes", []))
        links = "".join(f'<li><a href="{quote(item["path"], safe="/")}">{e(item["kind"])}</a> {e(item.get("note", ""))}</li>' for item in case["evidence"])
        details.append(f'<section><h2>{e(case["id"])}: {e(case["title"])}</h2><p><strong>Expected:</strong> {e(case["expected"])}</p><p><strong>Actual:</strong> {e(case["actual"])}</p><ul>{notes}{links}</ul></section>')
    run_html = "".join(f'<li>{e(run["run_id"])} · {e(run["target"])}<br>{e(run["started_at"])} → {e(run["finished_at"])}</li>' for run in summary["runs"])
    rate = "N/A" if summary["pass_rate_percent"] is None else f'{summary["pass_rate_percent"]}%'
    counts = " · ".join(f"{key}: {value}" for key, value in summary["counts"].items())
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; base-uri 'none'; form-action 'none'">
<title>Full Test Report — {summary['overall']}</title><style>
:root{{color-scheme:light dark;font:16px/1.55 system-ui,sans-serif}}body{{max-width:1160px;margin:2rem auto;padding:0 1rem}}h1{{margin-bottom:.3rem}}.summary,section{{padding:1rem;border:1px solid #8886;border-radius:.6rem;margin:1rem 0}}.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%}}th,td{{text-align:left;padding:.6rem;border-bottom:1px solid #8885;vertical-align:top}}td,li,p{{overflow-wrap:anywhere}}a{{color:#467dd7}}.muted{{opacity:.78}}section p{{white-space:pre-wrap}}
</style></head><body><h1>Full Test Report</h1><div class="summary"><strong>{summary['overall']}</strong> · Release gate: <strong>{summary['release_gate']}</strong>
<p>Total: {summary['total']} · Executed: {summary['executed']} · Flaky: {summary['flaky']} · Expected failures: {summary['expected_failures']}</p><p>{counts}</p>
<p>Pass rate: {rate} <span class="muted">PASS / (PASS + FAIL); not-executed cases excluded</span></p></div>
<div class="scroll"><table><thead><tr><th>ID</th><th>Type</th><th>Priority</th><th>Status</th><th>Title</th><th>Duration ms</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
{''.join(details)}<section><h2>Run context</h2><ul>{run_html}{''.join(f'<li>{e(note)}</li>' for note in summary['notes'])}</ul>
<p class="muted">Text credentials are redacted heuristically. Review screenshots and traces before sharing: binary evidence may contain session data.</p></section></body></html>'''


def render_junit(cases: list[dict]) -> bytes:
    suite = ET.Element("testsuite", name="Full Test", tests=str(len(cases)), failures=str(sum(case["status"] == "FAIL" for case in cases)),
                       errors="0", skipped=str(sum(case["status"] in ("BLOCKED", "SKIPPED", "NOT_RUN") for case in cases)),
                       time=f"{sum(case['duration_ms'] for case in cases) / 1000:.6f}")
    for case in cases:
        element = ET.SubElement(suite, "testcase", name=f"{case['id']}: {case['title']}", classname=case["type"], time=f"{case['duration_ms'] / 1000:.6f}")
        if case["status"] == "FAIL":
            ET.SubElement(element, "failure", message=case["actual"], type="AssertionFailure").text = f"Expected: {case['expected']}\nActual: {case['actual']}"
        elif case["status"] != "PASS":
            ET.SubElement(element, "skipped", message=case["actual"] or case["status"], type=case["status"])
        if case.get("flaky"):
            ET.SubElement(element, "system-out").text = "FLAKY: " + "\n".join(case.get("notes", []))
    return ET.tostring(suite, encoding="utf-8", xml_declaration=True)


def generate(inputs: list[Path], out_dir: Path) -> dict:
    cases, runs, notes = merge_inputs(inputs)
    # Validate before creating or modifying any outputs.
    if out_dir.is_symlink() or out_dir.exists() and (not out_dir.is_dir() or any(out_dir.iterdir())):
        raise ValueError("Output directory must be absent or empty; choose a fresh run directory")
    out_dir.mkdir(parents=True, exist_ok=True)
    attach_evidence(cases, out_dir)
    summary = summarize(cases, runs, notes)
    (out_dir / "report.html").write_text(render_html(cases, summary), encoding="utf-8")
    (out_dir / "report.md").write_text(render_markdown(cases, summary), encoding="utf-8")
    (out_dir / "junit.xml").write_bytes(render_junit(cases))
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, action="append", required=True, help="Full Test results JSON; repeat to merge UI/API inputs")
    parser.add_argument("--out-dir", type=Path, required=True, help="Fresh directory for HTML, Markdown, JUnit and summary outputs")
    args = parser.parse_args(argv)
    try:
        summary = generate(args.input, args.out_dir)
    except (ValueError, OSError, OverflowError) as exc:
        print("Report error: " + scrub(exc), file=sys.stderr)
        return 2
    print(json.dumps({key: summary[key] for key in ("overall", "total", "executed", "counts", "flaky")}, ensure_ascii=False))
    return 1 if summary["overall"] == "FAIL" else 2 if summary["overall"] == "INCOMPLETE" else 0


if __name__ == "__main__":
    raise SystemExit(main())
