"""Precompute lexical vectors once and retrieve a small candidate neighborhood."""
from __future__ import annotations

from collections import defaultdict
import re

from app.modules.duplicate.embeddings import SYNONYM_GROUPS, embed_text, normalize_text, tokenize


def _features(text):
    tokens = set(tokenize(text)) - {"核心", "功能", "范围", "子功", "：", "；"}
    norm = normalize_text(text)
    synonyms = {i for i, group in enumerate(SYNONYM_GROUPS) if any(normalize_text(t) in norm for t in group)}
    return tokens, {i: value for i, value in enumerate(embed_text(text)) if value}, synonyms


def _capabilities(point):
    location = getattr(point, "source_location", None) or {}
    return {re.sub(r"\W", "", value) for value in location.get("core_functions") or []
            if 4 <= len(value) <= 40}


class CandidateIndex:
    def __init__(self, points):
        self.points = points
        self.features = [_features(f"{p.name} {p.description}") for p in points]
        self.capabilities = [_capabilities(p) for p in points]
        self.postings = defaultdict(set)
        self.names = defaultdict(set)
        for index, (point, (tokens, _, synonyms)) in enumerate(zip(points, self.features)):
            for term in tokens | {f"synonym:{i}" for i in synonyms}:
                self.postings[term].add(index)
            for capability in self.capabilities[index]:
                self.postings[f"capability:{capability}"].add(index)
            self.names[normalize_text(point.name)].add(index)

    def search(self, point, top_k, threshold, *, query_index=None):
        tokens, vector, synonyms = self.features[query_index] if query_index is not None else _features(f"{point.name} {point.description}")
        capabilities = self.capabilities[query_index] if query_index is not None else _capabilities(point)
        pool = set(self.names.get(normalize_text(point.name), ()))
        for term in tokens | {f"synonym:{i}" for i in synonyms} | {f"capability:{c}" for c in capabilities}:
            pool.update(self.postings.get(term, ()))
        scored = []
        for index in pool:
            if index == query_index:
                continue
            if query_index is not None:
                left_location = getattr(point, "source_location", None) or {}
                right_location = getattr(self.points[index], "source_location", None) or {}
                if left_location.get("file_name") == right_location.get("file_name"):
                    if any(left_location.get(field) and left_location.get(field) == right_location.get(field)
                           for field in ("paragraph", "row_index")):
                        continue
            other_tokens, other_vector, other_synonyms = self.features[index]
            overlap = len(tokens & other_tokens) / max(1, len(tokens | other_tokens))
            cosine = sum(value * other_vector.get(bucket, 0) for bucket, value in vector.items())
            score = min(1.0, 0.55 * max(0, cosine) + 0.3 * min(overlap * 3, 1) + 0.15 * bool(synonyms & other_synonyms))
            shared_capabilities = capabilities & self.capabilities[index]
            if shared_capabilities:
                # A small duplicated capability inside two otherwise different
                # large modules must not disappear under whole-module averaging.
                score = max(score, 0.55 + 0.05 * min(2, len(shared_capabilities)))
            exact_name = normalize_text(point.name) == normalize_text(self.points[index].name)
            if score >= threshold or exact_name:
                scored.append((index, score, exact_name))
        scored.sort(key=lambda item: (-item[1], item[0]))
        # Top-k is a workload control, not a global truncation of the report.
        # Keep all exact names / strong matches even when a module has >k hits.
        return [(i, score) for rank, (i, score, exact) in enumerate(scored)
                if rank < top_k or exact or score >= 0.85]
