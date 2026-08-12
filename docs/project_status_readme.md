# 可研报告智能审查助手项目当前状态说明

生成时间：2026-08-12

本文档用于补充说明当前项目完成情况、近期完成的重复建设检查模块改造、大模型 API 配置方式，以及后续验证和开发建议。根目录原有 `readme.md` 未修改。

## 一、项目总体定位

本项目是一个面向可研报告审查场景的 Windows 桌面客户端 + FastAPI 后端服务组合。

当前整体架构为：

```text
Windows WPF 桌面客户端
  -> HTTP/HTTPS API
FastAPI 后端服务
  -> 文档解析 / 规则审查 / 大模型审查 / SQLite 或正式数据库
```

项目当前重点围绕九大审查能力入口建设，其中部分模块已具备后端接口和前端接入能力，重复建设检查模块已完成“当前可研报告内部 + 当前报告与往期报告之间”的完整验证链路。

## 二、近期完成内容总结

### 1. 大模型 API 配置

已根据同济 OpenAI 兼容接口完成配置脚本：

```text
configure_llm_api.ps1
configure_llm_api.bat
```

脚本会让用户隐藏输入 API key，并写入当前 Windows 用户环境变量：

```text
DEEPSEEK_API_KEY
DEEPSEEK_API_URL=https://llmapi.tongji.edu.cn/v1
DEEPSEEK_API_BASE_URL=https://llmapi.tongji.edu.cn/v1
DEEPSEEK_MODEL=DeepSeek-R1
LLM_API_KEY
LLM_BASE_URL
LLM_MODEL
```

后端启动脚本 `run_backend.bat` 已支持从用户环境变量读取这些配置，避免把密钥写入代码仓库。

### 2. 重复建设检查模块

已完成重复建设检查模块的核心扩展：

- 支持上传当前可研报告。
- 支持询问用户是否有往期可研报告。
- 支持上传一个或多个往期文件。
- 支持当前报告内部重复功能点检查。
- 支持当前报告与往期报告之间重复功能点检查。
- 支持 Word/PDF 文档解析后，通过大模型抽取功能点。
- 支持使用大模型判断功能点是否“重复 / 高度相似 / 无关”。
- 支持输出重复项标题、风险等级、原因、证据、修改建议、模型名称。

新增或重点修改的后端能力：

```text
POST /api/evaluate/duplicate/internal
POST /api/evaluate/duplicate/compare
```

其中 `/duplicate/compare` 是本次重点接口，用于比较当前可研报告与往期可研报告。

### 3. 前端接入

WPF 前端已接入重复建设检查模块：

- 用户选择“重复建设检查”后，会弹窗询问是否上传往期文件。
- 如果选择有往期文件，可以多选 `.docx/.pdf/.xlsx/.csv`。
- 前端会调用后端 `/api/evaluate/duplicate/compare`。
- 如果没有往期文件，则仍可走当前报告内部重复检查逻辑。

相关代码主要在：

```text
desktop-wpf/MainWindow.xaml.cs
```

### 4. 大模型调用稳定性修复

验证过程中发现 `DeepSeek-R1` 会优先产生较长 reasoning，如果 `max_tokens` 较小，可能出现 `content=null` 或最终 JSON 不完整。

已做修复：

- 新增 `DUPLICATE_LLM_TIMEOUT_SECONDS`，默认 `180` 秒。
- 新增 `DUPLICATE_LLM_MAX_TOKENS`，默认 `6000`。
- 功能点抽取阶段只解析模型返回的 `message.content`，避免误读 `reasoning`。
- 功能点抽取解析失败时自动重试一次。
- 重复判断阶段同样只读取 `message.content`。

这些修改主要位于：

```text
backend/app/core/config.py
backend/app/modules/duplicate/llm_extractor.py
backend/app/modules/duplicate/llm_judge.py
run_backend.bat
```

## 三、验证结果

已生成两份较完整的 Word 可研报告样例：

```text
samples/duplicate_report_test/current_feasibility_report.docx
samples/duplicate_report_test/history_phase1_feasibility_report.docx
```

