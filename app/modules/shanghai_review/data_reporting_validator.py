# -*- coding: utf-8 -*-
"""数据填报合理性审查。

合并以下 2 项内容合规性审查规则：
  1. 项目成效考核目标与建设内容的匹配性审查规则
  2. 数据上链内容合规性审查规则
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, List, Set

from app.modules.shanghai_review.base import (
    BaseValidator,
    ErrorCode,
    ValidationError,
    ValidationResult,
)


_BENEFIT_KEYWORDS: FrozenSet[str] = frozenset(
    {
        "效益",
        "提升",
        "降低",
        "节约",
        "减少",
        "缩短",
        "增长",
        "增加",
        "改善",
        "优化",
        "覆盖",
        "共享",
        "调用",
        "用户",
        "使用",
        "服务",
        "效率",
        "满意度",
        "投诉",
        "成本",
        "收入",
        "资金",
        "编码",
        "访问",
        "支撑",
        "运行",
        "标准化",
    }
)

_OUTPUT_KEYWORDS: FrozenSet[str] = frozenset(
    {
        "完成率",
        "数量",
        "个数",
        "购置",
        "开发",
        "测试",
        "通过",
        "部署",
        "安装",
        "交付",
        "构建",
        "实施",
        "建设",
    }
)

_AI_KEYWORDS: FrozenSet[str] = frozenset(
    {
        "智能化",
        "智能",
        "AI",
        "人工智能",
        "模型",
        "算法",
        "机器学习",
        "深度学习",
        "神经网络",
        "NLP",
        "计算机视觉",
        "语音识别",
        "自然语言",
        "知识图谱",
        "推荐系统",
        "预测",
        "决策支持",
        "自动化",
        "机器人",
    }
)

_EFFECTIVENESS_KEYWORDS: FrozenSet[str] = frozenset(
    {
        "准确率",
        "精确率",
        "召回率",
        "F1",
        "响应时间",
        "延迟",
        "吞吐量",
        "并发",
        "QPS",
        "TPS",
        "错误率",
        "误报率",
        "漏报率",
        "识别率",
        "正确率",
        "精度",
        "推理速度",
        "训练时间",
        "模型精度",
        "预测准确",
        "分类准确",
    }
)

_DATA_GOVERNANCE_SERVICES: Dict[str, FrozenSet[str]] = {
    "数据采集类": frozenset({"数据购买", "历史数据迁移"}),
    "数据加工类": frozenset({"数据标签", "数据融合"}),
    "数据管理类": frozenset({"历史数据归档/销毁"}),
    "数据安全与治理类": frozenset({"数据安全与治理"}),
}

_SERVICE_BLOCKCHAIN_MAP: Dict[str, FrozenSet[str]] = {
    "数据购买": frozenset({"购买数据", "外部数据", "数据目录", "数据来源", "数据资源"}),
    "历史数据迁移": frozenset({"迁移数据", "历史数据", "存量数据", "数据迁移"}),
    "数据融合": frozenset({"融合数据", "数据融合", "数据加工", "处理结果"}),
    "数据标签": frozenset({"标签数据", "标注数据", "数据标签"}),
}

_BLOCKCHAIN_NEGATIVE_LIST: FrozenSet[str] = frozenset(
    {
        "上链服务",
        "数据上链",
        "区块链服务",
        "链上服务",
        "政务链接入",
        "目录链接入",
        "数据确权服务",
    }
)


@dataclass(frozen=True)
class ClassifiedIndicator:
    name: str
    value: str
    category: str
    sub_category: str
    is_benefit: bool = False
    is_output: bool = False
    is_effectiveness: bool = False


@dataclass(frozen=True)
class DataGovernanceFinding:
    service_name: str
    category: str
    section: str
    evidence: str = ""


def _to_text(sec: Any) -> str:
    if isinstance(sec, str):
        return sec
    if isinstance(sec, list):
        return "\n".join(
            str(item.get("content", item)) if isinstance(item, dict) else str(item)
            for item in sec
        )
    if isinstance(sec, dict):
        parts: List[str] = []
        for key in ("content", "text", "description", "items", "data", "measures", "risks"):
            if key in sec:
                val = sec[key]
                if isinstance(val, list):
                    parts.extend(str(v) for v in val)
                else:
                    parts.append(str(val))
        return "\n".join(parts) if parts else str(sec)
    return str(sec)


def _classify_indicator(name: str, value: str, sub: str, category: str) -> ClassifiedIndicator:
    return ClassifiedIndicator(
        name=name,
        value=value,
        category=category,
        sub_category=sub,
        is_benefit=any(kw in name for kw in _BENEFIT_KEYWORDS) or "效益" in sub,
        is_output=any(kw in name for kw in _OUTPUT_KEYWORDS) or "产出" in sub,
        is_effectiveness=any(kw in name for kw in _EFFECTIVENESS_KEYWORDS),
    )


def _parse_indicators(sec: Any) -> List[ClassifiedIndicator]:
    indicators: List[ClassifiedIndicator] = []

    if isinstance(sec, dict):
        for cat_key, cat_name in [
            ("通用指标", "通用"),
            ("general", "通用"),
            ("行业指标", "行业"),
            ("industry", "行业"),
            ("业务指标", "业务"),
            ("business", "业务"),
        ]:
            cat_data = sec.get(cat_key)
            if cat_data is None:
                continue
            if isinstance(cat_data, list):
                for item in cat_data:
                    if isinstance(item, dict):
                        indicators.append(
                            _classify_indicator(
                                name=str(item.get("name", item.get("指标名称", ""))),
                                value=str(item.get("value", item.get("目标值", ""))),
                                sub=str(
                                    item.get(
                                        "sub_category",
                                        item.get("二级指标", item.get("三级指标", "")),
                                    )
                                ),
                                category=cat_name,
                            )
                        )
            elif isinstance(cat_data, str):
                for line in cat_data.split("\n"):
                    if line.strip():
                        indicators.append(
                            _classify_indicator(line.strip(), "", "", cat_name)
                        )

    if not indicators and isinstance(sec, list):
        for item in sec:
            if isinstance(item, dict):
                indicators.append(
                    _classify_indicator(
                        name=str(item.get("name", item.get("指标名称", ""))),
                        value=str(item.get("value", item.get("目标值", ""))),
                        sub=str(item.get("sub_category", item.get("二级指标", ""))),
                        category=str(item.get("category", item.get("一级指标", "业务"))),
                    )
                )

    if not indicators and isinstance(sec, str):
        indicators.extend(_parse_indicators_from_text(sec))

    return indicators


def _parse_indicators_from_text(text: str) -> List[ClassifiedIndicator]:
    indicators: List[ClassifiedIndicator] = []
    current_category = "业务"
    current_sub = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if "通用指标" in line:
            current_category = "通用"
        elif "行业指标" in line:
            current_category = "行业"
        elif "业务指标" in line:
            current_category = "业务"
        if "产出" in line:
            current_sub = "产出"
        elif "效益" in line:
            current_sub = "效益"
        if not _looks_like_indicator_line(line):
            continue
        indicators.append(_classify_indicator(line, "", current_sub, current_category))
    return indicators


def _looks_like_indicator_line(line: str) -> bool:
    compact = re.sub(r"\s+", "", line)
    if len(compact) < 5:
        return False
    header_tokens = ("指标名称", "指标类型", "一级指标", "二级指标", "三级指标", "目标值", "单位")
    if all(token in compact for token in ("指标", "名称")) and not re.search(
        r"\d|%|提升|降低|满意|覆盖|完成", compact
    ):
        return False
    return compact not in header_tokens


def _check_indicator_relevance(indicators: List[ClassifiedIndicator], construction_text: str) -> float:
    indicator_bigrams: Set[str] = set()
    for indicator in indicators:
        name = indicator.name.replace(" ", "")
        for index in range(len(name) - 1):
            indicator_bigrams.add(name[index : index + 2])

    text_clean = re.sub(r"\s+", "", construction_text)
    text_bigrams: Set[str] = set()
    step = max(1, len(text_clean) // 2000)
    for index in range(0, len(text_clean) - 1, step):
        text_bigrams.add(text_clean[index : index + 2])

    if not indicator_bigrams or not text_bigrams:
        return 0.0
    return len(indicator_bigrams & text_bigrams) / min(len(indicator_bigrams), 200)


def _validate_performance_target(document: Dict[str, Any]) -> List[ValidationError]:
    errors: List[ValidationError] = []
    sec_2_6 = document.get("2.6") or document.get("规划指标")
    indicators = _parse_indicators(sec_2_6) if sec_2_6 else []

    general_benefits = [
        indicator
        for indicator in indicators
        if indicator.category == "通用" and indicator.is_benefit
    ]
    if len(general_benefits) < 3:
        errors.append(
            ValidationError(
                code=ErrorCode.PTC_GENERAL_BENEFIT_INSUFFICIENT,
                message=f"通用指标中效益指标数量不足：当前 {len(general_benefits)} 个，要求至少 3 个。",
                section="2.6",
                severity="risk",
                suggestion="请参照《市级数字化项目规划指标参数填报操作手册（试行）》补充通用效益指标。",
            )
        )

    business_outputs_benefits = [
        indicator
        for indicator in indicators
        if indicator.category == "业务" and (indicator.is_output or indicator.is_benefit)
    ]
    if len(business_outputs_benefits) < 4:
        errors.append(
            ValidationError(
                code=ErrorCode.PTC_GENERAL_BENEFIT_INSUFFICIENT,
                message=f"业务指标中产出/效益指标数量不足：当前 {len(business_outputs_benefits)} 个，要求至少 4 个。",
                section="2.6",
                severity="risk",
                suggestion="请补充业务指标中的产出指标或效益指标，使其能够支撑建设内容成效。",
            )
        )

    construction_text = "\n".join(
        _to_text(document.get(key, ""))
        for key in ("5.1", "6.1", "建设内容", "建设目标", "建设方案")
    )
    is_ai_project = any(keyword in construction_text for keyword in _AI_KEYWORDS)
    if is_ai_project:
        effectiveness_indicators = [
            indicator for indicator in indicators if indicator.is_effectiveness
        ]
        if len(effectiveness_indicators) < 2:
            errors.append(
                ValidationError(
                    code=ErrorCode.PTC_AI_EFFECTIVENESS_INSUFFICIENT,
                    message=f"项目涉及智能化/AI/模型/算法，但成效指标不足：当前 {len(effectiveness_indicators)} 个，要求至少 2 个。",
                    section="2.6, 5.1/6.1",
                    severity="risk",
                    suggestion="请补充准确率、召回率、响应时间、处理速度等量化成效指标。",
                )
            )

    if indicators and construction_text:
        relevance = _check_indicator_relevance(indicators, construction_text)
        if relevance < 0.3:
            errors.append(
                ValidationError(
                    code=ErrorCode.PTC_IRRELEVANT_INDICATOR,
                    message=f"指标与建设内容的相关性较低（匹配度 {relevance:.0%}），请确认指标是否真实反映项目成效。",
                    section="2.6, 5.1/6.1",
                    severity="warning",
                    suggestion="请核对指标名称和建设内容关键词，避免设置与建设内容无关的成效指标。",
                )
            )

    return errors


def _find_governance_services(document: Dict[str, Any]) -> List[DataGovernanceFinding]:
    findings: List[DataGovernanceFinding] = []
    search_sections = [
        ("6.1", document.get("6.1") or document.get("建设内容")),
        ("5.1", document.get("5.1") or document.get("建设目标")),
        ("6.3", document.get("6.3") or document.get("数据治理")),
    ]

    for section_label, section in search_sections:
        if section is None:
            continue
        text = _to_text(section)
        for category, items in _DATA_GOVERNANCE_SERVICES.items():
            for item in items:
                if item in text:
                    index = text.find(item)
                    evidence = text[max(0, index - 30) : index + len(item) + 30].replace("\n", " ")
                    findings.append(
                        DataGovernanceFinding(item, category, section_label, evidence)
                    )

    seen: Set[str] = set()
    unique: List[DataGovernanceFinding] = []
    for finding in findings:
        if finding.service_name in seen:
            continue
        seen.add(finding.service_name)
        unique.append(finding)
    return unique


def _check_blockchain_scope(text: str) -> bool:
    chain_keywords = {"政务目录链", "区块链", "上链", "目录链", "政务链", "联盟链", "政务区块链"}
    scope_keywords = {"数据范围", "数据类别", "数据量", "更新频率", "上链方式", "接口", "上链方案", "数据目录"}
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in chain_keywords) and any(
        keyword.lower() in lowered for keyword in scope_keywords
    )


def _check_service_blockchain_coverage(service: DataGovernanceFinding, text_6_4: str) -> bool:
    expected = _SERVICE_BLOCKCHAIN_MAP.get(service.service_name)
    if expected is None:
        return True
    lowered = text_6_4.lower()
    return any(keyword.lower() in lowered for keyword in expected)


def _validate_data_blockchain(document: Dict[str, Any]) -> List[ValidationError]:
    errors: List[ValidationError] = []
    sec_6_4 = document.get("6.4") or document.get("数据上链内容")
    if sec_6_4 is None:
        errors.append(
            ValidationError(
                code=ErrorCode.DBC_CONTENT_MISSING,
                message="未找到第6.4节「数据上链内容」。",
                section="6.4",
                severity="risk",
                suggestion="请新增6.4节，明确对接政务目录链的数据范围、重点数据和上链方案。",
            )
        )
        return errors

    text_6_4 = _to_text(sec_6_4)
    if not _check_blockchain_scope(text_6_4):
        errors.append(
            ValidationError(
                code=ErrorCode.DBC_CONTENT_MISSING,
                message="第6.4节未明确描述对接政务目录链的数据范围及上链方案。",
                section="6.4",
                severity="risk",
                suggestion="请补充数据类别、来源系统、更新频率、上链方式和政务目录链对接方案。",
            )
        )

    governance_services = _find_governance_services(document)
    for service in governance_services:
        if not _check_service_blockchain_coverage(service, text_6_4):
            errors.append(
                ValidationError(
                    code=ErrorCode.DBC_DATA_SERVICE_MISSING,
                    message=f"项目涉及「{service.service_name}」数据治理服务（{service.section}节），但6.4节未体现对应数据的上链方案。",
                    section="6.4, " + service.section,
                    severity="risk",
                    suggestion=f"请在6.4节补充{service.service_name}涉及数据的上链范围、内容及对接方案。",
                )
            )

    sec_8_2 = document.get("8.2") or document.get("预算编制说明")
    if sec_8_2 is not None:
        text_8_2 = _to_text(sec_8_2)
        for negative_item in _BLOCKCHAIN_NEGATIVE_LIST:
            if negative_item in text_8_2:
                errors.append(
                    ValidationError(
                        code=ErrorCode.DBC_UPLINK_FEE_VIOLATION,
                        message=f"8.2节中出现了「{negative_item}」费用项。数据上链属于软件开发范畴，不宜作为服务费单独申报。",
                        section="8.2",
                        severity="risk",
                        suggestion="请将数据上链相关费用并入软件开发费，删除独立的上链服务费用项。",
                    )
                )
                break

    return errors


class DataReportingValidator(BaseValidator):
    """数据填报合理性审查，聚合成效目标匹配和数据上链合规两项规则。"""

    validator_id: str = "data_reporting"
    validator_name: str = "数据填报合理性审查"

    def validate(self, document: Dict[str, Any]) -> ValidationResult:
        all_errors: List[ValidationError] = []
        all_errors.extend(_validate_performance_target(document))
        all_errors.extend(_validate_data_blockchain(document))
        return ValidationResult.from_errors(all_errors)
