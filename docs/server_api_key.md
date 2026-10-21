# 服务器模型 API Key 配置与验证

后端服务器：`10.60.150.179`，开发机连接命令：`ssh worker8`。

## 配置在哪里生效

正式配置文件为服务器 `/etc/aiassist/backend.env`，权限必须保持 `0600`。
`aiassist-api.service` 每次启动都会执行 `docker run --env-file /etc/aiassist/backend.env`，
将配置注入 `aiassist-backend` 容器。后端从进程环境读取配置，不会自动读取本机或服务器源码目录中的 `.env`。

2026-10-21 已将用户提供的密钥写入服务器，并将两个调用入口统一到项目配置脚本使用的同济模型网关。配置文件权限为 `0600`，没有将密钥写入本机项目或文档。

当前模型相关变量如下，示例中不包含真实密钥：

```dotenv
DEEPSEEK_API_KEY=<填写实际密钥>
DEEPSEEK_API_URL=https://llmapi.tongji.edu.cn/v1
DEEPSEEK_API_BASE_URL=https://llmapi.tongji.edu.cn/v1
DEEPSEEK_MODEL=DeepSeek-V4-Flash
```

- `DEEPSEEK_API_KEY`：模型服务商的密钥，与登录本后端得到的 Bearer 令牌用途不同。
- `DEEPSEEK_API_URL`：公共模型客户端及重复建设模块使用。
- `DEEPSEEK_API_BASE_URL`：模型连通性测试和审查复核客户端使用。
- 两个地址都填写接口根地址，不带 `/chat/completions`；使用第三方兼容接口时，地址和模型名都应使用该服务商提供的值。
- 公共客户端还支持 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL` 等覆盖变量。当前服务器未配置这些覆盖变量。

## 在服务器配置

```bash
ssh worker8

# 在服务器执行：备份保存在 root 私有目录，编辑时保留其他数据库及认证配置
install -d -m 700 /var/backups/aiassist
cp --preserve=mode,ownership /etc/aiassist/backend.env \
  "/var/backups/aiassist/backend.env.before-llm.$(date +%Y%m%d-%H%M%S)"
vi /etc/aiassist/backend.env
chmod 600 /etc/aiassist/backend.env

# 等正在执行的审查任务结束后，重建容器并加载新环境
systemctl restart aiassist-api.service
systemctl is-active aiassist-api.service
docker inspect aiassist-backend --format '{{.State.Health.Status}}'
```

容器刚启动时健康状态可能为 `starting`，稍后应变为 `healthy`。
修改文件后需使用 `systemctl restart aiassist-api.service`；
单独执行 `docker restart aiassist-backend` 会沿用旧容器的环境变量。
密钥不要写入源码、命令行参数或提交到 Git。

也可以先在本机 `backend/.env` 填写模型配置，再由部署操作只合并模型变量到上述服务器配置。
不要用本机 `.env` 整体覆盖服务器文件，否则可能丢失服务器数据库及其他配置。

## 验证真实模型调用

健康检查 `/api/health` 仅验证后端存活。模型接口需先登录：

| 请求 | 用途 | 判断依据 |
| --- | --- | --- |
| `GET /api/health` | 后端健康 | HTTP 200，`status=ok` |
| `POST /api/auth/login` | 使用已注册账号登录 | HTTP 200，取得 `access_token` |
| `GET /api/evaluate/llm/status` | 查看模型配置 | `configured=true`、`api_key_present=true`，模型与地址正确 |
| `POST /api/evaluate/llm/ping` | 从服务器真实调用模型 | 响应体 `success=true`，有模型返回内容 |

`/llm/status` 和 `/llm/ping` 都需要请求头 `Authorization: Bearer <登录令牌>`。
`/llm/ping` 的 JSON 请求体示例：

```json
{"text":"请确认已理解输入：服务器模型连通性验证。"}
```

**HTTP 200 不代表模型调用成功。** 必须检查响应体的 `success`。
`configured=true` 只说明密钥已设置，不能证明密钥有效或模型可用。

可在 [Swagger 接口文档](http://10.60.150.179:8000/docs) 登录并 Authorize 后依次验证。
开发机也可通过 SSH 隧道执行验证：

```bash
ssh -N -L 127.0.0.1:18000:127.0.0.1:8000 worker8
```

随后访问 `http://127.0.0.1:18000/docs`。完整认证 API 见服务器 `/opt/aiassist/README.md`。

模型调用通过后，还需验证公共模型客户端的 JSON 调用，以及一个 `use_llm=true` 的真实审查任务，
确认任务成功且结果包含成功的模型复核，最后清理临时账号和项目。

## 2026-10-21 初始检查

配置前，服务器配置和运行容器中的 `DEEPSEEK_API_KEY` 均为空；本机项目 `backend/.env` 中也未设置密钥。
现有服务运行正常。此节记录配置前的检查，不代表配置后的状态。
初始检查结果见 `output/api-key-baseline-20261021.json` 和
`output/api-key-business-baseline-20261021.json`。

## 复现模型及审查验证

2026-10-21 配置后的验证已通过：

- 运行容器中的四个模型变量与服务器配置文件一致，后端健康状态为 `healthy`。
- 登录后的 `/api/evaluate/llm/ping` 返回 `success=true`。
- 公共模型客户端真实调用并成功解析 JSON。
- `use_llm=true` 异步资源审查完成，3 项模型复核成功，0 项错误，结果可从数据库查询。
- 临时项目、审查结果、账号及会话已清理；退出后的令牌返回 `401`。

配置前的备份位于服务器 `/var/backups/aiassist/before-llm-20261021T111330Z/backend.env`，仅 root 可访问。

从本机仓库根目录执行：

```bash
ssh worker8 'docker exec -i aiassist-backend python -' < scripts/verify_server_llm.py
```

脚本通过服务器内部 HTTP API 验证注册登录、模型状态、真实模型 ping、公共客户端 JSON 调用、
`use_llm=true` 的异步资源审查、结果持久化和会话撤销，结束后删除其创建的临时项目、账号及会话。
脚本会实际调用模型，每次运行消耗少量模型额度和注册限流额度。
报告不包含密钥、密码或登录令牌。当前运行结果保存在 `output/api-key-verification-20261021.json`。
