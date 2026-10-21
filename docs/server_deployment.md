# AI 助手服务器部署

目标服务器：`worker8`（`root@10.60.150.179`）。

2026-10-20 已部署登录注册功能。服务器连接、认证 API、curl 示例、错误码与验证说明见 [后端 README](../backend/README.md)。所有业务接口均需 `Authorization: Bearer <access_token>`；现有桌面端需要后续接入认证后才能访问业务接口。

## 地址与客户端

- 后端：`http://10.60.150.179:8000`
- 客户端 API 地址：`http://10.60.150.179:8000/api`
- 健康检查：`http://10.60.150.179:8000/api/health`
- 接口文档：`http://10.60.150.179:8000/docs`

Windows 客户端运行仓库根目录的 `run_desktop_server.bat` 即可访问服务器。
也可以设置当前进程或 Windows 用户环境变量 `AIASSIST_BACKEND_URL`，值需包含 `/api`。
未设置此变量时，原来的 `run_desktop.bat` 仍使用本机开发服务。
报告原文件和预览链接与 API 使用同一个服务器地址。

## 服务与目录

| 项目 | 配置 |
| --- | --- |
| 后端源码 | `/opt/aiassist/backend` |
| 部署脚本 | `/opt/aiassist/deploy` |
| 后端服务 | `aiassist-api.service`，Docker 容器 `aiassist-backend` |
| 数据库服务 | `aiassist-mysql.service`，独立 MySQL 8 实例 |
| 数据库监听 | `127.0.0.1:3307`，数据库 `aiassist` |
| 数据库数据 | `/var/lib/aiassist-mysql` |
| 应用缓存 | `/var/lib/aiassist` |
| 应用配置 | `/etc/aiassist/backend.env`，权限 `0600` |
| 容器镜像及用户配置 | `/etc/aiassist/container.env`，权限 `0600` |
| 独立数据库管理员配置 | `/etc/aiassist/mysql-admin.cnf`，权限 `0600` |
| 规则接口 | `http://127.0.0.1:5000/rules`，复用服务器现有规则服务 |

服务器原有的 `aiassist-backend.service` 是规则库服务；本项目的服务名为 `aiassist-api.service`。
部署脚本会拒绝覆盖不带本项目管理标记的同名服务。

后端以无登录权限的 `aiassist` 用户运行，MySQL 使用 `aiassist-db` 用户。
两个服务均设置为开机自启、异常退出后重启；容器移除或重建不删除数据库和应用缓存。
使用单个 Uvicorn 进程，与当前保存在进程内的审查任务状态一致。
MySQL 的报告 JSON 和原文件字段分别使用 `LONGTEXT`、`LONGBLOB`，通信包上限为 `128M`。

## 初次部署

服务器需已安装 Docker、MySQL 8 的 `mysqld` / `mysql` 客户端及 Python 3.10 以上。
以下命令在本机仓库根目录执行；敏感配置由服务器生成，不从本机复制。

```bash
ssh worker8 'install -d -m 755 /opt/aiassist/backend /opt/aiassist/deploy'
rsync -az --exclude '.env*' --exclude '.venv/' --exclude '__pycache__/' \
  --exclude '*.pyc' --exclude '/data/' backend/ worker8:/opt/aiassist/backend/
rsync -az --exclude '__pycache__/' deploy/ worker8:/opt/aiassist/deploy/
ssh worker8 'docker build --pull=false --build-arg PYTHON_IMAGE=python:3.11 \
  -t aiassist-backend:20261019-2238 /opt/aiassist/backend'
ssh worker8 '/root/miniconda3/bin/python /opt/aiassist/deploy/initialize_server.py \
  --image aiassist-backend:20261019-2238'
```

本次使用服务器已缓存的 `python:3.11` 基础镜像。Dockerfile 的默认基础镜像仍为 `python:3.12-slim`。
初始化脚本只初始化独立数据目录，并保留已有 `backend.env`；不会连接原有的 `3306` 数据库实例。

## 更新与维护

更新源码后，用新标签构建镜像，再执行初始化脚本并传入新标签；脚本会保留数据库和模型配置。
需要回滚时，向同一脚本传入服务器仍保留的旧镜像标签。

```bash
ssh worker8 'systemctl status aiassist-api aiassist-mysql --no-pager'
ssh worker8 'journalctl -u aiassist-api -n 100 --no-pager'
ssh worker8 'systemctl restart aiassist-api'
ssh worker8 'docker inspect aiassist-backend --format "{{.State.Health.Status}}"'
```

2026-10-21 已配置同济模型网关与 `DeepSeek-V4-Flash`，真实模型调用及开启模型的异步资源审查均验证通过。
模型配置、验证报告及复现步骤见 [服务器模型配置与验证](server_api_key.md)。
初次初始化时大模型 API Key 为空，规则审查、文件处理和持久化可以独立使用。
启用模型时，在服务器编辑 `/etc/aiassist/backend.env` 的 `DEEPSEEK_API_KEY`、
`DEEPSEEK_MODEL`、`DEEPSEEK_API_URL` 和 `DEEPSEEK_API_BASE_URL`，然后重启后端。
两个 API 地址应与提供方接口保持一致。Windows 用户环境变量中的模型密钥不会自动上传到服务器。

## 从本机测试

```bash
curl --noproxy '*' http://10.60.150.179:8000/api/health
# 先调用 /api/auth/login，使用返回的 access_token
AIASSIST_ACCESS_TOKEN='<登录返回的令牌>' backend/.venv/bin/python scripts/smoke_server_backend.py
```

测试覆盖规则查询、中文项目读写、大于 64 KiB 的 DOCX 上传与原文件下载、
重复请求幂等、报告导出、异步审查任务和项目历史。默认删除本次创建的测试项目。
若需要检查重启后的持久化：

```bash
backend/.venv/bin/python scripts/smoke_server_backend.py \
  --keep --record /tmp/aiassist-smoke.json
ssh worker8 'systemctl restart aiassist-api'
backend/.venv/bin/python scripts/smoke_server_backend.py \
  --verify-record /tmp/aiassist-smoke.json
```

第二步等待健康检查恢复后再执行最后一步；验证完成会删除测试项目。
以上持久化验证命令也需要设置 `AIASSIST_ACCESS_TOKEN`；会话由 MySQL 保存，后端重启后仍有效。
此测试从本机直接访问服务器，不依赖 SSH 端口转发。

价格核价还需补充 `backend/app/modules/price/data/shanghai_municipal_class2_fees.json`。
当前仓库不包含该标准文件，二类费用核价会明确返回配置缺失，规则查询会标明标准缺失；测试将此项记录为 `requires_configuration`。软硬件价格参考可独立运行。
标准文件应来自项目原有标准资料，补齐后重新构建并重启后端。
