"""Local, bounded source excerpts for revision review; retrieval never closes issues."""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from app.services.issue_memory_retrieval import canonical, compact, safe_aliases, token_overlap, tokens

MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_XML_BYTES = 32 * 1024 * 1024
MAX_BLOCKS = 20000
MAX_TEXT_CHARS = 2_000_000
MAX_EXCERPT = 1200
MAX_EVIDENCE_CANDIDATES = 3
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


@dataclass
class SourceText:
    filename: str
    text: str = ""
    blocks: list[dict] = field(default_factory=list)
    complete: bool = False
    limitation: str = ""


def read_source(content: bytes, filename: str) -> SourceText:
    result = SourceText(filename)
    if len(content) > MAX_SOURCE_BYTES:
        result.limitation = "原文超过问题记忆证据读取上限。"
        return result
    total = 0

    def add(text: str, location: dict) -> bool:
        nonlocal total
        if not text.strip():
            return True
        if len(text) > 2400:
            for offset in range(0, len(text), 1600):
                part = text[offset:offset + 1800]
                if not add(part, {**location, "char_start": offset, "char_end": offset + len(part)}):
                    return False
            return True
        if len(result.blocks) >= MAX_BLOCKS or total + len(text) > MAX_TEXT_CHARS:
            result.limitation = "原文超过问题记忆证据读取上限，证据读取未完整完成。"
            return False
        total += len(text)
        result.blocks.append({"text": text, "location": {"file_name": filename, **location}})
        return True

    try:
        suffix = Path(filename).suffix.lower()
        if suffix == ".docx":
            with ZipFile(io.BytesIO(content)) as archive:
                entry = archive.getinfo("word/document.xml")
                if entry.file_size > MAX_XML_BYTES:
                    result.limitation = "文档正文超过问题记忆证据读取上限。"
                    return result
                root = ElementTree.fromstring(archive.read(entry))
            for index, paragraph in enumerate(root.iter(W + "p"), 1):
                text = "".join(node.text or "" for node in paragraph.iter(W + "t"))
                if not add(text, {"paragraph": index, "precision": "paragraph"}):
                    break
        elif suffix in {".txt", ".csv", ".md"}:
            for index, text in enumerate(content.decode("utf-8-sig").splitlines(), 1):
                if not add(text, {"line_start": index, "line_end": index, "precision": "line"}):
                    break
        elif suffix in {".xlsx", ".xlsm"}:
            from openpyxl import load_workbook
            workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            try:
                stop = False
                for sheet in workbook:
                    for index, row in enumerate(sheet.iter_rows(values_only=True), 1):
                        if not add(" | ".join(str(cell) for cell in row if cell is not None),
                                   {"sheet_name": sheet.title, "row_index": index, "precision": "table_row"}):
                            stop = True
                            break
                    if stop:
                        break
            finally:
                workbook.close()
        else:
            result.limitation = "当前文件格式不支持问题记忆正文核验。"
            return result
        result.text = "\n".join(block["text"] for block in result.blocks)
        result.complete = bool(result.text.strip()) and not result.limitation
        if not result.text.strip() and not result.limitation:
            result.limitation = "未能读取可核验的文档正文。"
    except (ValueError, KeyError, OSError, UnicodeError, ElementTree.ParseError, BadZipFile):
        result.limitation = "未能完整读取文档正文。"
    return result


def evidence_text(finding: dict | None) -> str:
    if not finding:
        return ""
    for key in ("evidence", "context", "evidence_examples", "context_examples", "evidence_summary"):
        value = finding.get(key)
        if value:
            if isinstance(value, list):
                return "\n".join(str(item) for item in value[:4])[:MAX_EXCERPT]
            return str(value)[:MAX_EXCERPT]
    location = finding.get("source_location") or {}
    return str(location.get("quote") or "")[:MAX_EXCERPT] if isinstance(location, dict) else ""


def source_locations(finding: dict | None) -> list[dict]:
    finding = finding or {}
    locations = finding.get("source_locations") or finding.get("source_highlights") or []
    if not isinstance(locations, list):
        locations = []
    if not locations and isinstance(finding.get("source_location"), dict):
        location = finding["source_location"]
        locations = location.get("entries") or [location]
    return [dict(item) for item in locations[:6] if isinstance(item, dict)]


