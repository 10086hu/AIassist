"""Measure local parsing/retrieval and optional live-model review of a long DOCX.

Usage: backend/.venv/Scripts/python scripts/benchmark_duplicate_long_report.py FILE --with-model
Uses an isolated in-memory database and reads the existing user model configuration.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument("--with-model", action="store_true")
    parser.add_argument("--history", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if os.name == "nt":
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            for name in ("DEEPSEEK_API_KEY", "DEEPSEEK_API_URL", "DEEPSEEK_MODEL"):
                if not os.getenv(name):
                    try:
                        os.environ[name] = str(winreg.QueryValueEx(key, name)[0])
                    except FileNotFoundError:
                        pass
    os.environ.setdefault("DEEPSEEK_API_URL", "https://llmapi.tongji.edu.cn/v1")
    os.environ["DUPLICATE_CACHE_DIR"] = str(ROOT / ".cache" / "duplicate_benchmark")

    from app.modules.duplicate import llm_judge, service
    from app.modules.duplicate.document_parser import parse_document
    from app.modules.duplicate.compact_extractor import extract_compact_modules
    from app.modules.duplicate.execution import duplicate_execution
    from app.core.config import settings
    from app.db.models import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    content = args.file.read_bytes()
    started = time.perf_counter()
    document = parse_document(content, args.file.name)
    parsed = time.perf_counter()
    points = extract_compact_modules(document)
    extracted = time.perf_counter()
    refs = service._report_points_from_parsed(service.ParsedReport(args.file.name, "本期", "current", points))
    candidates = service._find_report_candidate_pairs(refs)
    retrieved = time.perf_counter()
    summary = {
        "file_name": args.file.name, "characters": len(document.raw_text), "modules": len(points),
        "internal_candidates": len(candidates), "parse_seconds": round(parsed - started, 3),
        "extract_seconds": round(extracted - parsed, 3), "retrieve_seconds": round(retrieved - extracted, 3),
        "model_review_completed": False,
    }
    if args.history:
        history_refs = []
        history_started = time.perf_counter()
        for history in args.history:
            historical = parse_document(history.read_bytes(), history.name)
            modules = extract_compact_modules(historical)
            history_refs.extend(service._report_points_from_parsed(
                service.ParsedReport(history.name, "往期", "history", modules)))
        summary["history_modules"] = len(history_refs)
        summary["cross_report_candidates"] = len(service._find_cross_report_candidate_pairs(refs, history_refs))
        summary["history_prepare_seconds"] = round(time.perf_counter() - history_started, 3)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    if args.with_model:
        if not settings.deepseek_api_key:
            raise ValueError("未配置模型密钥，已完成本地解析测速")
        calls = []
        lock = threading.Lock()
        original = llm_judge.post_direct

        def post(*args, **kwargs):
            before = time.perf_counter()
            response = original(*args, **kwargs)
            with lock:
                calls.append({"seconds": round(time.perf_counter() - before, 3), "status": response.status_code})
            return response

        llm_judge.post_direct = post
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        model_start = time.perf_counter()
        with Session(engine) as db, duplicate_execution(lambda f, message: print(message, flush=True)):
            if args.history:
                result = service.run_duplicate_compare_check(
                    db, content, args.file.name, [(p.read_bytes(), p.name) for p in args.history],
                    None, "长报告测速", None, use_llm=True,
                )
            else:
                result = service.run_duplicate_check_from_document(db, content, args.file.name, None,
                                                                   "长报告测速", None, use_llm=True)
        summary.update(model_review_completed=True, full_review_seconds=round(time.perf_counter() - model_start, 3),
                       model_requests=len(calls), model_request_times=calls,
                       findings=[{"left": p.item_name, "right": p.related_item_name,
                                  "label": p.result_label, "type": p.comparison_type,
                                  "reason": p.reason} for p in result.pairs])
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
