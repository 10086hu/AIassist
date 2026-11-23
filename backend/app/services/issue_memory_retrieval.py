"""Bounded lexical issue matching. No embeddings, model calls or learned aliases."""
from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from typing import Any

MAX_ALIASES = 100
MAX_VARIANTS = 20
MAX_CANDIDATES = 48
MAX_POSTING = 256


def compact(value: Any) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", unicodedata.normalize("NFKC", str(value or "")).lower())


def critical_signature(value: str) -> tuple:
    # Heading relocation is allowed; quantities, phase names and negations are not.
    text = unicodedata.normalize("NFKC", str(value)).lower()
    heading = r"(?:第[一二三四五六七八九十百\d]+[章节]|\d+(?:\.\d+){1,4})[\s、.:：]+"
    text = re.sub(r'(?m)(^|[\n“"])(\s*)' + heading +
                  r"(?!\s*(?:万元|亿元|元|台|套|个|项|人|核|年|月|天|%|gb|tb|mb))", r"\1", text)
    quantities = r"(?:[-+]?\d+(?:\.\d+)?|[一二三四五六七八九十百千万两]+(?=\s*(?:台|套|个|万元|亿元|元|项|人|核|年|月|天)))"
    units = r"(?:万元|亿元|人月|平方米|gb|tb|mb|元|台|套|个|项|人|核|年|月|天|%|倍|路|点|条|件)?"
    numbers = tuple(re.sub(r"\s+", "", match.group(0)) for match in re.finditer(quantities + r"\s*" + units, text))
    phases = tuple(sorted(set(re.findall(r"第?[一二三四五六七八九十百\d]+期|现状|既有|历史|拟建|已建|新增|原有|改造|扩容|本期|往期", text))))
    qualifiers = tuple(re.findall(r"<=|>=|[≤≥<>]|不低于|不高于|至少|至多|以上|以下|超过|少于|大于|等于", text))
    negatives = []
    for label, pattern in (("exemption", r"无需|不必|不需要"),
                           ("prohibition", r"不得|禁止|不能|不允许"),
                           ("absence", r"尚未|仍未|未|没有|缺少|缺乏|遗漏|无(?!需)")):
        if re.search(pattern, text):
            negatives.append(label)
        text = re.sub(pattern, "", text)
    if re.search(r"不|非", text):
        negatives.append("negative")
    return numbers, phases, tuple(negatives), qualifiers


def safe_aliases(context: dict | None) -> dict[str, str]:
    proposed: dict[str, set[str]] = defaultdict(set)
    for item in (context or {}).get("aliases", [])[:MAX_ALIASES]:
        if not isinstance(item, dict):
            continue
        canonical_raw = str(item.get("canonical") or "")[:200]
        canonical = compact(canonical_raw)
        if not 2 <= len(canonical) <= 200:
            continue
        variants = item.get("variants") or []
        if not isinstance(variants, list):
            continue
        for variant in [canonical_raw, *variants[:MAX_VARIANTS]]:
            raw_variant = str(variant)[:200]
            variant = compact(raw_variant)
            if 2 <= len(variant) <= 200 and critical_signature(raw_variant) == critical_signature(canonical_raw):
                proposed[variant].add(canonical)
    # A conflicting alias is excluded, rather than selecting the last declaration.
    conflicts = {key for key, values in proposed.items() if len(values) != 1}
    return {key: next(iter(values)) for key, values in proposed.items()
            if key not in conflicts and next(iter(values)) not in conflicts}


def canonical(value: str, aliases: dict[str, str]) -> str:
    text = compact(value)
    if not aliases:
        return text
    pattern = "|".join(re.escape(key) for key in sorted(aliases, key=lambda x: (-len(x), x)))
    return re.sub(pattern, lambda match: aliases[match.group(0)], text)


def canonical_descriptor(value: dict, aliases: dict[str, str]) -> dict:
    return {key: canonical(str(item), aliases) if key in {"entity", "kind", "evidence"} else item
            for key, item in value.items()}


def tokens(value: str) -> set[str]:
    value = compact(value)[:2400]
    words = set(re.findall(r"[a-z]+\d*|\d+(?:\.\d+)?", value))
    words.update(value[i:i + 2] for i in range(len(value) - 1)
                 if re.search(r"[\u4e00-\u9fff]", value[i:i + 2]))
    return words


def token_overlap(left: str, right: str) -> float:
    a, b = tokens(left), tokens(right)
    return 2 * len(a & b) / (len(a) + len(b)) if a and b else 0.0


def identity_conflict(left: dict, right: dict, aliases: dict[str, str]) -> str | None:
    a, b = canonical_descriptor(left, aliases), canonical_descriptor(right, aliases)
    if bool(a.get("entity")) != bool(b.get("entity")):
        return "missing_entity_identity"
    if a.get("entity") and a["entity"] != b["entity"]:
        return "different_entity"
    for key in ("entity",):
        if critical_signature(str(a.get(key) or "")) != critical_signature(str(b.get(key) or "")):
            return "changed_number_negation_or_phase"
    # Guard common technical object substitutions inside long, otherwise equal evidence.
    objects = ("服务器", "数据库", "操作系统", "防火墙", "交换机", "路由器", "存储设备", "工作站", "终端")
    a_objects = {word for word in objects if word in a.get("evidence", "")}
    b_objects = {word for word in objects if word in b.get("evidence", "")}
    if a_objects != b_objects:
        return "different_evidence_object"
    return None


class LexicalIndex:
    """Index a fixed issue pool; expensive comparisons see at most 48 candidates."""
    def __init__(self, descriptors: list[dict]):
        self.descriptors = descriptors
        self.by_rule: dict[tuple[str, str], list[int]] = defaultdict(list)
        self.postings: dict[tuple[str, str, str], list[int]] = defaultdict(list)
        self.by_entity: dict[tuple[str, str, str], list[int]] = defaultdict(list)
        for index, item in enumerate(descriptors):
            rule = (item.get("rule", ""), item.get("comparison", ""))
            self.by_rule[rule].append(index)
            if item.get("entity"):
                self.by_entity[(*rule, item["entity"])].append(index)
            for token in tokens(item.get("evidence", "")):
                posting = self.postings[(*rule, token)]
                if len(posting) < MAX_POSTING:
                    posting.append(index)

    def candidates(self, item: dict, excluded: set[int]) -> list[int]:
        rule = (item.get("rule", ""), item.get("comparison", ""))
        entities = [index for index in self.by_entity.get((*rule, item.get("entity", "")), [])
                    if index not in excluded]
        pool = entities or [index for index in self.by_rule.get(rule, []) if index not in excluded]
        if len(pool) <= MAX_CANDIDATES:
            return pool
        # If an entity has too many indistinguishable candidates, refuse fuzzy identity.
        # Arbitrarily truncating these candidates could hide an ambiguous second match.
        if entities:
            return []
        counts: Counter = Counter()
        postings = sorted((self.postings[(*rule, token)] for token in tokens(item.get("evidence", ""))
                           if self.postings.get((*rule, token))), key=len)
        allowed = set(pool)
        for posting in postings[:32]:
            counts.update(index for index in posting if index in allowed)
        return [index for index, _ in counts.most_common(MAX_CANDIDATES)]