class EvidenceIndex:
    def __init__(self, source: SourceText, context: dict | None = None):
        self.source = source
        self.aliases = safe_aliases(context)
        self.values = [canonical(block["text"], self.aliases) for block in source.blocks]
        self.token_sets = [tokens(text) for text in self.values]
        self.postings: dict[str, list[int]] = {}
        for index, text_tokens in enumerate(self.token_sets):
            for token in text_tokens:
                values = self.postings.setdefault(token, [])
                if len(values) < 256:
                    values.append(index)

    def retrieve(self, finding: dict | None) -> list[dict]:
        if not finding or not self.source.text:
            return []
        evidence = canonical(evidence_text(finding), self.aliases)
        entity = canonical(str(finding.get("memory_entity") or finding.get("hit_text") or
                               finding.get("raw_item") or finding.get("item") or
                               finding.get("resource_name") or finding.get("item_name") or ""), self.aliases)
        query = evidence or entity
        if len(query) < 2:
            return []
        counts: dict[int, int] = {}
        query_tokens = tokens(query)
        postings = sorted((self.postings[token] for token in query_tokens if self.postings.get(token)), key=len)
        for posting in postings[:32]:
            for index in posting:
                counts[index] = counts.get(index, 0) + 1
        ranked = sorted(counts, key=lambda index: (-counts[index], index))[:48]
        candidates = []
        for index in ranked:
            text = self.values[index]
            block_tokens = self.token_sets[index]
            overlap = 2 * len(query_tokens & block_tokens) / (len(query_tokens) + len(block_tokens)) if query_tokens and block_tokens else 0
            exact = len(evidence) >= 8 and evidence in text
            if not exact and overlap < .24 and not (entity and entity in text and overlap >= .10):
                continue
            score = 1.0 if exact else min(.95, overlap + (.1 if entity and entity in text else 0))
            block = self.source.blocks[index]
            raw = block["text"]
            quote = raw[:MAX_EXCERPT]
            start = 0
            if len(raw) > MAX_EXCERPT:
                # Map normalized characters back to actual source offsets. The quote
                # is always an unchanged substring, even for full-width characters.
                normalized, positions = [], []
                for position, char in enumerate(raw):
                    value = compact(char)
                    normalized.extend(value)
                    positions.extend([position] * len(value))
                joined = "".join(normalized)
                needles = [compact(evidence_text(finding)), entity]
                needles += sorted(tokens(query), key=lambda token: (-len(token), token))
                hit = next((joined.find(needle) for needle in needles if needle and needle in joined), 0)
                start = max(0, positions[hit] - 150) if positions and hit < len(positions) else 0
                quote = raw[start:start + MAX_EXCERPT]
            location = dict(block["location"])
            if "char_start" in location or start:
                location["char_start"] = location.get("char_start", 0) + start
                location["char_end"] = location["char_start"] + len(quote)
            candidates.append({"score": round(score, 4), "text": quote,
                               "source_location": {**location, "quote": quote},
                               "method": "exact_source_excerpt" if exact else "lexical_source_candidate",
                               "reference_only": True})
        return sorted(candidates, key=lambda item: -item["score"])[:MAX_EVIDENCE_CANDIDATES]


def verification(old: dict | None, finding: dict | None, *, state: str, reason: str,
                 index: EvidenceIndex, previous_result_id: str | None, result_id: str,
                 source_hash: str, match_method: str) -> dict[str, Any]:
    old_finding = (old or {}).get("finding") or {}
    candidates = index.retrieve(finding or old_finding)
    previous = evidence_text(old_finding)
    current = "\n\n".join(item["text"] for item in candidates)[:MAX_EXCERPT]
    locations = [item["source_location"] for item in candidates]
    readable = index.source.complete
    if state == "new":
        summary = "本次首次记录该问题，后续版本将跟踪整改进展。"
    elif state == "resolved":
        summary = "已按相同检查范围确认历史问题解决；本次原文片段供对照。"
    elif state == "reopened":
        summary = "此前已解决的问题在本次规则检查中再次出现。"
    elif finding is not None and state == "changed":
        summary = "已关联历史问题，本次问题内容有变化，仍需按规则意见整改。"
    elif finding is not None:
        summary = "本次规则检查仍检出对应问题。"
    else:
        summary = "本次未形成可靠问题对应；原文检索候选仅供核验，不能据此确认解决。"
    if not readable:
        summary += "本次正文读取不完整，原文对照待确认。"
    remaining = []
    if state != "resolved":
        advice = str((finding or old_finding).get("revision_advice") or
                     (finding or old_finding).get("suggestion") or "")[:800]
        if advice:
            remaining.append(advice)
        if not finding:
            remaining.append(reason or "请在相同规则范围内完成重新核验。")
        if not readable:
            remaining.append(index.source.limitation)
    return {"status": "confirmed_resolved" if state == "resolved" else
                      "rule_issue_present" if finding is not None and readable else "pending_confirmation",
            "reference_only": True, "method": match_method,
            "previous_evidence": previous, "old_evidence": previous, "current_evidence": current,
            "summary": summary, "reason": reason, "remaining_requirements": list(dict.fromkeys(remaining)),
            "source_locations": locations, "evidence_candidates": candidates,
            "source_references": {
                "previous": {"previous_result_id": previous_result_id,
                             "source_hash": (old or {}).get("source_hash"),
                             "source_locations": source_locations(old_finding)},
                "current": {"last_seen_result_id": result_id, "source_hash": source_hash,
                            "source_locations": locations}},
            "source_read_complete": readable, "source_limitation": index.source.limitation}
