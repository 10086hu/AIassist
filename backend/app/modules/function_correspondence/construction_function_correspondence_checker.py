# -*- coding: utf-8 -*-
"""
建设功能对应关系检查模块
Construction Function Correspondence Checker

用途：
    供“可研报告智能审查助手 / Windows 桌面端”调用，用于检查可研报告中
    建设内容、业务需求、系统功能、应用功能、功能点清单、投资概算等内容
    是否形成前后对应关系。

设计原则：
    1. 单文件模块，方便与其他小组检测模块后续合并；
    2. 默认内置“建设功能对应关系检查”规则，也支持从 Excel 或规则库网站 API 加载规则；
    3. 不强制依赖大模型；规则校验可直接运行，大模型 DeepSeek 作为可选语义复核增强；
    4. 输出统一 JSON，便于桌面端展示、后端入库和后续报告导出。

推荐调用：
    from construction_function_correspondence_checker import check_construction_function_correspondence

    result = check_construction_function_correspondence(
        report_path="demo_report.docx",
        rules_xlsx_path="规则库-建设功能的对应关系检查.xlsx",
        project_level="市级项目",
        use_llm=False,
    )

命令行调用：
    python construction_function_correspondence_checker.py demo_report.docx \
        --rules-xlsx 规则库-建设功能的对应关系检查.xlsx \
        --project-level 市级项目 \
        --out result.json

可选 DeepSeek：
    设置环境变量 DEEPSEEK_API_KEY 后加 --use-llm 即可启用语义复核。
"""

from __future__ import annotations

import argparse
import difflib
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


# =============================
# 1. 数据结构
# =============================

@dataclass
class Rule:
    rule_id: str
    rule_name: str
    rule_category: str = "一致性校验规则"
    rule_detail: str = ""
    city_judgement: str = ""
    district_judgement: str = ""
    tags: List[str] = field(default_factory=list)
    source: str = "default"


@dataclass
class Section:
    title: str
    section_type: str
    start_line: int
    end_line: int
    text: str


@dataclass
class FeatureItem:
    name: str
    normalized: str
    layer: str
    source_section: str
    evidence: str
    confidence: float = 0.0


@dataclass
class Finding:
    rule_id: str
    rule_name: str
    rule_category: str
    issue_type: str
    risk_level: str
    description: str
    reason: str
    suggestion: str
    source_section: str
    evidence: str
    matched_rule: Dict[str, Any]
    confidence: float
    need_human_review: bool = False


@dataclass
class CheckResult:
    module_code: str
    module_name: str
    report_path: str
    project_level: str
    status: str
    summary: Dict[str, Any]
    findings: List[Finding]
    matrix: List[Dict[str, Any]]
    rules_used: List[Dict[str, Any]]
    extracted_sections: List[Dict[str, Any]]
    elapsed_seconds: float

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["findings"] = [asdict(x) for x in self.findings]
        return data


# =============================
# 2. 默认规则：来自“规则库-建设功能的对应关系检查.xlsx”
# =============================

DEFAULT_RULES: List[Rule] = [
    Rule(
        rule_id="FUNC_CORR_001",
        rule_category="一致性校验规则",
        rule_name="建设依据与建设内容一致性校验规则",
        rule_detail="项目主要建设内容应与建设依据逐条对应",
        city_judgement="4.1项目建设必要性中的附表检查：主要建设内容与政策依据及摘要形成逐条对应关系",
        district_judgement="主要建设内容与所提供的建设依据能够一一对应",
        tags=["市级项目", "区级项目"],
        source="default_excel",
    ),
    Rule(
        rule_id="FUNC_CORR_002",
        rule_category="一致性校验规则",
        rule_name="建设依据上下文一致性校验规则",
        rule_detail="全文建设依据保持一致",
        city_judgement="2.3中摘要列举的建设依据与4.1附表的政策依据相一致",
        district_judgement="",
        tags=["市级项目"],
        source="default_excel",
    ),
    Rule(
        rule_id="FUNC_CORR_003",
        rule_category="一致性校验规则",
        rule_name="建设目标、建设内容、建设周期的上下文一致性校验规则",
        rule_detail="全文建设目标、建设内容、建设周期应保持描述一致",
        city_judgement="2.4与5.1建设目标、建设内容应相一致；2.4与9.3的建设周期应相一致",
        district_judgement="全文建设目标、建设内容、建设周期的描述相一致",
        tags=["市级项目", "区级项目"],
        source="default_excel",
    ),
    Rule(
        rule_id="FUNC_CORR_004",
        rule_category="一致性校验规则",
        rule_name="建设内容一致性校验规则",
        rule_detail="功能点需在需求描述章节、建设内容章节及投资概算章节中，形成一一对应的关联关系",
        city_judgement="4.3业务功能分析、4.6系统功能需求分析、5.1建设内容、第六章项目建设内容、第七章数据产出内容、第八章项目预算应能关联对应",
        district_judgement="需求分析、建设内容、功能点设计、投资概算应能关联对应",
        tags=["市级项目", "区级项目"],
        source="default_excel",
    ),
    Rule(
        rule_id="FUNC_CORR_005",
        rule_category="内容合规性审查规则",
        rule_name="建设依据与建设内容的相关性审查规则",
        rule_detail="列举的建设依据与本项目建设内容具有相关性",
        city_judgement="识别建设依据的摘要描述是否与本项目建设内容相关",
        district_judgement="识别建设依据的摘要描述是否与本项目建设内容相关",
        tags=["所有项目"],
        source="default_excel",
    ),
]

