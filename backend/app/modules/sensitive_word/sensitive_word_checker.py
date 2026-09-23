# -*- coding: utf-8 -*-
"""
敏感词检查模块
Sensitive Word Checker

用途：
    供“可研报告智能审查助手 / Windows 桌面端”调用，用于检查可研报告中
    是否存在敏感词、限制性表述、不规范用语、非信创/疑似不合规技术表达等内容。

设计原则：
    1. 单文件模块，便于与其他合作者负责的检测模块后续合并；
    2. 默认支持从 Excel 规则清单加载规则，也支持后续从规则库网站 API 加载规则；
    3. 不强制依赖大模型；规则匹配 + 场景识别可直接运行，DeepSeek 作为可选语义复核增强；
    4. 输出统一 JSON，便于 WPF 桌面端展示、后端入库和后续报告导出；
    5. 对敏感词不做“一刀切”结论，结合上下文区分拟采用、现状描述、引用说明、政策转述、需人工确认等场景。

推荐调用：
    from sensitive_word_checker import check_sensitive_words

    result = check_sensitive_words(
        report_path="demo_report.docx",
        rules_xlsx_path="规则库-敏感词检查.xlsx",
        use_llm=False,
    )

命令行调用：
    python sensitive_word_checker.py demo_report.docx \
        --rules-xlsx 规则库-敏感词检查.xlsx \
        --out sensitive_result.json

自定义敏感词：
    python sensitive_word_checker.py demo_report.docx \
        --rules-xlsx 规则库-敏感词检查.xlsx \
        --terms-file sensitive_terms.txt \
        --out sensitive_result.json

可选 DeepSeek：
    设置环境变量 DEEPSEEK_API_KEY 后加 --use-llm 即可启用语义复核。
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
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

try:
    from app.core.direct_http import urlopen_direct
except ImportError:
    _DIRECT_URL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def urlopen_direct(request: urllib.request.Request, *, timeout: int):
        return _DIRECT_URL_OPENER.open(request, timeout=timeout)


# ============================================================
# 1. 数据结构
# ============================================================

@dataclass
class SensitiveRule:
    """规则库中的敏感词检查规则。"""

    rule_id: str
    rule_name: str
    rule_category: str = "敏感词检查"
    rule_detail: str = ""
    judgement_condition: str = ""
    tags: List[str] = field(default_factory=list)
    city_judgement: str = ""
    district_judgement: str = ""
    source: str = "local"
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TextSegment:
    """从报告中解析出的文本片段。"""

    segment_id: str
    section: str
    text: str
    source_type: str = "paragraph"  # paragraph / table / sheet
    page_no: Optional[int] = None
    paragraph_index: int = 0


@dataclass
class SensitiveTerm:
    """敏感词、同义词、模式或技术实体。"""

    term: str
    aliases: List[str] = field(default_factory=list)
    category: str = "generic_sensitive"
    default_risk: str = "需人工确认"
    suggestion: str = "请结合上下文核实该表述是否符合审查要求，必要时修改为规范表述。"
    source: str = "builtin"
    matched_rule_name: str = ""
    is_regex: bool = False


@dataclass
class SensitiveFinding:
    """敏感词检查发现的问题。"""

    issue_id: str
    hit_text: str
    normalized_hit: str
    category: str
    risk_level: str
    scene_type: str
    section: str
    context: str
    paragraph_index: int
    source_type: str
    matched_rule: str
    rule_detail: str
    judgement_basis: str
    suggestion: str
    confidence: float = 0.75
    start: int = -1
    end: int = -1
    llm_review: Optional[Dict[str, Any]] = None


# ============================================================
# 2. 内置词库与判断配置
# ============================================================

# 非信创/国外产品或技术实体：默认仅做风险初筛，不作为最终合规结论。
# 实际项目可通过 Excel、规则库网站或 terms-file 继续扩充。
DEFAULT_NON_XINCHUANG_TERMS: List[SensitiveTerm] = [
    SensitiveTerm(
        term="Oracle",
        aliases=["Oracle数据库", "Oracle Database"],
        category="non_xinchuang_tech",
        default_risk="高",
        suggestion="如为拟建或采购内容，建议说明国产化替代方案或提供必要论证依据；如为现状描述，应明确迁移替代路径。",
        matched_rule_name="信创审核规则",
    ),
    SensitiveTerm(
        term="Windows Server",
        aliases=["Windows服务器", "Windows Server操作系统"],
        category="non_xinchuang_tech",
        default_risk="高",
        suggestion="如为拟建服务器操作系统，建议考虑信创操作系统或补充兼容保留论证依据。",
        matched_rule_name="信创审核规则",
    ),
    SensitiveTerm(
        term="SQL Server",
        aliases=["Microsoft SQL Server", "微软SQL Server"],
        category="non_xinchuang_tech",
        default_risk="高",
        suggestion="如为拟建数据库，建议考虑国产数据库或补充适配与替代说明。",
        matched_rule_name="信创审核规则",
    ),
    SensitiveTerm(
        term="MySQL",
        aliases=[],
        category="non_xinchuang_tech",
        default_risk="需人工确认",
        suggestion="需结合项目国产化要求、部署环境和替代说明判断是否需要调整。",
        matched_rule_name="信创审核规则",
    ),
    SensitiveTerm(
        term="VMware",
        aliases=["VMware vSphere", "ESXi"],
        category="non_xinchuang_tech",
        default_risk="需人工确认",
        suggestion="需结合虚拟化平台国产化要求判断，必要时说明替代方案。",
        matched_rule_name="信创审核规则",
    ),
    SensitiveTerm(
        term="Red Hat",
        aliases=["RHEL", "Red Hat Enterprise Linux"],
        category="non_xinchuang_tech",
        default_risk="需人工确认",
        suggestion="需结合操作系统国产化要求判断，必要时说明替代方案。",
        matched_rule_name="信创审核规则",
    ),
    SensitiveTerm(
        term="CentOS",
        aliases=[],
        category="non_xinchuang_tech",
        default_risk="需人工确认",
        suggestion="需结合服务器操作系统适配要求判断，必要时说明替代方案。",
        matched_rule_name="信创审核规则",
    ),
    SensitiveTerm(
        term="Cisco",
        aliases=["思科"],
        category="non_xinchuang_tech",
        default_risk="需人工确认",
        suggestion="如为拟采购网络设备，建议说明国产化适配情况或替代方案。",
        matched_rule_name="信创审核规则",
    ),
    SensitiveTerm(
        term="IBM",
        aliases=[],
        category="non_xinchuang_tech",
        default_risk="需人工确认",
        suggestion="如为拟采购软硬件产品，建议说明必要性、兼容性及国产化替代路径。",
        matched_rule_name="信创审核规则",
    ),
    SensitiveTerm(
        term="Microsoft",
        aliases=["微软", "Office", "Windows"],
        category="non_xinchuang_tech",
        default_risk="需人工确认",
        suggestion="需结合软件用途、授权方式及信创替代要求进行人工确认。",
        matched_rule_name="信创审核规则",
    ),
]

# 限制性/不规范表述：通常与采购公平性、品牌指定、口径不清有关。
DEFAULT_RESTRICTIVE_PATTERNS: List[SensitiveTerm] = [
    SensitiveTerm(
        term=r"(必须|应当|需|须|只|仅|唯一|指定).{0,20}(采购|采用|使用|选用).{0,20}(品牌|厂商|型号|产品)",
        category="restrictive_procurement",
        default_risk="高",
        suggestion="建议改为按功能、性能、兼容性、服务能力等客观需求描述，避免指定品牌、厂商或型号。",
        matched_rule_name="敏感词审核规则",
        is_regex=True,
    ),
    SensitiveTerm(
        term=r"(指定品牌|指定厂商|唯一供应商|独家供应|原厂唯一|不得替代|不可替代)",
        category="restrictive_procurement",
        default_risk="高",
        suggestion="建议删除限制性采购表述，改为客观技术指标或补充正式论证依据。",
        matched_rule_name="敏感词审核规则",
        is_regex=True,
    ),
    SensitiveTerm(
        term=r"(暂按内部口径|内部口径处理|特殊处理|默认通过|无需论证|无需审批|无需说明)",
        category="unclear_or_irregular_expression",
        default_risk="需人工确认",
        suggestion="建议补充正式依据、审批说明或适用范围，避免口径模糊。",
        matched_rule_name="敏感词审核规则",
        is_regex=True,
    ),
    SensitiveTerm(
        term=r"(国外品牌|进口设备|境外产品|境外服务|外资产品)",
        category="non_xinchuang_or_foreign_product",
        default_risk="需人工确认",
        suggestion="需结合项目建设要求判断是否允许，必要时说明采用依据或替代方案。",
        matched_rule_name="敏感词审核规则",
        is_regex=True,
    ),
]

LOW_RISK_CONTEXT_WORDS = [
    "现状", "原有", "已有", "历史", "存量", "兼容", "保留", "迁移", "替代", "改造",
    "引用", "引述", "摘自", "来源", "上级文件", "政策文件", "原文", "背景描述",
    "不再采用", "逐步替换", "国产化替代", "适配改造", "历史系统",
]

HIGH_RISK_CONTEXT_WORDS = [
    "拟采购", "采购", "拟采用", "采用", "选用", "部署", "建设", "新增", "购置",
    "必须", "须", "应当", "只能", "仅支持", "唯一", "指定", "不得替代",
]

POLICY_QUOTE_CONTEXT_WORDS = [
    "引用", "引述", "政策", "上级文件", "原文", "通知", "办法", "规定", "要求如下",
    "文件指出", "文件要求", "依据", "来源",
]


# ============================================================
# 3. 通用工具函数
# ============================================================

def now_str() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_text(text: str) -> str:
    """文本归一：去空白、统一大小写与常见标点。"""
    if text is None:
        return ""
    text = str(text)
    # 全角空格与常见不可见字符
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    # 全角英文数字转半角
    result = []
    for ch in text:
        code = ord(ch)
        if code == 0x3000:
            result.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            result.append(chr(code - 0xFEE0))
        else:
            result.append(ch)
    text = "".join(result)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def compact_text(text: str) -> str:
    return re.sub(r"\s+", "", normalize_text(text)).lower()


def context_window(text: str, start: int, end: int, window: int = 80) -> str:
    start_idx = max(0, start - window)
    end_idx = min(len(text), end + window)
    return text[start_idx:end_idx].strip()


def split_tags(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw_items = value
    else:
        raw = str(value)
        for sep in [",", "，", "、", ";", "；", "\n", "\t"]:
            raw = raw.replace(sep, "；")
        raw_items = raw.split("；")
    result, seen = [], set()
    for item in raw_items:
        tag = safe_str(item)
        if tag and tag not in seen:
            result.append(tag)
            seen.add(tag)
    return result


def risk_order(risk: str) -> int:
    order = {
        "高": 4,
        "高风险": 4,
        "中": 3,
        "中风险": 3,
        "需确认": 2,
        "需人工确认": 2,
        "低": 1,
        "低风险": 1,
    }
    return order.get(risk, 2)


def normalize_risk(risk: str) -> str:
    risk = safe_str(risk)
    if risk in ["高风险", "严重", "重大"]:
        return "高"
    if risk in ["中风险", "一般"]:
        return "中"
    if risk in ["需确认", "人工确认", "需人工复核"]:
        return "需人工确认"
    if risk in ["低风险"]:
        return "低"
    return risk or "需人工确认"


def extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """从大模型返回内容中提取 JSON 对象。"""
    if not text:
        return None
    text = text.strip()
    # 直接 JSON
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    # markdown code block
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        try:
            obj = json.loads(m.group(1).strip())
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    # 粗略截取
    first = text.find("{")
    last = text.rfind("}")
    if first >= 0 and last > first:
        try:
            obj = json.loads(text[first:last + 1])
            if isinstance(obj, dict):
                return obj
        except Exception:
            return None
    return None


# ============================================================
# 4. 规则加载：Excel / 规则库 API / 自定义词表
# ============================================================

def load_rules_from_xlsx(xlsx_path: Optional[str]) -> List[SensitiveRule]:
    """从 Excel 读取敏感词检查规则。

    兼容当前上传的规则清单格式：
        第一行：规则类别、规则名称、规则描述、判断条件、规则标签等；
        后续行：敏感词检测 / 内容合规性审查规则 / 信创审核规则 ...
    """
    if not xlsx_path:
        return []
    path = Path(xlsx_path)
    if not path.exists():
        raise FileNotFoundError(f"规则 Excel 不存在：{xlsx_path}")

    try:
        import openpyxl
    except Exception as exc:
        raise RuntimeError("读取 xlsx 需要安装 openpyxl：pip install openpyxl") from exc

    wb = openpyxl.load_workbook(path, data_only=True)
    rules: List[SensitiveRule] = []

    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue

        # 找表头行：优先包含“规则名称/规则描述/判断条件”的行
        header_idx = None
        for i, row in enumerate(rows[:10]):
            names = [safe_str(x) for x in row]
            if any("规则名称" in x for x in names) and any(("规则描述" in x or "规则详情" in x) for x in names):
                header_idx = i
                break
        if header_idx is None:
            continue

        headers = [safe_str(x) or f"未命名列{j}" for j, x in enumerate(rows[header_idx])]
        for ridx, row in enumerate(rows[header_idx + 1:], start=header_idx + 2):
            if not row or not any(safe_str(x) for x in row):
                continue
            row_map = {headers[i]: row[i] if i < len(row) else "" for i in range(len(headers))}
            raw = {k: safe_str(v) for k, v in row_map.items()}

            # 当前表第一列可能没有表头，但内容为“敏感词检测”
            first_cell = safe_str(row[0]) if len(row) > 0 else ""
            category = (
                raw.get("规则类别")
                or raw.get("类别")
                or raw.get("规则分类")
                or first_cell
                or "敏感词检查"
            )
            name = raw.get("规则名称") or raw.get("名称") or raw.get("规则标题") or f"敏感词规则-{ridx}"
            detail = raw.get("规则描述") or raw.get("规则详情") or raw.get("规则内容") or ""
            condition = raw.get("判断条件") or raw.get("判断标准") or raw.get("触发条件") or ""
            tags = split_tags(raw.get("规则标签") or raw.get("标签") or "")

            rules.append(SensitiveRule(
                rule_id=f"{path.stem}-{ws.title}-{ridx}",
                rule_name=name,
                rule_category=category,
                rule_detail=detail,
                judgement_condition=condition,
                tags=tags,
                source=f"excel:{path.name}",
                raw=raw,
            ))

    return rules


def load_rules_from_api(
    api_base_url: Optional[str],
    tag: Optional[str] = None,
    keyword: Optional[str] = "敏感词",
    timeout: int = 8,
) -> List[SensitiveRule]:
    """从规则库网站 API 加载规则。

    兼容当前规则库接口：
        /api/rule-library/list?page=1&page_size=200&tag=...&keyword=...
    api_base_url 可传：
        https://decree-tapering-that.ngrok-free.dev
        https://decree-tapering-that.ngrok-free.dev/api/rule-library/list
    """
    if not api_base_url:
        return []

    url = api_base_url.strip().rstrip("/")
    if not url:
        return []

    if "/api/rule-library/list" not in url:
        url = url + "/api/rule-library/list"

    params = {
        "page": "1",
        "page_size": "200",
    }
    if tag:
        params["tag"] = tag
    if keyword:
        params["keyword"] = keyword

    full_url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        full_url,
        headers={
            "User-Agent": "AIassist-sensitive-word-checker/1.0",
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []

    data = payload.get("data", payload)
    items = data.get("items", data if isinstance(data, list) else [])
    rules: List[SensitiveRule] = []

    for i, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        status = safe_str(item.get("status"))
        # 只使用已通过规则；如果 API 没有 status 字段，则保留
        if status and status not in ["已通过", "approved"]:
            continue
        rules.append(SensitiveRule(
            rule_id=safe_str(item.get("id") or item.get("rule_id") or f"api-{i}"),
            rule_name=safe_str(item.get("rule_name") or item.get("name") or f"敏感词规则-{i}"),
            rule_category=safe_str(item.get("rule_category") or item.get("category") or "敏感词检查"),
            rule_detail=safe_str(item.get("rule_detail") or item.get("rule_condition") or item.get("content")),
            judgement_condition=safe_str(item.get("judgement_condition") or item.get("rule_basis") or ""),
            city_judgement=safe_str(item.get("city_judgement")),
            district_judgement=safe_str(item.get("district_judgement")),
            tags=split_tags(item.get("tags")),
            source="api",
            raw=item,
        ))

    return rules


def load_custom_terms(terms_file: Optional[str]) -> List[SensitiveTerm]:
    """从自定义文件加载敏感词。

    支持：
      1. txt：每行一个词，可写为：词 或 词|类别|风险|建议
      2. json：列表，每项为字符串或对象 {term, aliases, category, risk/default_risk, suggestion}
      3. csv：列名支持 term, aliases, category, risk, suggestion
    """
    if not terms_file:
        return []
    path = Path(terms_file)
    if not path.exists():
        raise FileNotFoundError(f"敏感词文件不存在：{terms_file}")

    ext = path.suffix.lower()
    terms: List[SensitiveTerm] = []

    if ext == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("items") or data.get("terms") or []
        for item in data:
            if isinstance(item, str):
                terms.append(SensitiveTerm(term=item, source=f"file:{path.name}"))
            elif isinstance(item, dict):
                terms.append(SensitiveTerm(
                    term=safe_str(item.get("term") or item.get("word") or item.get("name")),
                    aliases=split_tags(item.get("aliases") or item.get("alias")),
                    category=safe_str(item.get("category") or "custom_sensitive"),
                    default_risk=normalize_risk(safe_str(item.get("risk") or item.get("default_risk") or "需人工确认")),
                    suggestion=safe_str(item.get("suggestion") or "请结合上下文进行人工复核。"),
                    source=f"file:{path.name}",
                    matched_rule_name=safe_str(item.get("matched_rule_name") or item.get("rule_name")),
                    is_regex=bool(item.get("is_regex")),
                ))
        return [x for x in terms if x.term]

    if ext == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                terms.append(SensitiveTerm(
                    term=safe_str(row.get("term") or row.get("word") or row.get("敏感词")),
                    aliases=split_tags(row.get("aliases") or row.get("同义词") or ""),
                    category=safe_str(row.get("category") or row.get("类别") or "custom_sensitive"),
                    default_risk=normalize_risk(safe_str(row.get("risk") or row.get("风险等级") or "需人工确认")),
                    suggestion=safe_str(row.get("suggestion") or row.get("建议") or "请结合上下文进行人工复核。"),
                    source=f"file:{path.name}",
                    matched_rule_name=safe_str(row.get("rule_name") or row.get("规则名称")),
                    is_regex=safe_str(row.get("is_regex")).lower() in ["1", "true", "yes", "是"],
                ))
        return [x for x in terms if x.term]

    # txt fallback
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [x.strip() for x in line.split("|")]
        term = parts[0]
        category = parts[1] if len(parts) > 1 and parts[1] else "custom_sensitive"
        risk = parts[2] if len(parts) > 2 and parts[2] else "需人工确认"
        suggestion = parts[3] if len(parts) > 3 and parts[3] else "请结合上下文进行人工复核。"
        terms.append(SensitiveTerm(
            term=term,
            category=category,
            default_risk=normalize_risk(risk),
            suggestion=suggestion,
            source=f"file:{path.name}",
            matched_rule_name="敏感词审核规则",
        ))

    return terms


def build_terms_from_rules(rules: List[SensitiveRule]) -> List[SensitiveTerm]:
    """根据规则描述生成检测项。

    当前 Excel 规则偏“规则描述/判断条件”，未逐条列出具体敏感词，
    因此这里根据规则名称与判断条件启用对应内置词库。
    后续如果规则详情中直接写了敏感词，也会尝试抽取。
    """
    terms: List[SensitiveTerm] = []
    joined = "\n".join([
        f"{r.rule_name} {r.rule_detail} {r.judgement_condition} {' '.join(r.tags)}"
        for r in rules
    ])

    if not rules:
        # 没有规则时仍可运行基础检查
        return DEFAULT_NON_XINCHUANG_TERMS + DEFAULT_RESTRICTIVE_PATTERNS

    if any(x in joined for x in ["信创", "非信创", "国产化", "信息技术应用创新"]):
        terms.extend(DEFAULT_NON_XINCHUANG_TERMS)

    if any(x in joined for x in ["敏感词", "厂商", "限制性", "不规范", "品牌", "敏感"]):
        terms.extend(DEFAULT_RESTRICTIVE_PATTERNS)

    # 兼容规则详情中出现“敏感词：A、B、C”或“关键词：A、B”
    extracted = []
    for rule in rules:
        text = f"{rule.rule_detail}\n{rule.judgement_condition}"
        patterns = [
            r"(?:敏感词|关键词|词条|命中词|检查词)\s*[:：]\s*([^\n。；;]+)",
            r"(?:包括|如|例如)\s*[:：]?\s*([A-Za-z0-9_\- /.，、,；;]{2,80})",
        ]
        for pat in patterns:
            for m in re.finditer(pat, text):
                raw = m.group(1)
                for term in split_tags(raw):
                    if 1 < len(term) <= 40 and not re.search(r"是否|出现|全文|项目|报告|规则|检查", term):
                        extracted.append(SensitiveTerm(
                            term=term,
                            category="rule_extracted_sensitive",
                            default_risk="需人工确认",
                            suggestion="该词来自规则描述，需结合上下文判断是否需要修改。",
                            source=rule.source,
                            matched_rule_name=rule.rule_name,
                        ))

    terms.extend(extracted)
    return deduplicate_terms(terms)


def deduplicate_terms(terms: List[SensitiveTerm]) -> List[SensitiveTerm]:
    result: List[SensitiveTerm] = []
    seen = set()
    for term in terms:
        key = (term.term.lower(), term.category, term.is_regex)
        if not term.term or key in seen:
            continue
        seen.add(key)
        result.append(term)
    return result


# ============================================================
# 5. 报告解析：docx / txt / xlsx
# ============================================================

def extract_docx_text(path: Path) -> List[TextSegment]:
    """使用 zip+xml 解析 docx，避免强依赖 python-docx。"""
    segments: List[TextSegment] = []
    ns = {
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    }

    with zipfile.ZipFile(path) as z:
        # 正文段落
        if "word/document.xml" in z.namelist():
            root = ET.fromstring(z.read("word/document.xml"))
            paragraphs = root.findall(".//w:p", ns)
            current_section = "全文"
            idx = 0
            for p in paragraphs:
                texts = [t.text or "" for t in p.findall(".//w:t", ns)]
                text = normalize_text("".join(texts))
                if not text:
                    continue
                if is_heading(text):
                    current_section = clean_heading(text)
                idx += 1
                segments.append(TextSegment(
                    segment_id=f"p-{idx}",
                    section=current_section,
                    text=text,
                    source_type="paragraph",
                    paragraph_index=idx,
                ))

            # 表格
            table_idx = 0
            for tbl in root.findall(".//w:tbl", ns):
                table_idx += 1
                rows_text = []
                for tr in tbl.findall(".//w:tr", ns):
                    cells = []
                    for tc in tr.findall(".//w:tc", ns):
                        cell_texts = [t.text or "" for t in tc.findall(".//w:t", ns)]
                        cell = normalize_text("".join(cell_texts))
                        if cell:
                            cells.append(cell)
                    if cells:
                        rows_text.append(" | ".join(cells))
                if rows_text:
                    text = "\n".join(rows_text)
                    segments.append(TextSegment(
                        segment_id=f"table-{table_idx}",
                        section=current_section,
                        text=text,
                        source_type="table",
                        paragraph_index=100000 + table_idx,
                    ))
    return segments


def extract_txt_text(path: Path) -> List[TextSegment]:
    content = path.read_text(encoding="utf-8", errors="ignore")
    segments = []
    current_section = "全文"
    for idx, line in enumerate(content.splitlines(), start=1):
        line = normalize_text(line)
        if not line:
            continue
        if is_heading(line):
            current_section = clean_heading(line)
        segments.append(TextSegment(
            segment_id=f"line-{idx}",
            section=current_section,
            text=line,
            source_type="paragraph",
            paragraph_index=idx,
        ))
    return segments


def extract_xlsx_text(path: Path) -> List[TextSegment]:
    try:
        import openpyxl
    except Exception as exc:
        raise RuntimeError("读取 xlsx 需要安装 openpyxl：pip install openpyxl") from exc

    wb = openpyxl.load_workbook(path, data_only=True)
    segments: List[TextSegment] = []
    idx = 0
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            cells = [safe_str(x) for x in row if safe_str(x)]
            if not cells:
                continue
            idx += 1
            segments.append(TextSegment(
                segment_id=f"{ws.title}-{idx}",
                section=ws.title,
                text=" | ".join(cells),
                source_type="sheet",
                paragraph_index=idx,
            ))
    return segments


def parse_report(report_path: str) -> List[TextSegment]:
    path = Path(report_path)
    if not path.exists():
        raise FileNotFoundError(f"报告文件不存在：{report_path}")

    ext = path.suffix.lower()
    if ext == ".docx":
        segments = extract_docx_text(path)
    elif ext in [".txt", ".md"]:
        segments = extract_txt_text(path)
    elif ext in [".xlsx", ".xlsm"]:
        segments = extract_xlsx_text(path)
    else:
        raise ValueError(f"暂不支持的文件格式：{ext}，建议使用 docx/txt/xlsx")

    # 如果章节都没识别出来，做一次章节传播
    return normalize_sections(segments)


def is_heading(text: str) -> bool:
    text = normalize_text(text)
    if not text or len(text) > 80:
        return False
    patterns = [
        r"^第[一二三四五六七八九十百\d]+[章节篇部分]",
        r"^[一二三四五六七八九十]+[、．.]\s*",
        r"^\d+(\.\d+){0,3}\s+",
        r"^\d+[、．.]\s*",
        r"^(项目概述|建设背景|建设内容|技术路线|系统架构|软硬件|设备配置|投资估算|预算|附件|安全|方案|需求分析|总体设计)",
    ]
    return any(re.search(p, text) for p in patterns)


def clean_heading(text: str) -> str:
    text = normalize_text(text)
    return re.sub(r"\s+", " ", text)[:80]


def normalize_sections(segments: List[TextSegment]) -> List[TextSegment]:
    current = "全文"
    for seg in segments:
        if is_heading(seg.text):
            current = clean_heading(seg.text)
            seg.section = current
        elif not seg.section or seg.section == "全文":
            seg.section = current
    return segments


# ============================================================
# 6. 敏感词匹配与语义场景识别
# ============================================================

def match_sensitive_terms(
    segments: List[TextSegment],
    terms: List[SensitiveTerm],
    rules: List[SensitiveRule],
    min_context_len: int = 8,
) -> List[SensitiveFinding]:
    findings: List[SensitiveFinding] = []
    rule_map = {r.rule_name: r for r in rules}

    issue_counter = 0
    for seg in segments:
        original = seg.text
        if not original or len(original.strip()) < min_context_len:
            continue

        normalized_original = normalize_text(original)
        compact_original = compact_text(normalized_original)

        for term in terms:
            matches: List[Tuple[str, int, int]] = []

            if term.is_regex:
                try:
                    for m in re.finditer(term.term, normalized_original, flags=re.IGNORECASE):
                        matches.append((m.group(0), m.start(), m.end()))
                except re.error:
                    continue
            else:
                all_names = [term.term] + list(term.aliases or [])
                for name in all_names:
                    if not name:
                        continue
                    # 英文/产品名：大小写不敏感；中文：正常匹配
                    if re.search(r"[A-Za-z]", name):
                        pattern = re.escape(name)
                        for m in re.finditer(pattern, normalized_original, flags=re.IGNORECASE):
                            matches.append((m.group(0), m.start(), m.end()))
                    else:
                        start = 0
                        while True:
                            idx = normalized_original.find(name, start)
                            if idx < 0:
                                break
                            matches.append((name, idx, idx + len(name)))
                            start = idx + max(len(name), 1)

                    # 处理去空白匹配，比如 "Windows  Server"
                    cname = compact_text(name)
                    if cname and cname in compact_original and not any(x[0] == name for x in matches):
                        # 位置无法精确映射时用整段上下文
                        matches.append((name, 0, min(len(normalized_original), len(name))))

            for hit, start, end in matches:
                if is_false_positive(hit, normalized_original):
                    continue

                context = context_window(normalized_original, start, end, 90)
                scene = classify_scene(hit, context, seg.section)
                risk = determine_risk(term, scene, context)
                matched_rule = select_matched_rule(term, rules)

                issue_counter += 1
                rule_detail = matched_rule.rule_detail if matched_rule else ""
                rule_name = matched_rule.rule_name if matched_rule else (term.matched_rule_name or "敏感词审核规则")
                basis = build_judgement_basis(term, hit, scene, context, matched_rule)
                suggestion = build_suggestion(term, scene, risk)

                findings.append(SensitiveFinding(
                    issue_id=f"SENS-{issue_counter:04d}",
                    hit_text=hit,
                    normalized_hit=term.term if not term.is_regex else hit,
                    category=term.category,
                    risk_level=risk,
                    scene_type=scene,
                    section=seg.section,
                    context=context,
                    paragraph_index=seg.paragraph_index,
                    source_type=seg.source_type,
                    matched_rule=rule_name,
                    rule_detail=rule_detail,
                    judgement_basis=basis,
                    suggestion=suggestion,
                    confidence=estimate_confidence(term, scene, risk, context),
                    start=start,
                    end=end,
                ))

    findings = merge_duplicate_findings(findings)
    findings.sort(key=lambda x: (-risk_order(x.risk_level), x.paragraph_index, x.hit_text))
    return findings


def is_false_positive(hit: str, context: str) -> bool:
    """简单误报过滤。"""
    h = safe_str(hit).lower()
    c = safe_str(context).lower()
    if not h:
        return True
    # MySQL 在“国产数据库兼容 MySQL 协议”场景下通常需确认或低风险，不直接过滤
    # 避免太短英文误判
    if re.fullmatch(r"[a-zA-Z]{1,2}", h):
        return True
    # 如为代码片段/URL 中的一部分，仍保留但可降置信，不在这里删除
    return False


def classify_scene(hit: str, context: str, section: str = "") -> str:
    context_norm = normalize_text(context)
    merged = f"{section} {context_norm}"

    if any(w in merged for w in POLICY_QUOTE_CONTEXT_WORDS):
        if any(w in merged for w in ["引用", "上级文件", "原文", "政策转述", "文件要求", "文件指出"]):
            return "政策转述/引用说明"

    if any(w in merged for w in ["现状", "原有", "已有", "历史系统", "存量系统"]):
        return "现状描述"

    if any(w in merged for w in ["兼容", "保留", "迁移", "替代", "改造", "国产化替代", "逐步替换"]):
        return "兼容保留/替代说明"

    if any(w in merged for w in ["拟采购", "采购", "购置", "拟采用", "采用", "选用", "部署", "新增", "建设"]):
        return "拟采用使用"

    if any(w in merged for w in ["必须", "指定", "唯一", "仅支持", "只能", "不得替代"]):
        return "拟采用使用"

    return "需人工确认"


def determine_risk(term: SensitiveTerm, scene: str, context: str) -> str:
    base = normalize_risk(term.default_risk)
    c = normalize_text(context)

    # 引用/政策转述类：默认低或需人工确认，不直接判高
    if scene == "政策转述/引用说明":
        return "低"

    # 现状/兼容保留类：非信创项一般降低为需确认或低
    if scene in ["现状描述", "兼容保留/替代说明"]:
        if term.category in ["non_xinchuang_tech", "non_xinchuang_or_foreign_product"]:
            if any(w in c for w in ["替代", "迁移", "不再采用", "逐步替换", "国产化替代"]):
                return "低"
            return "需人工确认"
        if base == "高":
            return "需人工确认"
        return base

    # 拟采用 + 非信创/限制性表达：提高风险
    if scene == "拟采用使用":
        if term.category in ["non_xinchuang_tech", "restrictive_procurement", "non_xinchuang_or_foreign_product"]:
            return "高"
        return base

    # 需人工确认场景
    if base == "高" and not any(w in c for w in HIGH_RISK_CONTEXT_WORDS):
        return "需人工确认"

    return base


def select_matched_rule(term: SensitiveTerm, rules: List[SensitiveRule]) -> Optional[SensitiveRule]:
    if not rules:
        return None

    # 先按内置规则名匹配
    if term.matched_rule_name:
        for rule in rules:
            if term.matched_rule_name in rule.rule_name or rule.rule_name in term.matched_rule_name:
                return rule

    text = f"{term.term} {term.category}"
    if "non_xinchuang" in term.category or any(x in text for x in ["信创", "Oracle", "Windows"]):
        for rule in rules:
            joined = f"{rule.rule_name} {rule.rule_detail} {rule.judgement_condition}"
            if any(x in joined for x in ["信创", "非信创", "国产化"]):
                return rule

    if "restrictive" in term.category or "sensitive" in term.category or "敏感" in text:
        for rule in rules:
            joined = f"{rule.rule_name} {rule.rule_detail} {rule.judgement_condition}"
            if any(x in joined for x in ["敏感", "厂商", "品牌", "限制"]):
                return rule

    return rules[0]


def build_judgement_basis(
    term: SensitiveTerm,
    hit: str,
    scene: str,
    context: str,
    rule: Optional[SensitiveRule],
) -> str:
    pieces = [f"命中“{hit}”，场景判断为“{scene}”。"]
    if rule:
        pieces.append(f"依据规则“{rule.rule_name}”：{rule.rule_detail or rule.judgement_condition}")
    if term.category == "restrictive_procurement":
        pieces.append("该表述可能形成品牌、厂商或型号限制，需要重点复核。")
    elif term.category == "non_xinchuang_tech":
        pieces.append("该技术/产品属于信创符合性需关注对象，应结合拟建或现状语境判断。")
    else:
        pieces.append("该表述属于规则库或自定义词库需关注内容。")
    return " ".join([x for x in pieces if x])


def build_suggestion(term: SensitiveTerm, scene: str, risk: str) -> str:
    if risk == "低":
        if scene == "政策转述/引用说明":
            return "建议保留引用并注明来源；如表述容易误解，可补充解释说明。"
        if scene in ["现状描述", "兼容保留/替代说明"]:
            return "建议明确该内容属于现状、兼容或替代说明，避免被误认为拟建采用。"

    if scene == "拟采用使用" and term.category == "restrictive_procurement":
        return "建议改为按功能、性能、服务能力、兼容性等客观需求描述，避免指定品牌、厂商或型号。"

    if scene == "拟采用使用" and term.category == "non_xinchuang_tech":
        return "建议核实是否符合信创要求；如确需采用，应补充适配说明、论证依据或国产化替代路径。"

    return term.suggestion or "请结合上下文核实该表述是否符合审查要求，必要时修改为规范表述。"


def estimate_confidence(term: SensitiveTerm, scene: str, risk: str, context: str) -> float:
    confidence = 0.72
    if term.is_regex:
        confidence += 0.08
    if scene in ["拟采用使用", "政策转述/引用说明", "现状描述", "兼容保留/替代说明"]:
        confidence += 0.08
    if risk in ["高", "低"]:
        confidence += 0.04
    if len(context) > 50:
        confidence += 0.03
    return round(min(confidence, 0.95), 2)


def merge_duplicate_findings(findings: List[SensitiveFinding]) -> List[SensitiveFinding]:
    """合并重复命中：同段落、同命中词、同上下文视为重复。"""
    result = []
    seen = {}
    for f in findings:
        key = (
            f.paragraph_index,
            f.normalized_hit.lower(),
            f.section,
            compact_text(f.context)[:80],
        )
        if key not in seen:
            seen[key] = f
            result.append(f)
        else:
            old = seen[key]
            # 保留更高风险
            if risk_order(f.risk_level) > risk_order(old.risk_level):
                seen[key] = f
    # 重建 issue_id
    for idx, f in enumerate(result, start=1):
        f.issue_id = f"SENS-{idx:04d}"
    return result


# ============================================================
# 7. DeepSeek 语义复核（可选）
# ============================================================

def deepseek_review_finding(
    finding: SensitiveFinding,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    api_url: Optional[str] = None,
    timeout: int = 20,
) -> Optional[Dict[str, Any]]:
    api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        return None
    model = model or os.getenv("DEEPSEEK_MODEL", "DeepSeek-V4-Flash")
    api_url = api_url or (
        os.getenv("DEEPSEEK_API_BASE_URL")
        or os.getenv("DEEPSEEK_API_URL")
        or "https://api.deepseek.com/v1"
    ).rstrip("/") + "/chat/completions"

    system_prompt = (
        "你是可研报告审查辅助系统中的敏感词检查模块。"
        "请根据命中内容、上下文和规则，判断该敏感表达属于拟采用使用、现状描述、引用说明、政策转述或需人工确认。"
        "只返回 JSON，不要输出多余文字。"
    )
    user_prompt = {
        "命中内容": finding.hit_text,
        "所在章节": finding.section,
        "上下文": finding.context,
        "初步风险等级": finding.risk_level,
        "初步场景": finding.scene_type,
        "命中规则": finding.matched_rule,
        "规则详情": finding.rule_detail,
        "要求输出字段": {
            "scene_type": "拟采用使用/现状描述/引用说明/政策转述/需人工确认",
            "risk_level": "高/中/需人工确认/低",
            "reason": "判断理由",
            "suggestion": "修改建议",
            "confidence": "0到1之间的小数",
        },
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_prompt, ensure_ascii=False)},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }

    req = urllib.request.Request(
        api_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )

    try:
        with urlopen_direct(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        return extract_json_object(content)
    except Exception as exc:
        return {
            "error": f"DeepSeek 复核失败：{exc}",
        }


def apply_llm_review(findings: List[SensitiveFinding], use_llm: bool = False, max_items: int = 15) -> List[SensitiveFinding]:
    if not use_llm:
        return findings

    # 为控制成本，只复核高风险和需人工确认的前若干条
    candidates = [f for f in findings if f.risk_level in ["高", "需人工确认"]][:max_items]

    for f in candidates:
        review = deepseek_review_finding(f)
        if not review or review.get("error"):
            f.llm_review = review
            continue

        f.llm_review = review
        if review.get("scene_type"):
            f.scene_type = safe_str(review.get("scene_type"))
        if review.get("risk_level"):
            f.risk_level = normalize_risk(safe_str(review.get("risk_level")))
        if review.get("reason"):
            f.judgement_basis = safe_str(review.get("reason"))
        if review.get("suggestion"):
            f.suggestion = safe_str(review.get("suggestion"))
        try:
            f.confidence = round(float(review.get("confidence", f.confidence)), 2)
        except Exception:
            pass

    findings.sort(key=lambda x: (-risk_order(x.risk_level), x.paragraph_index, x.hit_text))
    return findings


# ============================================================
# 8. 汇总与主入口
# ============================================================

def build_summary(findings: List[SensitiveFinding], segments: List[TextSegment], rules: List[SensitiveRule]) -> Dict[str, Any]:
    risk_counter = Counter(f.risk_level for f in findings)
    category_counter = Counter(f.category for f in findings)
    scene_counter = Counter(f.scene_type for f in findings)

    return {
        "total_findings": len(findings),
        "high_count": risk_counter.get("高", 0),
        "medium_count": risk_counter.get("中", 0),
        "need_confirm_count": risk_counter.get("需人工确认", 0) + risk_counter.get("需确认", 0),
        "low_count": risk_counter.get("低", 0),
        "checked_segments": len(segments),
        "loaded_rules": len(rules),
        "risk_distribution": dict(risk_counter),
        "category_distribution": dict(category_counter),
        "scene_distribution": dict(scene_counter),
    }


def finding_to_dict(f: SensitiveFinding) -> Dict[str, Any]:
    d = asdict(f)
    return d


def check_sensitive_words(
    report_path: str,
    rules_xlsx_path: Optional[str] = None,
    rule_api_url: Optional[str] = None,
    rule_api_tag: Optional[str] = None,
    rule_api_keyword: Optional[str] = "敏感词",
    terms_file: Optional[str] = None,
    custom_terms: Optional[Sequence[str]] = None,
    use_llm: bool = False,
    llm_max_items: int = 15,
    project_level: str = "通用",
    selected_rule_ids: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """敏感词检查主函数。

    参数：
        report_path: 可研报告文件路径，支持 docx/txt/xlsx。
        rules_xlsx_path: 敏感词检查规则 Excel。
        rule_api_url: 规则库网站 API 根地址或 /api/rule-library/list 地址。
        rule_api_tag: 从规则库网站按标签筛选。
        rule_api_keyword: 从规则库网站按关键词筛选，默认“敏感词”。
        terms_file: 自定义敏感词文件，支持 txt/json/csv。
        custom_terms: 运行时传入的敏感词列表。
        use_llm: 是否启用 DeepSeek 语义复核。
        llm_max_items: 最多复核的问题数。
        project_level: 项目层级，预留字段，便于市级/区级规则分流。

    返回：
        dict，可直接序列化为 JSON。
    """
    started = time.time()
    report_path_obj = Path(report_path)

    rules: List[SensitiveRule] = []
    if rules_xlsx_path:
        rules.extend(load_rules_from_xlsx(rules_xlsx_path))
    if rule_api_url:
        # API 规则作为补充；如果网络不可用，不影响本地 Excel 规则检查
        api_rules = load_rules_from_api(rule_api_url, tag=rule_api_tag, keyword=rule_api_keyword)
        # 去重
        existing = {(r.rule_name, r.rule_detail) for r in rules}
        for r in api_rules:
            if (r.rule_name, r.rule_detail) not in existing:
                rules.append(r)
                existing.add((r.rule_name, r.rule_detail))

    if selected_rule_ids:
        selected = {str(rule_id).strip() for rule_id in selected_rule_ids if str(rule_id).strip()}
        filtered_rules = [rule for rule in rules if rule.rule_id in selected]
        if filtered_rules:
            rules = filtered_rules

    terms = build_terms_from_rules(rules)
    terms.extend(load_custom_terms(terms_file))

    if custom_terms:
        for item in custom_terms:
            terms.append(SensitiveTerm(
                term=safe_str(item),
                category="custom_sensitive",
                default_risk="需人工确认",
                suggestion="该词为调用方传入的自定义敏感词，请结合上下文人工确认。",
                source="runtime",
                matched_rule_name="敏感词审核规则",
            ))

    terms = deduplicate_terms(terms)

    segments = parse_report(report_path)
    findings = match_sensitive_terms(segments, terms, rules)
    findings = apply_llm_review(findings, use_llm=use_llm, max_items=llm_max_items)

    summary = build_summary(findings, segments, rules)

    result = {
        "success": True,
        "module": "sensitive_word_check",
        "module_name": "敏感词检查",
        "project_level": project_level,
        "report_path": str(report_path_obj),
        "checked_at": now_str(),
        "elapsed_seconds": round(time.time() - started, 3),
        "summary": summary,
        "rules": [asdict(r) for r in rules],
        "terms": [
            {
                "term": t.term,
                "aliases": t.aliases,
                "category": t.category,
                "default_risk": t.default_risk,
                "source": t.source,
                "matched_rule_name": t.matched_rule_name,
                "is_regex": t.is_regex,
            }
            for t in terms
        ],
        "findings": [finding_to_dict(f) for f in findings],
        "sensitive_content_list": [
            {
                "命中内容": f.hit_text,
                "风险等级": f.risk_level,
                "所在章节": f.section,
                "语义场景": f.scene_type,
                "判断依据": f.judgement_basis,
                "修改建议": f.suggestion,
                "上下文片段": f.context,
            }
            for f in findings
        ],
    }
    return result


# 兼容不同命名习惯，便于其他同学集成
run_sensitive_word_check = check_sensitive_words
check_sensitive_word = check_sensitive_words


def save_json(result: Dict[str, Any], out_path: str) -> None:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def cli_main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="敏感词检查模块：检查可研报告中的敏感词、限制性表述和需关注内容。")
    parser.add_argument("report_path", help="可研报告路径，支持 docx/txt/xlsx")
    parser.add_argument("--rules-xlsx", default=None, help="敏感词检查规则 Excel 路径")
    parser.add_argument("--rule-api-url", default=None, help="规则库网站地址或 /api/rule-library/list 接口地址")
    parser.add_argument("--rule-api-tag", default=None, help="规则库标签筛选")
    parser.add_argument("--rule-api-keyword", default="敏感词", help="规则库关键词筛选，默认：敏感词")
    parser.add_argument("--terms-file", default=None, help="自定义敏感词文件，支持 txt/json/csv")
    parser.add_argument("--custom-term", action="append", default=[], help="额外自定义敏感词，可多次传入")
    parser.add_argument("--project-level", default="通用", help="项目层级，如 市级项目/区级项目/通用")
    parser.add_argument("--use-llm", action="store_true", help="启用 DeepSeek 语义复核，需要 DEEPSEEK_API_KEY")
    parser.add_argument("--llm-max-items", type=int, default=15, help="最多使用大模型复核的问题数量")
    parser.add_argument("--out", default=None, help="结果 JSON 输出路径")
    args = parser.parse_args(argv)

    result = check_sensitive_words(
        report_path=args.report_path,
        rules_xlsx_path=args.rules_xlsx,
        rule_api_url=args.rule_api_url,
        rule_api_tag=args.rule_api_tag,
        rule_api_keyword=args.rule_api_keyword,
        terms_file=args.terms_file,
        custom_terms=args.custom_term,
        use_llm=args.use_llm,
        llm_max_items=args.llm_max_items,
        project_level=args.project_level,
    )

    if args.out:
        save_json(result, args.out)
        print(f"敏感词检查完成，结果已写入：{args.out}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(cli_main())
