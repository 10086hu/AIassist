from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import List, Optional

from app.core.config import settings


TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff]{2}|[a-zA-Z0-9_]+")

SYNONYM_GROUPS = (
    {"统一身份认证", "身份认证", "单点登录", "登录入口", "权限校验", "会话管理"},
    {"数据共享交换", "数据交换", "接口发布", "数据订阅", "交换监控", "接口管理"},
    {"可视化驾驶舱", "综合态势", "图表展示", "趋势分析", "运行指标"},
    {"日志审计", "审计查询", "操作日志", "访问日志", "配置变更"},
)


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def text_hash(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def tokenize(text: str) -> List[str]:
    compact = normalize_text(text)
    tokens = TOKEN_PATTERN.findall(compact)
    bigrams = [compact[index : index + 2] for index in range(max(len(compact) - 1, 0))]
    return tokens + bigrams


def embed_text(text: str, dim: Optional[int] = None) -> List[float]:
    size = dim or settings.embedding_dim
    vector = [0.0] * size
    counts = Counter(tokenize(text))
    if not counts:
        return vector

    for token, count in counts.items():
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "big") % size
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[bucket] += sign * (1.0 + math.log(count))
    return _l2_normalize(vector)


def cosine_similarity(left: List[float], right: List[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def lexical_overlap(left: str, right: str) -> float:
    left_tokens = set(tokenize(left))
    right_tokens = set(tokenize(right))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def duplicate_similarity_score(left: str, right: str) -> float:
    vector_score = cosine_similarity(embed_text(left), embed_text(right))
    overlap_score = lexical_overlap(left, right)
    synonym_score = _synonym_overlap(left, right)
    score = (0.55 * max(vector_score, 0.0)) + (0.3 * min(overlap_score * 3.0, 1.0)) + (0.15 * synonym_score)
    return min(score, 1.0)


def _l2_normalize(vector: List[float]) -> List[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def _synonym_overlap(left: str, right: str) -> float:
    left_norm = normalize_text(left)
    right_norm = normalize_text(right)
    matches = 0
    for group in SYNONYM_GROUPS:
        left_hit = any(normalize_text(term) in left_norm for term in group)
        right_hit = any(normalize_text(term) in right_norm for term in group)
        if left_hit and right_hit:
            matches += 1
    return 1.0 if matches else 0.0
