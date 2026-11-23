from __future__ import annotations

"""
大模型 JSON 解析工具。

为什么要单独有这个文件：
1. 大模型不是传统接口，它虽然可以被要求“只返回 JSON”，但实际输出可能不稳定。
2. 常见返回形式包括：
   - 纯 JSON：{"passed": true}
   - Markdown 代码块：```json ... ```
   - JSON 前后带解释文字：下面是审查结果：{...}
3. 后端接口通常需要 dict/list，而不是一整段字符串。
   如果不做兜底解析，就容易出现“无法解析 LLM 响应”。

本文件只负责一件事：从模型返回的文本中尽量提取合法 JSON。
它不负责调用 DeepSeek。真正发送请求的逻辑在 llm_client.py 中。
"""

import json
import re
from typing import Any


# 用来匹配 Markdown 代码块，例如：
# ```json
# {"passed": true, "reason": "ok"}
# ```
#
# 说明：
# - (?:json)? 表示代码块语言可以写 json，也可以不写。
# - re.IGNORECASE 表示 JSON/json/Json 都能匹配。
# - re.DOTALL 表示中间内容允许跨多行。
FENCED_BLOCK_PATTERN = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)


def parse_json_value_from_text(text: str) -> Any:
    """从大模型返回文本中提取第一个可解析的 JSON 值。

    这个函数是“宽松解析”：
    - 顶层是对象 dict，可以解析。
    - 顶层是数组 list，也可以解析。
    - 顶层是字符串、数字、布尔值，只要是合法 JSON，也可以解析。

    适合场景：
    - 你不确定模型会返回对象还是数组。
    - 你的业务允许顶层数组，例如问题列表 [{"field": "金额", "issue": "..."}]。

    如果你的接口必须返回 JSON 对象，请优先使用 parse_json_object_from_text()。
    """
    decoder = json.JSONDecoder()
    last_error: Exception | None = None

    # _json_candidates() 会把“最可能是 JSON 的片段”按优先级列出来。
    # 这里逐个尝试，只要有一个候选能解析成功，就立即返回。
    for candidate in _json_candidates(text):
        stripped = candidate.strip().lstrip("\ufeff")
        if not stripped:
            continue

        # 第一种尝试：候选文本本身就是合法 JSON。
        # 例如候选内容刚好是 {"passed": true}。
        try:
            return json.loads(stripped)
        except json.JSONDecodeError as exc:
            last_error = exc

        # 第二种尝试：候选文本前面可能带了说明文字。
        # 例如：审查结果如下：{"passed": true}
        # raw_decode 可以从某个位置开始解析，并允许 JSON 后面还有其他文字。
        for index, char in enumerate(stripped):
            if char not in "{[":
                continue
            try:
                value, _ = decoder.raw_decode(stripped[index:])
                return value
            except json.JSONDecodeError as exc:
                last_error = exc

    # 走到这里说明所有候选都解析失败。
    # 错误信息只截取前 300 个字符，避免日志或接口返回里塞进过长模型原文。
    raise ValueError(f"No JSON value found in LLM response. Preview: {text[:300]}") from last_error


def parse_json_object_from_text(text: str) -> dict[str, Any]:
    """从大模型返回文本中提取 JSON 对象，也就是 Python dict。

    审查接口最推荐使用这个函数，因为多数接口需要固定字段，例如：
    - passed：是否通过审查
    - risk_level：风险等级
    - summary：一句话总结
    - issues：问题列表
    - suggestions：整改建议

    如果模型返回的是数组或其他 JSON 类型，本函数会抛出 ValueError。
    这能帮助我们尽早发现 prompt 没约束好，或者模型没有按接口约定返回。
    """
    value = parse_json_value_from_text(text)
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object, got {type(value).__name__}")
    return value


def _json_candidates(text: str) -> list[str]:
    """生成多个可能包含 JSON 的候选文本。

    候选顺序很重要：
    1. 优先尝试 Markdown 代码块里的内容。
       因为模型常常把真正的 JSON 放进 ```json ... ``` 中。
    2. 再尝试完整原文。
       这样可以覆盖模型直接返回纯 JSON 的情况。
    3. 再截取最外层 {...}。
       这样可以覆盖 JSON 前后有解释文字的情况。
    4. 最后截取最外层 [...]。
       这样可以覆盖模型返回顶层数组的情况。

    这个函数只负责生成候选，不负责判断候选是否真的合法。
    真正的合法性检查交给 json.loads() 和 JSONDecoder.raw_decode()。
    """
    candidates = [match.group(1) for match in FENCED_BLOCK_PATTERN.finditer(text)]
    candidates.append(text)

    object_start = text.find("{")
    object_end = text.rfind("}")
    if object_start != -1 and object_end > object_start:
        candidates.append(text[object_start : object_end + 1])

    array_start = text.find("[")
    array_end = text.rfind("]")
    if array_start != -1 and array_end > array_start:
        candidates.append(text[array_start : array_end + 1])

    return candidates
