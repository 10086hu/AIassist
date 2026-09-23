from __future__ import annotations

"""
全组统一的大模型调用客户端。

这个文件的目标是：让所有组员调用 DeepSeek 时都走同一套入口。
组员写业务代码时，只需要做两件事：
1. 写清楚自己的 prompt，也就是告诉模型要完成什么审查任务。
2. 调用 LLMClient.chat() 或 LLMClient.chat_json() 获取结果。

不建议每个业务模块都自己写 HTTP 请求、API Key 读取、超时、重试、JSON 解析。
如果每个人都重复写一份，后期一旦接口地址、模型名称、错误处理策略变化，就要到处改。
所以这些底层逻辑统一集中在本文件里维护。

推荐使用场景：
- 数据合理性审查
- 重复建设审查
- 报告内容摘要
- 风险点提取
- 任意需要后端调用 DeepSeek 的业务模块
"""

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional

from app.core.direct_http import urlopen_direct

try:
    # 正式放入后端项目时，推荐路径是 backend/app/core/llm_client.py。
    # 这种情况下可以直接读取 app.core.config 中的统一配置。
    from app.core.config import settings
except ImportError:
    # 如果组员把 deepseek_common_package 单独复制到自己的目录里使用，
    # 可能没有 app.core.config。这里提供一个轻量兜底配置对象，
    # 让文件仍然可以通过环境变量独立运行。
    class _EnvironmentSettings:
        deepseek_api_key = os.getenv("DEEPSEEK_API_KEY")
        deepseek_api_url = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/v1")
        deepseek_model = os.getenv("DEEPSEEK_MODEL", "DeepSeek-V4-Flash")

    settings = _EnvironmentSettings()

try:
    # 后端项目内使用时，走 app.core 的绝对导入。
    from app.core.llm_json import parse_json_object_from_text, parse_json_value_from_text
except ImportError:
    try:
        # 如果这个文件作为 Python 包导入，例如 deepseek_common_package.llm_client，
        # 则使用相对导入读取同目录下的 llm_json.py。
        from .llm_json import parse_json_object_from_text, parse_json_value_from_text
    except ImportError:
        # 如果组员直接进入文件夹运行脚本，例如 python demo.py，
        # 则使用普通同目录导入。
        from llm_json import parse_json_object_from_text, parse_json_value_from_text


# DeepSeek 的 Chat Completions 接口兼容 OpenAI 的 messages 格式。
# 每条 message 都必须包含：
# - role：消息角色，常用值是 system、user、assistant。
# - content：消息正文，也就是实际发给模型看的文字。
Message = Dict[str, str]


class LLMError(RuntimeError):
    """大模型调用失败时统一抛出的异常。

    业务模块建议捕获这个异常，然后转换成更友好的接口返回，例如：
    - "大模型调用失败，请检查 API Key 是否配置"
    - "DeepSeek 账户余额不足"
    - "网络超时，请稍后重试"

    统一异常类型的好处是：业务模块不用关心底层到底是 HTTPError、TimeoutError
    还是 JSONDecodeError，只处理 LLMError 即可。
    """


@dataclass(frozen=True)
class LLMConfig:
    """一次客户端初始化后的配置快照。

    frozen=True 表示这个对象创建后不可修改。
    这样可以避免程序运行过程中某个模块意外修改配置，导致同一次请求前后行为不一致。
    """

    api_key: str
    base_url: str
    model: str
    timeout: int = 60
    max_retries: int = 2


