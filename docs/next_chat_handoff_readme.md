# 下一次聊天接力 README

生成日期：2026-08-14

本文件用于在下一次 Codex 聊天中快速恢复上下文。项目根目录为：

```text
D:\AIassist
```

当前项目是 Windows WPF 桌面端 + FastAPI 后端的可研报告智能审查助手。

## 1. 当前分支

当前已新建并切换到分支：

```text
duplicate-check-completion
```

之前曾尝试创建 `codex/duplicate-check-completion`，但 Git refs 创建目录式分支失败，因此改用不带斜杠的分支名。

建议下一次聊天先运行：

```powershell
git status --short --branch
```

确认当前分支和工作区状态。

## 2. 本轮主要完成内容

### 2.1 大模型 API 配置

已新增 API key 配置脚本：

```text
configure_llm_api.ps1
configure_llm_api.bat
```

用途：

- 隐藏输入 API key。
- 写入当前 Windows 用户环境变量。
- 避免把密钥写入 Git 仓库。

使用方式：

```powershell
.\configure_llm_api.bat
```

配置项包括：

```text
DEEPSEEK_API_KEY
DEEPSEEK_API_URL=https://llmapi.tongji.edu.cn/v1
DEEPSEEK_API_BASE_URL=https://llmapi.tongji.edu.cn/v1
DEEPSEEK_MODEL=DeepSeek-R1
LLM_API_KEY
LLM_BASE_URL
LLM_MODEL
```

注意：不要在聊天、文档或提交内容中暴露真实 API key。

### 2.2 重复建设检查模块

重复建设检查模块已经从“当前报告内部重复检查”扩展为：

- 当前可研报告内部重复功能点检查。
- 当前可研报告与一个或多个往期可研报告之间的重复功能点检查。
- 支持 Word 文档解析。
- 支持 Excel/CSV 功能点清单解析。
- 支持大模型抽取可研报告中的功能点。
- 支持大模型判断功能点是否重复、高度相似或无关。

核心后端接口：

```text
POST /api/evaluate/duplicate/internal
POST /api/evaluate/duplicate/compare
```

重点接口是：

```text
POST /api/evaluate/duplicate/compare
```

它接收：

```text
current_file
history_files
project_name
department
current_stage
history_stages
```

### 2.3 WPF 前端接入

WPF 前端已接入重复建设检查模块：

- 用户选择“重复建设检查”后，会弹窗询问是否有往期文件。
- 如果有往期文件，允许多选 `.docx/.xlsx/.csv`。
- 有往期文件时调用 `/api/evaluate/duplicate/compare`。
- 没有往期文件时走当前报告内部重复检查逻辑。

主要修改文件：

```text
desktop-wpf/MainWindow.xaml.cs
```

### 2.4 DeepSeek-R1 稳定性修复

验证过程中发现：

- `DeepSeek-R1` 会优先生成很长的 `reasoning`。
- 如果 `max_tokens` 不够，`message.content` 可能为空。
- 后端之前会误读 `reasoning`，导致 JSON 解析失败。
- 同济模型接口偶发 DNS 解析失败、连接中断或远端断开。

已修复：

- 重复建设模块只读取 `message.content`，不再把 `reasoning` 当成结构化结果。
- 功能点抽取解析失败时自动重试一次。
- 新增 `DUPLICATE_LLM_TIMEOUT_SECONDS`，默认 `180` 秒。
- 新增 `DUPLICATE_LLM_MAX_TOKENS`，默认 `6000`。
- `run_backend.bat` 已设置上述默认值。

主要修改文件：

```text
backend/app/core/config.py
backend/app/modules/duplicate/llm_extractor.py
backend/app/modules/duplicate/llm_judge.py
run_backend.bat
```

## 3. 验证结果

已生成两份 Word 可研报告样例：

```text
samples/duplicate_report_test/current_feasibility_report.docx
samples/duplicate_report_test/history_phase1_feasibility_report.docx
```

