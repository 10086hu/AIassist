"""Versioned, atomic server-local cache of extracted modules and source locations."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from dataclasses import asdict, replace
from pathlib import Path

from app.core.config import settings
from app.modules.duplicate.excel_parser import ParsedFunctionPoint
from app.modules.duplicate.execution import report_progress

VERSION = "compact-modules-v5"
logger = logging.getLogger(__name__)


def _bind_filename(point, filename):
    location = dict(point.source_location or {})
    if not location:
        return point
    location["file_name"] = filename
    if "source_spans" in location:
        location["source_spans"] = [{**span, "file_name": filename} for span in location["source_spans"]]
    return replace(point, source_location=location)


def cached_document_points(content: bytes, filename: str, context: str, use_llm: bool, loader):
    if not settings.duplicate_cache_enabled:
        return loader()
    # Extraction depends on the source text, not project labels or phase names.
    # Those belong to the comparison, and should not force re-reading history.
    identity = json.dumps([VERSION, use_llm, settings.deepseek_model,
                           settings.deepseek_api_url], ensure_ascii=False).encode("utf-8")
    key = hashlib.sha256(identity + b"\0" + content).hexdigest()
    directory = Path(settings.duplicate_cache_dir)
    path = directory / (key + ".json")
    try:
        if path.exists() and time.time() - path.stat().st_mtime < 30 * 86400:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data["version"] == VERSION:
                points = [_bind_filename(ParsedFunctionPoint(**item), filename) for item in data["points"]]
                report_progress(0.35, f"已复用文档摘要缓存：{len(points)} 个建设模块")
                return points
    except (OSError, ValueError, KeyError, TypeError):
        logger.warning("模块摘要缓存不可用，重新解析文档")
    points = [_bind_filename(point, filename) for point in loader()]
    temporary = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                         suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump({"version": VERSION, "points": [asdict(p) for p in points]}, handle, ensure_ascii=False)
        os.replace(temporary, path)
    except OSError:
        logger.warning("无法写入模块摘要缓存，本次检查正常继续")
    finally:
        if temporary is not None and temporary.exists():
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return points