# =============================
# 3. 文档解析：DOCX / PDF / TXT
# =============================

_XML_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


def _clean_text(s: str) -> str:
    if not s:
        return ""
    s = s.replace("\u3000", " ").replace("\xa0", " ")
    s = re.sub(r"[\t\r\f\v]+", " ", s)
    s = re.sub(r" +", " ", s)
    return s.strip()


def _extract_docx_paragraphs_and_tables(path: str) -> Tuple[List[str], List[List[List[str]]]]:
    """纯标准库读取 docx 正文段落和表格。"""
    paragraphs: List[str] = []
    tables: List[List[List[str]]] = []

    with zipfile.ZipFile(path) as zf:
        xml = zf.read("word/document.xml")
    root = ET.fromstring(xml)
    body = root.find("w:body", _XML_NS)
    if body is None:
        return paragraphs, tables

    for child in list(body):
        tag = child.tag.split("}")[-1]
        if tag == "p":
            texts = [node.text or "" for node in child.findall(".//w:t", _XML_NS)]
            line = _clean_text("".join(texts))
            if line:
                paragraphs.append(line)
        elif tag == "tbl":
            table_rows: List[List[str]] = []
            for tr in child.findall(".//w:tr", _XML_NS):
                cells: List[str] = []
                for tc in tr.findall("./w:tc", _XML_NS):
                    texts = [node.text or "" for node in tc.findall(".//w:t", _XML_NS)]
                    cells.append(_clean_text("".join(texts)))
                if any(cells):
                    table_rows.append(cells)
                    paragraphs.append(" | ".join([c for c in cells if c]))
            if table_rows:
                tables.append(table_rows)

    return paragraphs, tables


def _extract_pdf_text(path: str) -> Tuple[List[str], List[List[List[str]]]]:
    """读取 PDF 文本。优先 pdfplumber；其次 PyMuPDF。"""
    paragraphs: List[str] = []
    tables: List[List[List[str]]] = []

    try:
        import pdfplumber  # type: ignore
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                for line in text.splitlines():
                    line = _clean_text(line)
                    if line:
                        paragraphs.append(line)
                try:
                    for tb in page.extract_tables() or []:
                        rows = [[_clean_text(str(c or "")) for c in row] for row in tb]
                        rows = [row for row in rows if any(row)]
                        if rows:
                            tables.append(rows)
                except Exception:
                    pass
        return paragraphs, tables
    except Exception:
        pass

    try:
        import fitz  # type: ignore
        doc = fitz.open(path)
        for page in doc:
            text = page.get_text() or ""
            for line in text.splitlines():
                line = _clean_text(line)
                if line:
                    paragraphs.append(line)
        return paragraphs, tables
    except Exception as exc:
        raise RuntimeError("PDF 解析失败：请安装 pdfplumber 或 PyMuPDF，或转换为 Word 后再检测") from exc


def parse_report_file(path: str) -> Tuple[List[str], List[List[List[str]]]]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"报告文件不存在：{path}")

    suffix = p.suffix.lower()
    if suffix == ".docx":
        return _extract_docx_paragraphs_and_tables(str(p))
    if suffix == ".pdf":
        return _extract_pdf_text(str(p))
    if suffix in {".txt", ".md"}:
        text = p.read_text(encoding="utf-8", errors="ignore")
        lines = [_clean_text(x) for x in text.splitlines()]
        return [x for x in lines if x], []

    raise ValueError(f"暂不支持的报告格式：{suffix}，请使用 .docx/.pdf/.txt")


# =============================
# 4. 规则加载：默认规则 / Excel / 规则库 API
# =============================

def _xlsx_col_to_index(cell_ref: str) -> int:
    m = re.match(r"([A-Z]+)", cell_ref)
    if not m:
        return 0
    letters = m.group(1)
    num = 0
    for ch in letters:
        num = num * 26 + (ord(ch) - 64)
    return num - 1


