# 全组统一 DeepSeek 调用说明

这个文档给组员看，用来说明：后端模块如何统一调用 DeepSeek 大模型。

核心原则：业务模块只写自己的审查逻辑和提示词，不要重复写接口密钥、HTTP 请求、重试、JSON 解析这些底层代码。

## 文件作用

`llm_client.py`

统一的大模型调用入口。组员主要使用这里的 `LLMClient`。

它负责：

- 读取 `DEEPSEEK_API_KEY` 等环境变量。
- 拼接 DeepSeek 接口地址。
- 发送 `/chat/completions` 请求。
- 处理超时、HTTP 错误、余额不足、接口密钥缺失等异常。
- 提供 `chat()` 和 `chat_json()` 两种调用方式。

`llm_json.py`

统一的 JSON 解析工具。组员通常不需要直接调用它，`LLMClient.chat_json()` 会自动使用它。

它负责：

- 解析纯 JSON。
- 解析 Markdown 代码块里的 JSON。
- 从带解释文字的模型回复中提取 JSON。
- 避免后端出现“无法解析 LLM 响应”的问题。

`LLM_USAGE.md`

就是当前这个说明文档。

它负责：

- 告诉组员如何配置 DeepSeek 接口密钥。
- 告诉组员如何导入和调用大模型。
- 给出数据合理性审查等业务示例。
- 说明常见报错应该怎么排查。

## 第一步：配置环境变量

启动后端前，先在 PowerShell 中配置 DeepSeek 接口密钥：

```powershell
# 必填：DeepSeek 接口密钥，没有它就无法调用大模型
$env:DEEPSEEK_API_KEY="你的 DeepSeek 接口密钥"

# 可选：DeepSeek 接口地址，一般保持默认即可
$env:DEEPSEEK_API_URL="https://api.deepseek.com/v1"

# 可选：DeepSeek 模型名称，一般使用 deepseek-chat
$env:DEEPSEEK_MODEL="deepseek-chat"
```

说明：

- `DEEPSEEK_API_KEY` 必填，没有它就无法调用大模型。
- `DEEPSEEK_API_URL` 一般不用改，默认就是 DeepSeek 官方地址。
- `DEEPSEEK_MODEL` 一般用 `deepseek-chat`。
- 不要把真实接口密钥提交到 Git 或发到群里。

如果你使用项目里的 `.env` 文件，也可以写成：

```env
# DeepSeek 接口密钥，必须配置
DEEPSEEK_API_KEY=你的 DeepSeek 接口密钥

# DeepSeek 接口地址，通常不用改
DEEPSEEK_API_URL=https://api.deepseek.com/v1

# DeepSeek 模型名称，通常不用改
DEEPSEEK_MODEL=deepseek-chat
```

## 第二步：在业务模块中导入

如果文件已经放在后端项目的 `backend/app/core` 下面，业务模块这样导入：

```python
# LLMClient：统一的大模型调用客户端
# LLMError：大模型调用失败时抛出的统一异常
from app.core.llm_client import LLMClient, LLMError
```

如果你只是单独复制了 `deepseek_common_package` 文件夹，也可以在同目录脚本里这样导入：

```python
# 单独复制共享包时，也可以从同目录导入
from llm_client import LLMClient, LLMError
```

## 普通文本调用

当你只需要一段自然语言结果时，用 `chat()`。

适合：

- 生成审查意见。
- 总结报告。
- 输出给人工阅读的解释。

```python
# 从后端统一位置导入大模型客户端
from app.core.llm_client import LLMClient

# 创建客户端；它会自动读取 DEEPSEEK_API_KEY 等环境变量
client = LLMClient()

# chat() 返回普通字符串，适合生成给人看的审查意见
answer = client.chat(
    messages=[
        {
            # system 用来定义模型角色和总体要求
            "role": "system",
            "content": "你是严格的政务项目审查助手，请给出清晰、谨慎、可追溯的意见。",
        },
        {
            # user 用来放本次具体任务和待审查内容
            "role": "user",
            "content": "请总结这个项目材料的主要风险点：项目预算 500 万，建设周期 1 个月。",
        },
    ],
    # 审查类任务建议温度低一点，输出更稳定
    temperature=0.2,
    # 控制模型最多输出多长，内容较多时可以调大
    max_tokens=1000,
)

# 打印模型返回的自然语言文本
print(answer)
```

## JSON 结构化调用

后端接口更推荐用 `chat_json()`。

原因：接口通常要返回固定字段给前端，例如 `passed`、`risk_level`、`issues`，不能只返回一段文字。