class LLMClient:
    """全组共用的 DeepSeek 客户端。

    最常用的两个方法：
    - chat()：返回普通文本，适合摘要、自然语言解释、人工阅读的审查意见。
    - chat_json()：返回 Python dict/list，适合后端接口继续读取字段并返回给前端。

    配置读取优先级：
    1. 初始化 LLMClient 时显式传入的 api_key/base_url/model。
    2. 通用环境变量 LLM_API_KEY、LLM_BASE_URL、LLM_MODEL。
    3. DeepSeek 环境变量或项目 settings：DEEPSEEK_API_KEY、DEEPSEEK_API_URL、DEEPSEEK_MODEL。
    4. 默认地址 https://api.deepseek.com/v1 和默认模型 DeepSeek-V4-Flash。
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: int = 60,
        max_retries: int = 2,
    ) -> None:
        # API Key 是唯一必须配置的值。
        # 为了安全，不建议把真实 Key 写死在代码里，应该放在 .env 或环境变量中。
        key = (
            api_key
            or os.getenv("LLM_API_KEY")
            or getattr(settings, "deepseek_api_key", None)
            or os.getenv("DEEPSEEK_API_KEY")
            or os.getenv("OPENAI_API_KEY")
        )
        if not key:
            raise LLMError("API key is missing. Set DEEPSEEK_API_KEY or LLM_API_KEY.")

        # base_url 默认只到 /v1，不要在这里写 /chat/completions。
        # 具体接口路径会在 _post_json('/chat/completions', body) 中拼接。
        resolved_base_url = (
            base_url
            or os.getenv("LLM_BASE_URL")
            or getattr(settings, "deepseek_api_url", None)
            or os.getenv("DEEPSEEK_API_URL")
            or "https://api.deepseek.com/v1"
        )

        # 模型名称统一从环境变量读取；当前默认使用 DeepSeek-V4-Flash。
        resolved_model = (
            model
            or os.getenv("LLM_MODEL")
            or getattr(settings, "deepseek_model", None)
            or os.getenv("DEEPSEEK_MODEL")
            or "DeepSeek-V4-Flash"
        )

        self.config = LLMConfig(
            api_key=key,
            base_url=resolved_base_url.rstrip("/"),
            model=resolved_model,
            timeout=timeout,
            max_retries=max_retries,
        )

    def chat(
        self,
        messages: Iterable[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int = 2000,
        response_format: Optional[Dict[str, Any]] = None,
        extra_body: Optional[Dict[str, Any]] = None,
    ) -> str:
        """调用大模型并返回普通文本。

        参数说明：
        - messages：发给模型的对话内容。
        - temperature：随机性，越低越稳定；审查类任务建议 0.0-0.3。
        - max_tokens：最多返回多少 token，结果很长时可以调大。
        - response_format：要求模型按指定格式返回，例如 JSON。
        - extra_body：预留扩展参数，例如 top_p 等。

        适合场景：
        - 生成一段审查说明
        - 总结材料
        - 输出给人看的自然语言意见
        """
        body: Dict[str, Any] = {
            "model": self.config.model,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        # DeepSeek 兼容 OpenAI 的 response_format 参数。
        # 当我们希望模型返回 JSON 时，会传 {"type": "json_object"}。
        if response_format is not None:
            body["response_format"] = response_format

        # extra_body 用于保留扩展能力。
        # 普通组员刚开始使用时可以不用管它。
        if extra_body:
            body.update(extra_body)

        data = self._post_json("/chat/completions", body)

        # DeepSeek/OpenAI 兼容接口的典型响应结构：
        # {
        #   "choices": [
        #     {"message": {"content": "模型输出内容"}}
        #   ]
        # }
        # 如果这里结构不对，通常说明接口返回了异常格式或供应商响应变更。
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected LLM response shape: {data}") from exc

        if not isinstance(content, str):
            raise LLMError(f"LLM content is not text: {content!r}")
        return content.strip()

    def chat_json(
        self,
        messages: Iterable[Message],
        *,
        temperature: float = 0.1,
        max_tokens: int = 2000,
        require_object: bool = True,
        extra_body: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """调用大模型并解析 JSON 结果。

        审查接口强烈建议使用这个方法，而不是 chat()。
        原因是前端和后端通常需要结构化字段，例如：
        - passed：是否通过
        - risk_level：风险等级
        - summary：总结
        - issues：问题列表
        - suggestions：整改建议

        本方法会先要求模型尽量返回 JSON，再调用 llm_json.py 中的解析函数兜底处理。
        即使模型返回 Markdown 代码块或 JSON 前后夹杂说明文字，也尽量能解析出来。

        require_object=True 表示必须返回 JSON 对象，也就是 Python dict。
        如果你的业务确实允许模型返回顶层数组，可以传 require_object=False。
        """
        text = self.chat(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            extra_body=extra_body,
        )
        return parse_json_object_from_text(text) if require_object else parse_json_value_from_text(text)

    def _post_json(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """发送 HTTP POST 请求并返回 JSON 对象。

        这是内部方法，业务模块不要直接调用。
        它统一处理：
        - 拼接 DeepSeek 接口地址
        - 添加 Authorization 请求头
        - 将 Python dict 编码成 JSON 请求体
        - 读取并解析 JSON 响应
        - 对网络错误、超时、HTTP 错误做统一包装
        - 对临时错误做有限重试
        """
        url = f"{self.config.base_url}{path}"
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

        last_error: BaseException | None = None
        for attempt in range(self.config.max_retries + 1):
            request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
            try:
                with urlopen_direct(request, timeout=self.config.timeout) as response:
                    raw = response.read().decode("utf-8")
                    data = json.loads(raw)
                    if not isinstance(data, dict):
                        raise LLMError(f"LLM response is not a JSON object: {raw[:500]}")
                    return data
            except urllib.error.HTTPError as exc:
                # HTTPError 中通常包含 DeepSeek 返回的错误原因。
                # 例如 401 表示 Key 无效，402 表示账户余额不足，429 表示请求过快。
                error_body = exc.read().decode("utf-8", errors="replace")
                last_error = LLMError(f"HTTP {exc.code}: {error_body}")

                # 400/401/402 这类问题通常重试也不会成功，所以直接结束。
                # 408/429/5xx 可能是临时问题，允许进入重试。
                if exc.code not in {408, 429, 500, 502, 503, 504}:
                    break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, LLMError) as exc:
                # 网络不可达、超时、响应不是合法 JSON 等问题统一记录。
                last_error = exc

            # 指数退避：第 1 次失败后等 1 秒，第 2 次失败后等 2 秒。
            # 这样可以减少接口临时拥堵时连续请求造成的压力。
            if attempt < self.config.max_retries:
                time.sleep(2**attempt)

        raise LLMError(f"LLM request failed: {last_error}") from last_error


# 别名：组员如果更习惯写 DeepSeekClient，也可以这样导入。
DeepSeekClient = LLMClient


def get_llm_client(**kwargs: Any) -> LLMClient:
    """创建 LLMClient 的工厂函数。

    现在它只是简单返回 LLMClient(**kwargs)。
    以后如果需要做客户端缓存、统一日志、依赖注入，都可以只改这个函数，
    业务模块里调用 get_llm_client() 的代码不用跟着改。
    """
    return LLMClient(**kwargs)