验证方式不是功能点清单，而是直接上传两份 Word 可研报告到后端接口。

最终验证结果：

```text
HTTP_STATUS 200
status: completed
当前报告提取功能点: 14
往期报告提取功能点: 13
跨报告重复项: 13
使用模型: deepseek
```

识别出的重复或相似功能点包括：

```text
单点登录 ↔ 单点登录
权限校验 ↔ 权限校验
交换监控 ↔ 交换监控
接口发布 ↔ 接口发布
跨部门数据交换 ↔ 跨部门数据交换
异常告警 ↔ 异常告警
统一账号管理 ↔ 账号统一管理
会话管理 ↔ 会话管理
登录审计 ↔ 登录审计
接口订阅 ↔ 接口订阅
```

因此可以确认：Word 报告解析、大模型功能点抽取、跨报告重复判断、后端接口返回、前端调用入口这条链路已经完成并验证通过。

## 四、验证脚本

新增验证脚本：

```text
scripts/validate_duplicate_compare_api.py
scripts/run_duplicate_compare_http_validation.py
```

推荐使用：

```powershell
python scripts\run_duplicate_compare_http_validation.py
```

该脚本会：

- 加载当前 Windows 用户环境中的大模型配置。
- 在本地临时启动 FastAPI 后端。
- 生成两份 Word 可研报告样例。
- 调用 `/api/evaluate/duplicate/compare`。
- 输出接口状态、功能点数量、重复项数量和前若干条重复结果。

## 五、当前九大模块完成情况

| 模块 | 当前状态 | 说明 |
|---|---|---|
| 建设依据审查 | 已有后端接口/模块 | 已有 `shanghai_review` 相关能力，可继续补规则和展示细节 |
| 重复建设检查 | 已完成并验证 | 已支持当前报告内部检查和当前报告与往期报告对比 |
| 建设功能对应关系检查 | 已有后端接口/模块 | 已有 `function_correspondence` 相关能力 |
| 数据填报合理性 | 已有后端接口/模块 | 已有 `data_rules`、`data-reporting` 相关能力 |
| 资源申请合理性 | 已有后端接口/模块 | 已有 `resource` 相关能力 |
| 安全内容合理性 | 待进一步独立完善 | 当前未看到完整独立安全审查模块闭环 |
| 价格合理性 | 预留/未完整实现 | 后端存在预留接口，返回未实现状态 |
| 软硬件产品价格参考 | 待实现 | 当前未看到完整独立实现 |
| 非信创内容检查 | 待实现 | 当前未看到完整独立实现 |

另外，项目中已有 `sensitive_word` 敏感词检查能力，可作为实际审查能力的一部分继续接入或归并到九大模块体系中。

## 六、启动方式

配置大模型 API key：

```powershell
.\configure_llm_api.bat
```

启动后端：

```powershell
.\run_backend.bat
```

启动桌面客户端：

```powershell
.\run_desktop.bat
```

或直接构建 WPF：

```powershell
dotnet build desktop-wpf\AiReportDesktop.csproj
```

## 七、当前主要风险和注意事项

1. 同济大模型接口偶发 DNS 解析失败或连接中断，验证时可能需要重试。
2. `DeepSeek-R1` reasoning 较长，重复建设模块已提高 token 上限，但复杂长报告仍建议做分章抽取和缓存。
3. 当前本地开发使用 SQLite，正式部署应切换到服务器数据库。
4. API key 不应提交到仓库，应继续使用环境变量或服务器密钥管理。
5. 部分中文源码在 Windows 控制台显示可能乱码，但不一定代表文件内容损坏；验证应以实际运行和 UTF-8 解析为准。

## 八、后续建议

优先建议：

1. 将重复建设检查结果在 WPF 前端做更细的结果展示，例如按“内部重复 / 与往期重复”分组。
2. 给大模型抽取结果增加缓存，避免同一报告重复调用模型。
3. 对长篇可研报告增加分章抽取、合并去重和失败重试队列。
4. 给 `/duplicate/compare` 增加自动化测试用例，覆盖 Word、PDF、Excel/CSV 混合输入。
5. 继续完善未完成的价格、安全、信创相关模块。