```python
# 从后端统一位置导入大模型客户端
from app.core.llm_client import LLMClient

# 创建客户端；它会自动读取 DeepSeek 配置
client = LLMClient()

# chat_json() 会要求模型返回 JSON，并自动解析成 Python dict
result = client.chat_json(
    messages=[
        {
            # system 提示词要明确要求“只返回 JSON”
            "role": "system",
            "content": (
                "你是严格的数据合理性审查助手。"
                "必须只返回 JSON，不要返回 Markdown，不要添加解释文字。"
            ),
        },
        {
            # user 提示词里放具体数据和返回字段格式
            "role": "user",
            "content": """
请审查以下数据是否合理，并按指定 JSON 格式返回。

待审查数据：
- 项目名称：智慧园区平台
- 建设周期：1 个月
- 预算金额：5000 万元
- 运维人员：0 人

返回格式：
{
  "passed": true,
  "risk_level": "low|medium|high",
  "summary": "一句话总结",
  "issues": [
    {
      "field": "字段名",
      "problem": "发现的问题",
      "reason": "判断原因",
      "suggestion": "整改建议"
    }
  ]
}
""",
        },
    ],
    # JSON 审查类任务建议设置得更稳定
    temperature=0.1,
    max_tokens=1500,
)

# result 已经是 dict，可以像普通字典一样读取字段
print(result["passed"])
print(result["risk_level"])
print(result["issues"])
```

## 数据合理性审查推荐写法

建议每个业务模块封装一个自己的函数，例如：

```python
# 业务模块只需要导入统一客户端，不要重复写 DeepSeek 请求代码
from app.core.llm_client import LLMClient, LLMError


def review_data_reasonableness(project_data: dict) -> dict:
    """调用大模型进行数据合理性审查。

    project_data 是业务接口传入的项目数据。
    返回值是结构化 dict，方便 FastAPI 接口直接返回给前端。
    """
    # 创建统一客户端；接口密钥、模型名、接口地址都由 llm_client.py 统一处理
    client = LLMClient()

    try:
        # 审查类接口推荐使用 chat_json()，这样后端拿到的就是结构化结果
        return client.chat_json(
            messages=[
                {
                    # system 提示词：定义模型身份、审查角度和输出约束
                    "role": "system",
                    "content": (
                        "你是政务信息化项目的数据合理性审查助手。"
                        "请从预算、周期、人员、规模、逻辑矛盾等角度审查。"
                        "必须只返回 JSON。"
                    ),
                },
                {
                    # user 提示词：传入具体项目数据，并明确要求返回哪些字段
                    "role": "user",
                    "content": f"""
请审查以下项目数据是否合理：

{project_data}

返回 JSON 格式：
{{
  "passed": true,
  "risk_level": "low|medium|high",
  "summary": "一句话总结",
  "issues": [
    {{
      "field": "字段名",
      "problem": "问题描述",
      "reason": "判断依据",
      "suggestion": "建议"
    }}
  ]
}}
""",
                },
            ],
            temperature=0.1,
            max_tokens=2000,
        )
    except LLMError as exc:
        # 统一捕获大模型调用异常，避免后端接口直接报 500 崩溃
        return {
            "passed": False,
            "risk_level": "unknown",
            "summary": "大模型调用失败，无法完成智能审查。",
            "issues": [
                {
                    "field": "LLM",
                    "problem": str(exc),
                    "reason": "DeepSeek 调用失败，可能是接口密钥、余额、网络或接口配置问题。",
                    "suggestion": "请检查 DEEPSEEK_API_KEY、账户余额和后端日志。",
                }
            ],
        }
```

## chat 和 chat_json 怎么选

用 `chat()` 的情况：

- 只需要一段文字。
- 不需要后端按字段读取。
- 例如摘要、解释、自然语言建议。

用 `chat_json()` 的情况：

- 后端要把结果返回给前端展示。
- 前端要读取固定字段。
- 需要保存审查结论到数据库。
- 例如数据合理性审查、重复建设审查、风险清单生成。

## 常见错误

`deepseek_api_key 未配置`

说明没有配置 DeepSeek 接口密钥。

解决：

```powershell
# 设置 DeepSeek 接口密钥后，需要重新启动后端才会生效
$env:DEEPSEEK_API_KEY="你的 DeepSeek 接口密钥"
```

然后重新启动后端。

`Payment required`

说明 DeepSeek 账户余额不足，或者当前接口密钥没有可用额度。

解决：

- 登录 DeepSeek 控制台检查余额。
- 确认后端使用的是有余额的接口密钥。
- 充值或更换接口密钥后重新启动后端。

`无法解析 LLM 响应`

说明模型没有按要求返回 JSON，或者返回内容里混入了额外文字。

解决：

- 优先使用 `chat_json()`，不要自己直接 `json.loads()` 模型原文。
- 在 system 提示词里写清楚“必须只返回 JSON”。
- 在 user 提示词里给出明确的 JSON 返回格式。

`Method Not Allowed`

通常说明请求方法不对，例如接口要求 POST，但你用浏览器地址栏发了 GET。

解决：

- 用前端按钮、Postman、Apifox 或 curl 发 POST 请求。
- 不要直接在浏览器地址栏访问 POST 接口。

## 组内统一约定

建议全组遵守：

- 所有人都从 `app.core.llm_client` 导入 `LLMClient`。
- 所有人都使用 `chat_json()` 做审查类接口。
- 不要在业务模块里重复写 DeepSeek 请求代码。
- 不要把接口密钥写进代码。
- 提示词可以每个业务模块自己写，但返回 JSON 字段尽量统一。
- 大模型失败时要返回可读错误，不要让后端直接崩溃。