验证不是使用功能点清单，而是直接上传两份 Word 可研报告到后端接口。

最终成功结果：

```text
HTTP_STATUS 200
status: completed
当前报告提取功能点: 14
往期报告提取功能点: 13
跨报告重复项: 13
使用模型: deepseek
```

部分识别结果：

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

结论：

```text
Word 报告生成 -> Word 解析 -> 大模型功能点抽取 -> 跨报告重复判断 -> 后端接口返回
```

这条链路已经验证通过。

## 4. 新增验证脚本

新增脚本：

```text
scripts/validate_duplicate_compare_api.py
scripts/run_duplicate_compare_http_validation.py
```

推荐验证命令：

```powershell
python scripts\run_duplicate_compare_http_validation.py
```

该脚本会：

- 加载 Windows 用户环境变量中的大模型配置。
- 临时启动 FastAPI 后端。
- 生成两份 Word 可研报告。
- 调用 `/api/evaluate/duplicate/compare`。
- 输出 HTTP 状态、功能点数量、重复项数量和前若干条重复结果。

如果模型接口偶发 DNS 或远端断开，可重试该脚本。

## 5. 项目当前完成情况

九大模块大致状态如下：

| 模块 | 当前状态 |
|---|---|
| 建设依据审查 | 已有后端模块和接口，可继续补规则 |
| 重复建设检查 | 已完成并验证通过 |
| 建设功能对应关系检查 | 已有后端模块和接口 |
| 数据填报合理性 | 已有 `data_rules` / `data-reporting` 能力 |
| 资源申请合理性 | 已有 `resource` 能力 |
| 安全内容合理性 | 待独立完善 |
| 价格合理性 | 预留接口，未完整实现 |
| 软硬件产品价格参考 | 待实现 |
| 非信创内容检查 | 待实现 |

另外项目中已有 `sensitive_word` 敏感词检查能力，可继续接入或归并到审查体系中。

## 6. 重要文件清单

本轮重点相关文件：

```text
backend/app/api/evaluate.py
backend/app/core/config.py
backend/app/modules/duplicate/service.py
backend/app/modules/duplicate/llm_extractor.py
backend/app/modules/duplicate/llm_judge.py
backend/app/schemas.py
desktop-wpf/MainWindow.xaml.cs
run_backend.bat
configure_llm_api.bat
configure_llm_api.ps1
scripts/validate_duplicate_compare_api.py
scripts/run_duplicate_compare_http_validation.py
samples/duplicate_report_test/
docs/project_status_readme.md
docs/next_chat_handoff_readme.md
```

## 7. 启动和验证命令

配置 API key：

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

构建 WPF：

```powershell
dotnet build desktop-wpf\AiReportDesktop.csproj
```

验证重复建设检查：

```powershell
python scripts\run_duplicate_compare_http_validation.py
```

## 8. 已知问题和注意事项

1. 同济大模型接口偶发 DNS 解析失败或远端断开，通常重试可恢复。
2. `DeepSeek-R1` reasoning 很长，必须保证 `DUPLICATE_LLM_MAX_TOKENS` 足够大。
3. 不要把 API key 提交到 Git。
4. PowerShell 直接 `Get-Content` 查看 UTF-8 Markdown 时可能显示乱码，建议用 VS Code 或明确指定 UTF-8。
5. 项目当前仍有部分模块只是入口或预留接口，重点已完成的是重复建设检查模块。

## 9. 下一步建议

下一次聊天可以优先做：

1. 检查当前分支工作区状态。
2. 确认是否需要提交并 push。
3. 如需提交，建议先检查是否要包含 `samples/duplicate_report_test/`。
4. 给重复建设检查增加自动化测试。
5. 优化 WPF 结果展示，将“内部重复”和“与往期重复”分组展示。
6. 为大模型抽取结果增加缓存，降低重复调用成本。