def _read_simple_xlsx_first_sheet(path: str) -> List[List[Any]]:
    """标准库读取简单 xlsx 的第一个 sheet，足够读取规则清单。"""
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        shared_strings: List[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root:
                texts = []
                for t in si.iter():
                    if t.tag.endswith("}t") or t.tag == "t":
                        texts.append(t.text or "")
                shared_strings.append("".join(texts))

        sheet_name = "xl/worksheets/sheet1.xml"
        if sheet_name not in names:
            sheet_candidates = [n for n in names if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")]
            if not sheet_candidates:
                return []
            sheet_name = sorted(sheet_candidates)[0]

        root = ET.fromstring(zf.read(sheet_name))

    rows: List[List[Any]] = []
    for row in root.iter():
        if not row.tag.endswith("}row") and row.tag != "row":
            continue
        values: Dict[int, Any] = {}
        for c in list(row):
            if not (c.tag.endswith("}c") or c.tag == "c"):
                continue
            ref = c.attrib.get("r", "A1")
            col_idx = _xlsx_col_to_index(ref)
            cell_type = c.attrib.get("t", "")
            value = ""
            v_node = None
            for x in list(c):
                if x.tag.endswith("}v") or x.tag == "v":
                    v_node = x
                    break
                # inline string
                if x.tag.endswith("}is") or x.tag == "is":
                    texts = []
                    for t in x.iter():
                        if t.tag.endswith("}t") or t.tag == "t":
                            texts.append(t.text or "")
                    value = "".join(texts)
                    break
            if v_node is not None:
                raw = v_node.text or ""
                if cell_type == "s":
                    try:
                        value = shared_strings[int(raw)]
                    except Exception:
                        value = raw
                else:
                    value = raw
            values[col_idx] = _clean_text(str(value)) if value is not None else ""
        if values:
            max_idx = max(values)
            rows.append([values.get(i, "") for i in range(max_idx + 1)])
    return rows


def load_rules_from_xlsx(path: str) -> List[Rule]:
    rows = _read_simple_xlsx_first_sheet(path)
    if not rows:
        return []

    header = [str(x).strip() for x in rows[0]]
    idx = {name: i for i, name in enumerate(header)}

    def get(row: List[Any], name: str) -> str:
        i = idx.get(name)
        if i is None or i >= len(row):
            return ""
        return _clean_text(str(row[i] or ""))

    rules: List[Rule] = []
    current: Optional[Rule] = None

    for row in rows[1:]:
        serial = get(row, "序号")
        category = get(row, "规则类别")
        name = get(row, "规则名称")
        detail = get(row, "规则描述")
        condition = get(row, "判断条件")
        tag = get(row, "规则标签")

        if serial or name:
            rid = f"FUNC_CORR_{len(rules) + 1:03d}"
            current = Rule(
                rule_id=rid,
                rule_category=category or "一致性校验规则",
                rule_name=name or f"建设功能对应关系规则{len(rules)+1}",
                rule_detail=detail,
                city_judgement="",
                district_judgement="",
                tags=[],
                source="xlsx",
            )
            rules.append(current)

        if current is None:
            continue

        if tag:
            if tag not in current.tags:
                current.tags.append(tag)
            if "市级" in tag:
                current.city_judgement = condition or current.city_judgement
            elif "区级" in tag:
                current.district_judgement = condition or current.district_judgement
            elif "所有" in tag:
                current.city_judgement = condition or current.city_judgement
                current.district_judgement = condition or current.district_judgement
        elif condition and not current.city_judgement:
            current.city_judgement = condition

    return rules


def load_rules_from_api(
    base_url: str,
    keyword: str = "建设",
    page_size: int = 200,
    timeout: int = 12,
) -> List[Rule]:
    """从规则库网站接口加载规则。默认读取 /api/rule-library/list。"""
    base = base_url.rstrip("/")
    params = urllib.parse.urlencode({"page": 1, "page_size": page_size, "keyword": keyword})
    url = f"{base}/api/rule-library/list?{params}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "aiassist-rule-loader/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"规则库接口调用失败：{url}；{exc}") from exc

    data = payload.get("data", payload)
    items = data.get("items", []) if isinstance(data, dict) else []
    rules: List[Rule] = []
    for item in items:
        name = item.get("rule_name") or item.get("name") or ""
        detail = item.get("rule_detail") or item.get("rule_condition") or ""
        category = item.get("rule_category") or item.get("category") or ""
        tags = item.get("tags") or []
        if isinstance(tags, str):
            tags = re.split(r"[;,，；、\s]+", tags)
        tags = [str(x).strip() for x in tags if str(x).strip()]

        text_all = " ".join([name, detail, category, " ".join(tags)])
        # 尽量只保留建设功能对应关系相关规则，避免把全部规则纳入本模块。
        if not any(k in text_all for k in ["建设", "功能", "对应", "一致", "依据", "内容"]):
            continue

        rules.append(
            Rule(
                rule_id=str(item.get("id") or item.get("rule_id") or f"API_{len(rules)+1}"),
                rule_name=name,
                rule_category=category or "一致性校验规则",
                rule_detail=detail,
                city_judgement=item.get("city_judgement") or "",
                district_judgement=item.get("district_judgement") or "",
                tags=tags,
                source="api",
            )
        )
    return rules


def load_rules(
    rules_xlsx_path: Optional[str] = None,
    rule_api_base: Optional[str] = None,
    keyword: str = "建设",
) -> List[Rule]:
    if rules_xlsx_path:
        rules = load_rules_from_xlsx(rules_xlsx_path)
        if rules:
            return rules
    if rule_api_base:
        rules = load_rules_from_api(rule_api_base, keyword=keyword)
        if rules:
            return rules
    return list(DEFAULT_RULES)


# =============================
# 5. 章节识别与功能要素抽取
# =============================

SECTION_KEYWORDS: Dict[str, List[str]] = {
    "建设依据": ["建设依据", "政策依据", "项目依据", "编制依据", "建设背景", "项目建设必要性"],
    "建设目标": ["建设目标", "项目目标", "总体目标", "具体目标"],
    "建设周期": ["建设周期", "实施周期", "建设工期", "项目周期", "实施进度", "进度计划"],
    "建设内容": ["建设内容", "主要建设内容", "项目建设内容", "建设任务", "建设范围", "建设方案"],
    "业务需求": ["业务需求", "需求分析", "业务功能分析", "业务分析", "业务需求分析"],
    "系统功能": ["系统功能需求", "功能需求", "系统功能", "系统需求", "功能需求分析"],
    "应用功能": ["应用功能", "应用系统", "应用功能设计", "功能设计", "应用设计", "系统设计"],
    "功能点清单": ["功能点清单", "功能清单", "功能列表", "功能点", "功能项", "功能明细"],
    "投资概算": ["投资概算", "项目预算", "投资估算", "预算表", "概算", "第八章项目预算", "经费预算"],
}

LAYER_ORDER = ["建设内容", "业务需求", "系统功能", "应用功能", "功能点清单", "投资概算"]


def _is_probable_heading(line: str) -> bool:
    if not line or len(line) > 80:
        return False
    if re.match(r"^(第[一二三四五六七八九十百\d]+[章节部分]|[一二三四五六七八九十百\d]+[、.．]|\d+(\.\d+){0,3}[、.．\s])", line):
        return True
    return any(any(k in line for k in ks) for ks in SECTION_KEYWORDS.values()) and len(line) <= 60


def _classify_section(title: str) -> str:
    for sec_type, keys in SECTION_KEYWORDS.items():
        if any(k in title for k in keys):
            return sec_type
    return "其他"


def detect_sections(lines: List[str]) -> List[Section]:
    if not lines:
        return []

    headings: List[Tuple[int, str, str]] = []
    for i, line in enumerate(lines):
        if _is_probable_heading(line):
            sec_type = _classify_section(line)
            headings.append((i, line, sec_type))

    if not headings:
        return [Section("全文", "其他", 0, len(lines), "\n".join(lines))]

    sections: List[Section] = []
    for idx, (start, title, sec_type) in enumerate(headings):
        end = headings[idx + 1][0] if idx + 1 < len(headings) else len(lines)
        body = "\n".join(lines[start:end])
        sections.append(Section(title=title, section_type=sec_type, start_line=start, end_line=end, text=body))

    # 合并同类章节文本，保留原始章节。
    return sections


STOP_WORDS = {
    "建设", "项目", "系统", "平台", "模块", "功能", "应用", "服务", "管理", "实现", "支持", "提供",
    "进行", "相关", "内容", "需求", "分析", "设计", "清单", "包括", "主要", "本项目", "本系统",
}

FEATURE_SUFFIX = r"(?:功能|模块|系统|平台|接口|服务|大屏|驾驶舱|模型|算法|数据库|数据集|终端|应用|管理|审核|审计|分析|监测|预警|查询|统计|报表|共享|交换|采集|治理|归集|对接)"


def normalize_feature_name(text: str) -> str:
    text = _clean_text(text)
    text = re.sub(r"[（(].*?[）)]", "", text)
    text = re.sub(r"^第?[一二三四五六七八九十百\d]+[、.．\s-]*", "", text)
    text = re.sub(r"[：:；;，,。\.、\s\-—_\[\]【】《》<>/\\|]+", "", text)
    text = text.lower()
    replace_pairs = [
        ("信息化", "数字化"),
        ("数据共享交换", "数据共享"),
        ("共享交换", "共享"),
        ("用户权限", "权限"),
        ("身份认证", "认证"),
        ("日志审计", "审计"),
        ("统计分析", "分析"),
        ("数据共享平台", "数据共享"),
        ("智能审核系统", "智能审核"),
    ]
    for a, b in replace_pairs:
        text = text.replace(a, b)

    # 末尾通用类型词通常不作为核心语义区分项，用于提高“系统/功能/模块”之间的对应识别率。
    text = re.sub(r"(建设费用|开发费用|设备费用|采购费用|服务费用|费用|预算|概算)$", "", text)
    text = re.sub(r"(功能|模块|系统|平台|接口|服务|应用|建设|设计|清单|列表|明细|项目|内容)$", "", text)
    return text[:80]


def _split_candidates(text: str) -> List[str]:
    parts = re.split(r"[。；;\n\r]|(?<=等)[，,、]|[、]", text)
    result = []
    for p in parts:
        p = _clean_text(p)
        if 2 <= len(p) <= 80:
            result.append(p)
    return result


def _extract_feature_names_from_text(text: str) -> List[str]:
    names: List[str] = []
    for sent in _split_candidates(text):
        if not any(k in sent for k in ["功能", "模块", "系统", "平台", "接口", "服务", "数据", "应用", "管理", "审核", "审计", "共享", "交换", "建设"]):
            continue

        # 抽取类似“智能审核功能”“数据共享接口”“日志审计模块”等短语。
        matched_in_sentence = False
        for m in re.finditer(r"([\u4e00-\u9fa5A-Za-z0-9]{2,24}" + FEATURE_SUFFIX + r")", sent):
            name = _clean_text(m.group(1))
            if name and name not in names:
                names.append(name)
                matched_in_sentence = True

        # 如果句子本身较短且没有抽取到更具体短语，也作为候选。
        if not matched_in_sentence and 4 <= len(sent) <= 40 and sent not in names:
            names.append(sent)

    # 去重并过滤过泛表述。
    cleaned: List[str] = []
    seen = set()
    for n in names:
        n = re.sub(r"^(本项目|本系统|系统|项目)?(拟|需|应|需要|计划)?(建设|实现|提供|完成|新增|完善|开发|配置|采购)", "", n).strip()
        if len(n) < 3:
            continue
        # 过滤章节标题或字段名，避免“建设内容”“功能点清单”等被误当成功能点。
        if _is_probable_heading(n) or n in SECTION_KEYWORDS or any(n == k for keys in SECTION_KEYWORDS.values() for k in keys):
            continue
        norm = normalize_feature_name(n)
        if not norm or norm in STOP_WORDS or len(norm) < 2:
            continue
        if norm not in seen:
            seen.add(norm)
            cleaned.append(n)
    return cleaned


def _infer_layer_from_text(text: str, default_layer: str = "其他") -> str:
    for layer in LAYER_ORDER:
        if any(k in text for k in SECTION_KEYWORDS.get(layer, [])):
            return layer
    return default_layer


def extract_feature_items(sections: List[Section], tables: List[List[List[str]]]) -> List[FeatureItem]:
    items: List[FeatureItem] = []

    for sec in sections:
        layer = sec.section_type if sec.section_type in LAYER_ORDER else _infer_layer_from_text(sec.title, "其他")
        if layer not in LAYER_ORDER:
            # 其他章节中也可能包含清单或预算关键词，逐句再判断。
            layer = _infer_layer_from_text(sec.text, "其他")
        if layer not in LAYER_ORDER:
            continue

        for line in sec.text.splitlines():
            for name in _extract_feature_names_from_text(line):
                items.append(
                    FeatureItem(
                        name=name,
                        normalized=normalize_feature_name(name),
                        layer=layer,
                        source_section=sec.title,
                        evidence=line[:220],
                        confidence=0.65,
                    )
                )

    # 表格抽取：如果某一行包含功能/模块/建设内容等关键词，则抽取单元格。
    for table in tables:
        if not table:
            continue
        header = table[0]
        header_text = " ".join(header)
        table_layer = _infer_layer_from_text(header_text, "其他")
        for row in table[1:] if len(table) > 1 else table:
            row_text = " | ".join(row)
            layer = _infer_layer_from_text(row_text, table_layer)
            if layer not in LAYER_ORDER:
                layer = table_layer if table_layer in LAYER_ORDER else "功能点清单" if any(k in header_text for k in ["功能", "模块", "清单"]) else "其他"
            if layer not in LAYER_ORDER:
                continue
            cells = [c for c in row if c and any(k in c for k in ["功能", "模块", "系统", "平台", "接口", "服务", "数据", "应用", "管理", "审核", "审计", "共享", "交换"])]
            for cell in cells:
                for name in _extract_feature_names_from_text(cell):
                    items.append(
                        FeatureItem(
                            name=name,
                            normalized=normalize_feature_name(name),
                            layer=layer,
                            source_section="表格内容",
                            evidence=row_text[:220],
                            confidence=0.75,
                        )
                    )

    # 去重：同一层同一标准名只保留证据最长的一条。
    best: Dict[Tuple[str, str], FeatureItem] = {}
    for item in items:
        key = (item.layer, item.normalized)
        if key not in best or len(item.evidence) > len(best[key].evidence):
            best[key] = item
    return list(best.values())


# =============================
# 6. 对应关系矩阵与规则校验
# =============================

def feature_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.92
    seq = difflib.SequenceMatcher(None, a, b).ratio()
    set_a, set_b = set(a), set(b)
    jaccard = len(set_a & set_b) / max(1, len(set_a | set_b))
    # 连续二字片段重合，可提升中文短语匹配。
    grams_a = {a[i:i+2] for i in range(max(0, len(a)-1))}
    grams_b = {b[i:i+2] for i in range(max(0, len(b)-1))}
    gram = len(grams_a & grams_b) / max(1, len(grams_a | grams_b))
    return max(seq * 0.55 + jaccard * 0.25 + gram * 0.20, seq)


def cluster_features(items: List[FeatureItem], threshold: float = 0.72) -> List[Dict[str, Any]]:
    clusters: List[Dict[str, Any]] = []
    for item in sorted(items, key=lambda x: (-len(x.normalized), x.layer)):
        best_idx, best_score = -1, 0.0
        for i, c in enumerate(clusters):
            score = feature_similarity(item.normalized, c["normalized"])
            if score > best_score:
                best_idx, best_score = i, score
        if best_idx >= 0 and best_score >= threshold:
            clusters[best_idx]["items"].append(item)
            # 选更短、更像名称的作为 canonical 展示。
            if len(item.name) < len(clusters[best_idx]["canonical"]):
                clusters[best_idx]["canonical"] = item.name
                clusters[best_idx]["normalized"] = item.normalized
        else:
            clusters.append({"canonical": item.name, "normalized": item.normalized, "items": [item]})

    matrix: List[Dict[str, Any]] = []
    for c in clusters:
        layer_map: Dict[str, List[FeatureItem]] = {layer: [] for layer in LAYER_ORDER}
        for it in c["items"]:
            if it.layer in layer_map:
                layer_map[it.layer].append(it)
        row = {
            "feature": c["canonical"],
            "normalized": c["normalized"],
            "coverage": {layer: bool(layer_map[layer]) for layer in LAYER_ORDER},
            "evidence": {
                layer: [
                    {"name": it.name, "section": it.source_section, "text": it.evidence}
                    for it in layer_map[layer][:3]
                ]
                for layer in LAYER_ORDER
            },
            "coverage_count": sum(1 for layer in LAYER_ORDER if layer_map[layer]),
        }
        matrix.append(row)

    return sorted(matrix, key=lambda x: (-x["coverage_count"], x["feature"]))


def _rule_by_id(rules: List[Rule], suffix: str, fallback_idx: int = 0) -> Rule:
    for r in rules:
        if r.rule_id.endswith(suffix):
            return r
    if fallback_idx < len(rules):
        return rules[fallback_idx]
    return DEFAULT_RULES[0]


def _risk_by_missing_count(missing_count: int) -> str:
    if missing_count >= 2:
        return "中"
    return "低"


def build_findings_from_matrix(matrix: List[Dict[str, Any]], rules: List[Rule]) -> List[Finding]:
    findings: List[Finding] = []
    main_rule = _rule_by_id(rules, "004", fallback_idx=min(3, len(rules)-1))

    for row in matrix:
        cov = row["coverage"]
        feature = row["feature"]

        missing: List[str] = []
        issue_type = ""
        reason = ""
        suggestion = ""
        risk = "低"
        need_review = False

        if cov.get("建设内容") and not (cov.get("业务需求") or cov.get("系统功能") or cov.get("应用功能") or cov.get("功能点清单")):
            missing = ["业务需求", "系统功能", "应用功能", "功能点清单"]
            issue_type = "建设内容缺少后续功能支撑"
            reason = f"“{feature}”在建设内容中出现，但未在需求分析、功能设计或功能点清单中形成对应。"
            suggestion = "建议在业务需求、系统功能、应用功能或功能点清单中补充对应内容，说明该建设内容的需求来源、功能边界和落地形式。"
            risk = "中"
        elif cov.get("功能点清单") and not (cov.get("建设内容") or cov.get("业务需求")):
            missing = ["建设内容", "业务需求"]
            issue_type = "功能点缺少前置依据"
            reason = f"“{feature}”出现在功能点清单中，但未在建设内容或业务需求章节中找到明显前置来源。"
            suggestion = "建议在建设内容或业务需求分析中补充该功能点的建设依据、业务场景和必要性说明。"
            risk = "中"
        elif cov.get("投资概算") and not (cov.get("建设内容") or cov.get("功能点清单")):
            missing = ["建设内容", "功能点清单"]
            issue_type = "预算内容缺少建设功能对应"
            reason = f"“{feature}”疑似出现在预算或投资概算中，但未在建设内容或功能清单中找到对应说明。"
            suggestion = "建议核对预算项是否与具体建设内容、功能模块或功能点清单相对应，必要时补充说明或调整预算项。"
            risk = "中"
        elif row["coverage_count"] == 1:
            present_layer = [k for k, v in cov.items() if v]
            issue_type = "孤立功能项"
            reason = f"“{feature}”仅在{present_layer[0] if present_layer else '单一章节'}中出现，跨章节对应证据不足。"
            suggestion = "建议补充该功能项在建设内容、需求分析、功能设计、功能清单或预算章节中的对应描述。"
            risk = "低"
            need_review = True
        else:
            continue

        evidence_text = ""
        source_section = ""
        for layer in LAYER_ORDER:
            evs = row["evidence"].get(layer) or []
            if evs:
                evidence_text = evs[0].get("text", "")
                source_section = evs[0].get("section", layer)
                break

        findings.append(
            Finding(
                rule_id=main_rule.rule_id,
                rule_name=main_rule.rule_name,
                rule_category=main_rule.rule_category,
                issue_type=issue_type,
                risk_level=risk,
                description=f"功能项“{feature}”存在对应关系风险，缺少环节：{', '.join(missing) if missing else '需进一步核实'}。",
                reason=reason,
                suggestion=suggestion,
                source_section=source_section or "未定位",
                evidence=evidence_text,
                matched_rule={
                    "rule_detail": main_rule.rule_detail,
                    "city_judgement": main_rule.city_judgement,
                    "district_judgement": main_rule.district_judgement,
                    "tags": main_rule.tags,
                },
                confidence=0.78 if risk == "中" else 0.62,
                need_human_review=need_review,
            )
        )

    return findings


def _extract_key_phrases(text: str, max_count: int = 30) -> List[str]:
    candidates = []
    for name in _extract_feature_names_from_text(text):
        n = normalize_feature_name(name)
        if n and n not in candidates:
            candidates.append(n)
    return candidates[:max_count]


def _section_text(sections: List[Section], section_type: str) -> str:
    return "\n".join(sec.text for sec in sections if sec.section_type == section_type)


def build_findings_for_context_consistency(sections: List[Section], rules: List[Rule]) -> List[Finding]:
    """补充校验：建设依据/目标/周期与建设内容的一致性。"""
    findings: List[Finding] = []
    basis_text = _section_text(sections, "建设依据")
    content_text = _section_text(sections, "建设内容")
    target_text = _section_text(sections, "建设目标")
    period_text = _section_text(sections, "建设周期")

    if basis_text and content_text:
        rule = _rule_by_id(rules, "001", 0)
        basis_phrases = _extract_key_phrases(basis_text, 20)
        content_phrases = _extract_key_phrases(content_text, 20)
        if basis_phrases and content_phrases:
            best = 0.0
            for a in basis_phrases:
                for b in content_phrases:
                    best = max(best, feature_similarity(a, b))
            if best < 0.35:
                findings.append(Finding(
                    rule_id=rule.rule_id,
                    rule_name=rule.rule_name,
                    rule_category=rule.rule_category,
                    issue_type="建设依据与建设内容相关性不足",
                    risk_level="低",
                    description="建设依据章节与建设内容章节之间未发现明显功能或建设内容对应线索。",
                    reason="系统从建设依据和建设内容中抽取的核心短语相似度较低，可能存在依据描述与本项目建设内容关联不足的问题。",
                    suggestion="建议补充建设依据与主要建设内容之间的对应说明，必要时以附表形式逐条列明政策依据、摘要和对应建设内容。",
                    source_section="建设依据 / 建设内容",
                    evidence=(basis_text[:180] + " ... " + content_text[:180])[:360],
                    matched_rule={"rule_detail": rule.rule_detail, "city_judgement": rule.city_judgement, "district_judgement": rule.district_judgement, "tags": rule.tags},
                    confidence=0.58,
                    need_human_review=True,
                ))

    # 目标与内容存在一方缺失时提示，不强行判高风险。
    if (target_text and not content_text) or (content_text and not target_text):
        rule = _rule_by_id(rules, "003", 2)
        findings.append(Finding(
            rule_id=rule.rule_id,
            rule_name=rule.rule_name,
            rule_category=rule.rule_category,
            issue_type="建设目标与建设内容对应不足",
            risk_level="低",
            description="建设目标与建设内容章节未形成完整互证关系。",
            reason="系统检测到建设目标或建设内容存在一方缺失或难以定位，可能影响前后文一致性判断。",
            suggestion="建议核对建设目标、建设内容和建设周期描述，确保全文口径一致，并在相关章节补充对应关系。",
            source_section="建设目标 / 建设内容",
            evidence=(target_text[:160] + " ... " + content_text[:160])[:320],
            matched_rule={"rule_detail": rule.rule_detail, "city_judgement": rule.city_judgement, "district_judgement": rule.district_judgement, "tags": rule.tags},
            confidence=0.55,
            need_human_review=True,
        ))

    return findings


# =============================
# 7. 可选 DeepSeek 语义复核
# =============================

def call_deepseek_json(prompt: str, api_key: Optional[str] = None, timeout: int = 30) -> Optional[Dict[str, Any]]:
    """调用 DeepSeek/OpenAI 兼容接口，返回 JSON。默认使用 DEEPSEEK_API_KEY。"""
    key = api_key or os.getenv("DEEPSEEK_API_KEY")
    if not key:
        return None
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    url = base_url.rstrip("/") + "/chat/completions"

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是可研报告审查助手，只输出合法 JSON，不要输出 Markdown。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        content = payload["choices"][0]["message"]["content"]
        content = content.strip()
        content = re.sub(r"^```json\s*", "", content)
        content = re.sub(r"```$", "", content)
        return json.loads(content)
    except Exception:
        return None


def llm_review_findings(findings: List[Finding], matrix: List[Dict[str, Any]], api_key: Optional[str] = None, limit: int = 8) -> List[Finding]:
    if not findings:
        return findings

    for f in findings[:limit]:
        prompt = {
            "任务": "复核建设功能对应关系检查结果，判断问题是否成立。",
            "待复核问题": asdict(f),
            "输出要求": {
                "is_valid": "问题是否成立，true/false",
                "risk_level": "高/中/低/需人工确认",
                "reason": "简要判断理由",
                "suggestion": "修改建议",
            },
        }
        result = call_deepseek_json(json.dumps(prompt, ensure_ascii=False), api_key=api_key)
        if not isinstance(result, dict):
            continue
        if result.get("is_valid") is False:
            f.need_human_review = True
            f.confidence = min(f.confidence, 0.50)
            f.risk_level = "需人工确认"
            f.reason = "大模型复核认为该问题可能不成立或需要人工结合上下文确认。" + ("；" + str(result.get("reason")) if result.get("reason") else "")
        else:
            if result.get("risk_level") in {"高", "中", "低", "需人工确认"}:
                f.risk_level = str(result["risk_level"])
            if result.get("reason"):
                f.reason = str(result["reason"])
            if result.get("suggestion"):
                f.suggestion = str(result["suggestion"])
            f.confidence = max(f.confidence, 0.72)
    return findings


# =============================
# 8. 主检测入口
# =============================

def filter_rules_by_project_level(rules: List[Rule], project_level: str) -> List[Rule]:
    if not project_level:
        return rules
    selected = []
    for r in rules:
        tag_text = " ".join(r.tags)
        if "所有" in tag_text or project_level in tag_text or project_level.replace("项目", "") in tag_text:
            selected.append(r)
    return selected or rules


def check_construction_function_correspondence(
    report_path: str,
    rules_xlsx_path: Optional[str] = None,
    rule_api_base: Optional[str] = None,
    project_level: str = "市级项目",
    use_llm: bool = False,
    deepseek_api_key: Optional[str] = None,
    output_path: Optional[str] = None,
    selected_rule_ids: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """
    建设功能对应关系检查主函数。

    参数：
        report_path: 可研报告路径，支持 .docx/.pdf/.txt。
        rules_xlsx_path: 建设功能对应关系检查规则 Excel，可为空；为空时使用内置规则。
        rule_api_base: 规则库网站基础地址，例如 https://xxx.ngrok-free.dev，可为空。
        project_level: 市级项目 / 区级项目 / 所有项目。
        use_llm: 是否启用 DeepSeek 语义复核。
        deepseek_api_key: DeepSeek API Key；为空时读环境变量 DEEPSEEK_API_KEY。
        output_path: 结果 JSON 输出路径。

    返回：
        dict，可直接序列化为 JSON。
    """
    start = time.time()
    lines, tables = parse_report_file(report_path)
    sections = detect_sections(lines)
    rules = filter_rules_by_project_level(load_rules(rules_xlsx_path, rule_api_base), project_level)
    if selected_rule_ids:
        selected = {str(rule_id).strip() for rule_id in selected_rule_ids if str(rule_id).strip()}
        filtered_rules = [rule for rule in rules if rule.rule_id in selected]
        if filtered_rules:
            rules = filtered_rules

    items = extract_feature_items(sections, tables)
    matrix = cluster_features(items)
    findings = []
    findings.extend(build_findings_from_matrix(matrix, rules))
    findings.extend(build_findings_for_context_consistency(sections, rules))

    # 去重：按 issue_type + description + evidence 合并。
    dedup: Dict[str, Finding] = {}
    for f in findings:
        key = normalize_feature_name(f.issue_type + f.description + f.evidence[:80])
        if key not in dedup or f.confidence > dedup[key].confidence:
            dedup[key] = f
    findings = list(dedup.values())

    if use_llm:
        findings = llm_review_findings(findings, matrix, api_key=deepseek_api_key)

    risk_counter = Counter(f.risk_level for f in findings)
    issue_counter = Counter(f.issue_type for f in findings)

    status = "通过" if not findings else "发现问题"
    result = CheckResult(
        module_code="construction_function_correspondence",
        module_name="建设功能的对应关系检查",
        report_path=str(report_path),
        project_level=project_level,
        status=status,
        summary={
            "total_findings": len(findings),
            "risk_count": dict(risk_counter),
            "issue_type_count": dict(issue_counter),
            "feature_item_count": len(items),
            "matrix_row_count": len(matrix),
            "rules_used_count": len(rules),
            "used_llm_review": bool(use_llm),
        },
        findings=sorted(findings, key=lambda x: ({"高": 0, "中": 1, "低": 2, "需人工确认": 3}.get(x.risk_level, 4), -x.confidence)),
        matrix=matrix,
        rules_used=[asdict(r) for r in rules],
        extracted_sections=[asdict(sec) for sec in sections if sec.section_type != "其他"],
        elapsed_seconds=round(time.time() - start, 3),
    )

    data = result.to_dict()
    if output_path:
        Path(output_path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


# 兼容不同合作者可能采用的调用命名。
def run_check(report_path: str, **kwargs: Any) -> Dict[str, Any]:
    return check_construction_function_correspondence(report_path, **kwargs)


def check(report_path: str, **kwargs: Any) -> Dict[str, Any]:
    return check_construction_function_correspondence(report_path, **kwargs)


# =============================
# 9. 命令行入口
# =============================

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="建设功能对应关系检查模块")
    parser.add_argument("report", help="可研报告路径，支持 .docx/.pdf/.txt")
    parser.add_argument("--rules-xlsx", default=None, help="建设功能对应关系检查规则 Excel 路径")
    parser.add_argument("--rule-api", default=None, help="规则库网站地址，例如 https://xxx.ngrok-free.dev")
    parser.add_argument("--project-level", default="市级项目", choices=["市级项目", "区级项目", "所有项目"], help="项目层级")
    parser.add_argument("--use-llm", action="store_true", help="启用 DeepSeek 语义复核")
    parser.add_argument("--deepseek-api-key", default=None, help="DeepSeek API Key，可为空，默认读取环境变量")
    parser.add_argument("--out", default="construction_function_correspondence_result.json", help="输出 JSON 文件路径")
    args = parser.parse_args(argv)

    result = check_construction_function_correspondence(
        report_path=args.report,
        rules_xlsx_path=args.rules_xlsx,
        rule_api_base=args.rule_api,
        project_level=args.project_level,
        use_llm=args.use_llm,
        deepseek_api_key=args.deepseek_api_key,
        output_path=args.out,
    )

    print(json.dumps({
        "module_name": result["module_name"],
        "status": result["status"],
        "summary": result["summary"],
        "output_path": args.out,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
