"""Bounded model work and progress reporting for one duplicate review."""
from __future__ import annotations

import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from typing import Callable

from app.core.config import settings

_reporter = ContextVar("duplicate_reporter", default=None)
_deadline = ContextVar("duplicate_deadline", default=None)


@contextmanager
def duplicate_execution(reporter: Callable[[float, str], None] | None = None):
    reporter_token = _reporter.set(reporter)
    deadline_token = _deadline.set(time.monotonic() + settings.duplicate_task_timeout_seconds)
    try:
        yield
    finally:
        _reporter.reset(reporter_token)
        _deadline.reset(deadline_token)


def report_progress(fraction: float, message: str) -> None:
    callback = _reporter.get()
    if callback:
        callback(fraction, message)


def remaining_timeout(default: float) -> float:
    deadline = _deadline.get()
    if deadline is None:
        return default
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError("重复建设检查超过总时限，请检查模型服务后重新执行；本次未生成完整审查结论")
    return min(default, remaining)


def parallel_model_work(items, worker, *, message: str, start: float, end: float):
    """Workers only call the model; database writes stay on the caller thread."""
    items = list(items)
    total = len(items)
    if not total:
        report_progress(end, f"{message}：0/0")
        return []
    results = [None] * total
    concurrency = max(1, min(8, settings.duplicate_llm_concurrency))
    pool = ThreadPoolExecutor(max_workers=concurrency)
    pending = {}
    next_index = 0
    completed = 0
    started = time.monotonic()
    try:
        while completed < total:
            remaining_timeout(1)
            while next_index < total and len(pending) < concurrency:
                context = copy_context()
                future = pool.submit(context.run, worker, items[next_index])
                pending[future] = next_index
                next_index += 1
            report_progress(
                start + (end - start) * completed / total,
                f"{message}：已完成 {completed}/{total}，已用时 {int(time.monotonic() - started)} 秒",
            )
            done, _ = wait(pending, timeout=min(2, remaining_timeout(2)), return_when=FIRST_COMPLETED)
            for future in done:
                results[pending.pop(future)] = future.result()
                completed += 1
        report_progress(end, f"{message}：已完成 {total}/{total}")
        return results
    finally:
        # Outstanding HTTP calls own no database objects and cannot save results.
        pool.shutdown(wait=False, cancel_futures=True)
