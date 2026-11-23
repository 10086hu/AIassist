"""Compare completed offline report verification outputs without rerunning checks.

The verification runner already normalizes generated UUIDs and timestamp fields.
Only the explicitly measured price-parser duration is removed here. All business
values, notices, evidence and errors remain part of the comparison.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


def normalized(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: normalized(item) for key, item in value.items() if key != "parse_elapsed_seconds"}
    if isinstance(value, list):
        return [normalized(item) for item in value]
    return value


def differences(before: Any, after: Any, path: str = "$") -> list[dict[str, Any]]:
    if type(before) is not type(after):
        return [{"path": path, "before": before, "after": after, "change": "type"}]
    if isinstance(before, dict):
        output = []
        for key in sorted(before.keys() | after.keys()):
            next_path = path + "." + key
            if key not in before:
                output.append({"path": next_path, "after": after[key], "change": "added"})
            elif key not in after:
                output.append({"path": next_path, "before": before[key], "change": "removed"})
            else:
                output.extend(differences(before[key], after[key], next_path))
        return output
    if isinstance(before, list):
        output = []
        for index in range(max(len(before), len(after))):
            next_path = f"{path}[{index}]"
            if index >= len(before):
                output.append({"path": next_path, "after": after[index], "change": "added"})
            elif index >= len(after):
                output.append({"path": next_path, "before": before[index], "change": "removed"})
            else:
                output.extend(differences(before[index], after[index], next_path))
        return output
    return [] if before == after else [{"path": path, "before": before, "after": after, "change": "value"}]


def result_errors(value: Any, path: str = "$") -> list[dict[str, Any]]:
    errors = []
    if isinstance(value, dict):
        for key, item in value.items():
            next_path = path + "." + key
            if key in {"verification_error", "error", "llm_error", "llm_error_type", "errors"} and item:
                errors.append({"path": next_path, "value": item})
            elif isinstance(item, (dict, list)):
                errors.extend(result_errors(item, next_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            errors.extend(result_errors(item, f"{path}[{index}]"))
    return errors


def result_signals(value: Any, path: str = "$") -> list[dict[str, Any]]:
    """Expose completion and error metadata without treating disabled AI as a rule failure."""
    signals = []
    if isinstance(value, dict):
        for key, item in value.items():
            next_path = path + "." + key
            name = key.lower()
            if (name in {"status", "state", "success", "completed", "complete", "partial"}
                    or (name.endswith("_status") and name != "match_status")
                    or name.endswith(("_error", "_errors", "_error_type", "_error_message", "_error_count"))
                    or name in {"error", "errors", "verification_error"}
                    or any(word in name for word in ("timeout", "timed_out", "interrupted", "incomplete", "failure"))):
                signals.append({"path": next_path, "value": item})
            if isinstance(item, (dict, list)):
                signals.extend(result_signals(item, next_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            signals.extend(result_signals(item, f"{path}[{index}]"))
    return signals


def business_failed_rules(value: Any, path: str = "$") -> list[dict[str, Any]]:
    """A validator's failed judgement reports a document issue, not an interrupted job."""
    result = []
    if isinstance(value, dict):
        if (value.get("status") == "failed" and value.get("passed") is False
                and ("rule_name" in value or "rule_id" in value) and isinstance(value.get("issues"), list)):
            result.append({"path": path + ".status", "rule_name": value.get("rule_name"),
                           "issue_count": len(value["issues"]), "summary": value.get("summary")})
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                result.extend(business_failed_rules(item, path + "." + key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            result.extend(business_failed_rules(item, f"{path}[{index}]"))
    return result


def interrupted_signals(signals: list[dict[str, Any]], business_rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    business_paths = {rule["path"] for rule in business_rules}
    bad_states = {"error", "failed", "failure", "timeout", "timed_out", "interrupted",
                  "incomplete", "partial", "aborted", "cancelled", "检查未完成", "检测未完成",
                  "未完成", "部分完成", "失败", "检查失败", "检测失败", "超时", "中断"}
    for signal in signals:
        if signal["path"] in business_paths:
            continue
        key = signal["path"].rsplit(".", 1)[-1].lower()
        value = signal["value"]
        if ((isinstance(value, str) and value.lower() in bad_states)
                or (isinstance(value, bool) and value and any(word in key for word in
                    ("timeout", "timed_out", "interrupted", "incomplete", "failure")))):
            result.append(signal)
    return result


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     default=str).encode("utf-8")).hexdigest()


def compare(before: dict, after: dict) -> dict:
    def indexed(items, keys):
        counts = Counter(tuple(item[key] for key in keys) for item in items)
        duplicate = [key for key, count in counts.items() if count != 1]
        if duplicate:
            raise ValueError(f"Duplicate input records: {duplicate}")
        return {tuple(item[key] for key in keys): item for item in items}

    source_before = indexed(before["sources"], ("label",))
    source_after = indexed(after["sources"], ("label",))
    sources = []
    for key in sorted(source_before.keys() | source_after.keys()):
        left, right = source_before.get(key), source_after.get(key)
        sources.append({"report": key[0], "same_sha256": bool(left and right and left["sha256"] == right["sha256"]),
                        "before": left, "after": right})
    rows_before = indexed(before["results"], ("report", "module"))
    rows_after = indexed(after["results"], ("report", "module"))
    rows = []
    for report, module in sorted(rows_before.keys() | rows_after.keys()):
        left, right = rows_before.get((report, module)), rows_after.get((report, module))
        result_before = normalized(left["result"]) if left else None
        result_after = normalized(right["result"]) if right else None
        delta = differences(result_before, result_after)
        after_summary = result_after.get("summary", {}) if isinstance(result_after, dict) else {}
        after_signals = result_signals(result_after)
        after_business_failed_rules = business_failed_rules(result_after)
        rows.append({"report": report, "module": module, "same": not delta,
                     "before_sha256": digest(result_before), "after_sha256": digest(result_after),
                     "before_errors": result_errors(result_before), "after_errors": result_errors(result_after),
                     "before_completion_signals": result_signals(result_before),
                     "after_completion_signals": after_signals,
                     "after_interruption_signals": interrupted_signals(after_signals, after_business_failed_rules),
                     "after_business_failed_rules": after_business_failed_rules,
                     "after_parse_warnings": after_summary.get("parse_warnings", []),
                     "after_notices": result_after.get("notices", []) if isinstance(result_after, dict) else [],
                     "after_price_library_status": after_summary.get("price_library_status"),
                     "after_llm_status": after_summary.get("llm_status"),
                     "before_status": result_before.get("status") if isinstance(result_before, dict) else None,
                     "after_status": result_after.get("status") if isinstance(result_after, dict) else None,
                     "difference_count": len(delta), "differences": delta})
    changed = [item for item in rows if not item["same"]]
    expected_fix = ({(row["report"], row["module"]) for row in changed}
                    == {("高效", "security"), ("国资", "security")}
                    and all(any(item["path"] == "$.verification_error" and item["value"] == "IndexError"
                                for item in row["before_errors"])
                            and not any(item["path"] == "$.verification_error" for item in row["after_errors"])
                            and row["after_status"] in {"通过", "发现问题"} for row in changed))
    verification_errors = [{"report": row["report"], "module": row["module"], "errors": row["after_errors"]}
                           for row in rows if any(error["path"] == "$.verification_error" for error in row["after_errors"])]
    interruptions = [{"report": row["report"], "module": row["module"], "signals": row["after_interruption_signals"]}
                     for row in rows if row["after_interruption_signals"]]
    limitations = [{"report": row["report"], "module": row["module"],
                    "parse_warnings": row["after_parse_warnings"], "notices": row["after_notices"],
                    "price_library_status": row["after_price_library_status"]}
                   for row in rows if row["after_parse_warnings"] or row["after_notices"]]
    return {"comparison_scope": "Four original reports, offline rule checks; no live model validation",
            "additional_ignored_fields": ["parse_elapsed_seconds"],
            "source_files_identical": bool(sources) and all(item["same_sha256"] for item in sources),
            "before_count": len(rows_before), "after_count": len(rows_after), "compared_count": len(rows),
            "identical_count": sum(item["same"] for item in rows), "changed_count": len(changed),
            "after_status_counts": dict(Counter(str(item["after_status"]) for item in rows)),
            "after_execution_summary": {"verification_error_count": len(verification_errors),
                    "verification_errors": verification_errors,
                    "interruption_or_timeout_count": len(interruptions), "interruptions_or_timeouts": interruptions,
                    "llm_intentionally_disabled_count": sum(row["after_llm_status"] == "disabled" for row in rows),
                    "business_manual_review_count": sum(row["after_status"] == "需人工复核" for row in rows),
                    "business_rule_failed_status_count": sum(len(row["after_business_failed_rules"]) for row in rows),
                    "business_rule_failed_status_note": "Validator status=failed with passed=false and document issues is a business judgement; it is not a runtime interruption.",
                    "limited_or_notice_count": len(limitations), "limitations_or_notices": limitations},
            "only_expected_security_exception_fix": expected_fix,
            "sources": sources, "results": rows,
            "limitations": {"before": before.get("limitations", []), "after": after.get("limitations", [])}}


def main():
    root = Path(__file__).resolve().parents[1]
    folder = root / ".codex-run/merge-20261113"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, default=folder / "real-reports-before.json")
    parser.add_argument("--after", type=Path, default=folder / "real-reports-after.json")
    parser.add_argument("--output", type=Path, default=folder / "real-report-comparison.json")
    args = parser.parse_args()
    before, after = (json.loads(path.read_text(encoding="utf-8")) for path in (args.before, args.after))
    output = compare(before, after)
    output["inputs"] = {"before": str(args.before.resolve()), "after": str(args.after.resolve())}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps({key: output[key] for key in ("source_files_identical", "before_count", "after_count",
                     "compared_count", "identical_count", "changed_count", "only_expected_security_exception_fix")}, ensure_ascii=False))
    print("OUTPUT", args.output)


if __name__ == "__main__":
    main()
