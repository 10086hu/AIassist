# AI Assist 后端：服务器连接与登录注册 API

后端已部署在 `10.60.150.179`，使用 FastAPI、SQLAlchemy 和服务器上的独立 MySQL 8。2026-10-20 已完成登录注册功能的部署和验证。本文同时保存在服务器 `/opt/aiassist/README.md` 和 `/opt/aiassist/backend/README.md`。

## 1. 连接服务器

需要处于能够访问 `10.60.150.179` 的内网或 VPN。

| 用途 | 地址 / 命令 |
| --- | --- |
| SSH | `ssh root@10.60.150.179`，端口 `22` |
| 当前开发机 SSH 别名 | `ssh worker8` |
| 指定当前开发机上的私钥 | `ssh -i ~/.ssh/id_ed25519_worker8 root@10.60.150.179` |
| API 根地址 | `http://10.60.150.179:8000/api` |
| Swagger 接口文档 | <http://10.60.150.179:8000/docs> |
| OpenAPI JSON | <http://10.60.150.179:8000/openapi.json> |
| 健康检查，无需登录 | <http://10.60.150.179:8000/api/health> |

SSH 需要已授权的密钥；`worker8` 别名和上述私钥只存在于当前开发机，其他电脑需自行配置授权密钥。README 不包含 SSH 密码、私钥或数据库密码。

当前后端使用内网 HTTP。需要加密传输账号、密码与令牌时，可在本机建立 SSH 隧道：

```bash
# 保持该窗口运行；需要指定私钥时增加 -i 私钥路径
ssh -N -L 127.0.0.1:18000:127.0.0.1:8000 root@10.60.150.179
```

随后使用 `http://127.0.0.1:18000/api` 和 <http://127.0.0.1:18000/docs>。流量通过 SSH 加密。内网直连和 SSH 隧道均已验证。

## 2. 认证与权限

- 注册开放，注册成功后需再调用登录接口。
- 登录接收 JSON，返回不透明的 Bearer 令牌；令牌不是 JWT。
- 业务请求携带 `Authorization: Bearer <access_token>`；不接受 URL 参数令牌，不使用 Cookie。
- 会话默认有效期 24 小时；过期后重新登录。每次登录创建独立会话，支持多设备同时使用。
- 退出当前会话立即撤销当前令牌；退出全部会话或改密会撤销该账号全部令牌。
- `/api/projects/*`、`/api/evaluate/*`、`/api/completeness/*` 的所有业务接口均需登录，包括文件下载、预览和报告导出。
- 注册、登录、`/api/health`、`/docs`、`/openapi.json` 允许匿名访问。
- **业务数据是共享工作空间：所有已注册且启用的用户，登录后可访问同一组项目、报告与任务。当前没有按账号隔离项目，也没有管理员/普通用户角色划分。**
- 当前不包含短信、邮箱验证或忘记密码找回流程。

Swagger 操作：先在 `auth` 中注册、登录，复制响应的 `access_token`，点击右上角 **Authorize**，仅粘贴令牌值，然后调用业务接口。

## 3. 接口清单

以下路径相对于 API 根地址 `/api`。请求体均为 `application/json`。

| 方法 | 路径 | 需要登录 | 成功响应 | 用途 |
| --- | --- | --- | --- | --- |
| POST | `/auth/register` | 否 | `201` 用户信息 | 注册 |
| POST | `/auth/login` | 否 | `200` 令牌和用户信息 | 登录 |
| GET | `/auth/me` | 是 | `200` 用户信息 | 当前用户 |
| POST | `/auth/logout` | 是 | `204` 空响应 | 退出当前会话 |
| POST | `/auth/logout-all` | 是 | `204` 空响应 | 退出全部会话 |
| PUT | `/auth/password` | 是 | `204` 空响应 | 改密并退出全部会话 |

### 注册：POST /api/auth/register

```json
{
  "username": "demo_user",
  "password": "Replace-With-Your-Passphrase-2026!",
  "display_name": "测试用户"
}
```

| 字段 | 必填 | 规则 |
| --- | --- | --- |
| `username` | 是 | 3–32 个 ASCII 字符；首字符为字母或数字，其余允许字母、数字、`_`、`.`、`-`；去除首尾空格并转小写，大小写视为同一账号 |
| `password` | 是 | 12–128 个字符，不能全为空白；支持中文和其他 Unicode；密码的首尾空格会保留 |
| `display_name` | 否 | 去除首尾空格后为 1–64 个字符；省略或为 `null` 时使用用户名 |

不接受额外字段，客户端不能设置 `is_active` 或角色。成功响应示例：

```json
{
  "id": "用户 UUID",
  "username": "demo_user",
  "display_name": "测试用户",
  "is_active": true,
  "created_at": "2026-10-20T08:00:00Z",
  "last_login_at": null
}
```

重名返回 `409`，校验失败返回 `422`。时间均为 UTC 的 ISO 8601 格式，客户端可转换为北京时间。

### 登录：POST /api/auth/login

```json
{
  "username": "demo_user",
  "password": "Replace-With-Your-Passphrase-2026!"
}
```

成功响应示例，使用实际响应中的令牌：

```json
{
  "access_token": "登录返回的43字符随机令牌",
  "token_type": "bearer",
  "expires_in": 86400,
  "expires_at": "2026-10-21T08:00:00Z",
  "user": {
    "id": "用户 UUID",
    "username": "demo_user",
    "display_name": "测试用户",
    "is_active": true,
    "created_at": "2026-10-20T08:00:00Z",
    "last_login_at": "2026-10-20T08:00:00Z"
  }
}
```

未知用户名、错误密码、停用账号统一返回 `401` 和 `{"detail":"用户名或密码错误"}`。`expires_in` 单位为秒，不提供自动续期或刷新令牌接口。

### 当前用户、退出和改密

```http
GET /api/auth/me
Authorization: Bearer <access_token>
```

返回与注册相同结构的用户信息，不包含密码与密码哈希。

```http
POST /api/auth/logout
Authorization: Bearer <access_token>
```

成功返回 `204`，没有 JSON 响应体。当前令牌随后返回 `401`，其他设备会话仍有效。`POST /api/auth/logout-all` 请求方式相同，但撤销全部会话。

```http
PUT /api/auth/password
Content-Type: application/json
Authorization: Bearer <access_token>

{
  "current_password": "Replace-With-Your-Passphrase-2026!",
  "new_password": "Replace-With-A-Different-Passphrase-2026!"
}
```

新密码遵循注册规则。当前密码错误或新旧密码相同返回 `400`。成功返回 `204`，包括当前会话在内的全部会话失效，需用新密码登录。

## 4. 完整 curl 示例

以下为 Bash 示例。请替换示例用户名、密码；已有账号可跳过注册。

```bash
# 使用 SSH 隧道时改成 http://127.0.0.1:18000/api
BASE=http://10.60.150.179:8000/api

# 注册
curl --noproxy '*' -fsS "$BASE/auth/register" \
  -H 'Content-Type: application/json' \
  -d '{"username":"demo_user","password":"Replace-With-Your-Passphrase-2026!","display_name":"测试用户"}'

# 登录并提取令牌，需要本机有 Python 3
TOKEN=$(curl --noproxy '*' -fsS "$BASE/auth/login" \
  -H 'Content-Type: application/json' \
  -d '{"username":"demo_user","password":"Replace-With-Your-Passphrase-2026!"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')

# 当前用户
curl --noproxy '*' -fsS "$BASE/auth/me" -H "Authorization: Bearer $TOKEN"

# 创建和查询项目
curl --noproxy '*' -fsS "$BASE/projects" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"登录后的测试项目","department":"测试部门"}'
curl --noproxy '*' -fsS "$BASE/projects" -H "Authorization: Bearer $TOKEN"

# 退出，返回 204
curl --noproxy '*' -i -X POST "$BASE/auth/logout" -H "Authorization: Bearer $TOKEN"

# 退出后再次访问，返回 401
curl --noproxy '*' -i "$BASE/auth/me" -H "Authorization: Bearer $TOKEN"
unset TOKEN
```

客户端需为所有业务请求统一加上认证头。浏览器直接打开文件或导出链接不会自动携带 Bearer 头，客户端需要通过带认证的 HTTP 请求获取文件。

## 5. 错误码与限流

错误主体为 `{"detail":"说明"}`。`422` 的 `detail` 是字段错误数组，只返回 `loc`、`msg`、`type`，不包含原始密码或请求内容。

| 状态码 | 含义 |
| --- | --- |
| `200` / `201` / `204` | 查询或登录成功 / 注册成功 / 退出或改密成功 |
| `400` | 当前密码错误，或新旧密码相同 |
| `401` | 未登录、错误凭证、伪造/过期/已撤销令牌，或停用账号 |
| `409` | 用户名重复 |
| `422` | 字段缺失、格式错误、密码长度不符或额外字段 |
| `429` | 请求过多；`Retry-After` 响应头是需等待的秒数 |

固定时间窗口限流，成功和失败请求均计数：

- 注册：每 IP 每小时 10 次。
- 登录：每 IP 每 5 分钟 30 次；每用户名每 5 分钟 10 次。
- 改密：每 IP 每 5 分钟 30 次；每用户每 5 分钟 10 次。

计数存于 MySQL，重启后仍保留，过期窗口自动清理。应用不直接信任任意 `X-Forwarded-For`，代理来源由 Uvicorn 的受信任代理配置决定。

## 6. 数据库与维护

| 项目 | 当前配置 |
| --- | --- |
| 后端源码 / 部署脚本 | `/opt/aiassist/backend` / `/opt/aiassist/deploy` |
| 后端服务 | `aiassist-api.service` |
| Docker 容器 / 镜像 | `aiassist-backend` / `aiassist-backend:auth-20261020` |
| 数据库服务 | `aiassist-mysql.service` |
| MySQL | `8.0.46`，监听 `127.0.0.1:3307`，库名 `aiassist` |
| 数据库数据 / 应用缓存 | `/var/lib/aiassist-mysql` / `/var/lib/aiassist` |
| 应用配置，0600 | `/etc/aiassist/backend.env` |
| 镜像与运行用户配置，0600 | `/etc/aiassist/container.env` |
| 数据库管理员配置，0600 | `/etc/aiassist/mysql-admin.cnf` |
| 更新前备份，0700 | `/var/backups/aiassist/before-auth-20261020` |

复用现有独立 MySQL，启动时自动创建 `users`、`auth_sessions`、`auth_rate_limits`，保留业务数据。密码采用随机盐 scrypt（N=32768、r=8、p=3，输出 32 字节）。数据库只保存令牌的 SHA-256 摘要，原始令牌仅在登录时返回。认证响应设置 `Cache-Control: no-store`。

`/etc/aiassist/backend.env` 相关配置示例：

```dotenv
APP_ENV=production
AUTH_SESSION_HOURS=24
DATABASE_URL=mysql+pymysql://<应用账号>:<服务器上的密码>@127.0.0.1:3307/aiassist?charset=utf8mb4
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

会话小时数使用整数，范围 1–720，超出后取边界值；修改配置、重启后仅影响新登录会话。Web 前端使用其他来源时需加入 `CORS_ORIGINS` 并重启。原生桌面 HTTP 请求不受浏览器 CORS 限制。

后端、独立数据库均开机自启。原有 `aiassist-backend.service` 是规则库服务；管理本项目时应使用 `aiassist-api.service`。

在服务器执行：

```bash
systemctl status aiassist-api.service aiassist-mysql.service --no-pager
journalctl -u aiassist-api.service -n 100 --no-pager
docker logs --tail 100 aiassist-backend
systemctl restart aiassist-api.service
docker inspect aiassist-backend --format '{{.State.Health.Status}}'

# 使用私有客户端配置连接，不在命令中填写密码
mysql --defaults-file=/etc/aiassist/mysql-admin.cnf aiassist
```

MySQL 交互会话内查询账号或停用指定账号：

```sql
SELECT id, username, display_name, is_active, created_at, last_login_at FROM users;

-- 将 demo_user 替换为目标用户名
START TRANSACTION;
UPDATE users SET is_active = 0 WHERE username = 'demo_user';
DELETE s FROM auth_sessions s JOIN users u ON u.id = s.user_id WHERE u.username = 'demo_user';
COMMIT;
```

其他电脑需要数据库连接时，可用 `ssh -N -L 127.0.0.1:13307:127.0.0.1:3307 root@10.60.150.179` 转发，再用已授权的数据库账号连接本机 `13307`。密码从服务器私有配置取得，不写入 README。

完整安装、镜像构建和回滚见 [服务器部署文档](../docs/server_deployment.md)。已备份更新前源码、数据库和配置，旧镜像为 `aiassist-backend:20261019-2238`。旧版本没有认证，回滚应在受控维护期间进行。

## 7. 验证与复现

本次已完成：

- 23 项认证测试，覆盖重名、校验脱敏、错误凭证、伪造/过期令牌、停用账号、退出、改密、并发注册、限流与全部业务路径的认证声明。
- 原有完整性检查、项目历史、异步重复检查和来源定位回归测试。
- 从本机访问服务器，验证项目读写、约 129 KB DOCX 上传下载、幂等上传、导出、异步审查与项目历史。
- MySQL 50 次并发计数，准确放行 20 次、限制 30 次；确认密码和会话只保存哈希。
- 重启后端及独立 MySQL，确认原会话和账号密码持久化，退出全部会话后原令牌失效。
- 清理本次临时项目、测试账号与会话；没有预置通用账号或默认密码。

本地测试（需要 pytest、httpx 及后端依赖）：

```bash
cd backend
python -m pytest tests/test_auth.py -q
```

在仓库根目录执行服务器认证测试，需要 `requests`：

```bash
python scripts/smoke_server_auth.py --record output/auth-check.json
```

脚本创建唯一的 `auth_smoke_*` 测试账号，结束时撤销会话，账号 ID 写入报告供管理员清理。每次执行消耗限流额度，不要短时间反复执行。

复现重启验证：

```bash
python scripts/smoke_server_auth.py --keep \
  --state-file /tmp/aiassist-auth-state.json --record output/auth-check.json
ssh worker8 'systemctl stop aiassist-api; systemctl restart aiassist-mysql; systemctl start aiassist-api'
# 等健康检查恢复后执行
python scripts/smoke_server_auth.py --verify-state /tmp/aiassist-auth-state.json \
  --record output/auth-restart-check.json
```

临时状态文件以 `0600` 保存测试凭证，验证后自动删除。须保存在仓库之外，不能提交 Git。业务验证脚本 `scripts/smoke_server_backend.py` 需要先登录，并通过 `AIASSIST_ACCESS_TOKEN` 环境变量提供令牌。

本次未涉及桌面端接入。原有价格标准 JSON 和模型 API Key 仍需按部署文档补充；对应配置缺项不影响登录注册。

密码参数依据：[OWASP Password Storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html#scrypt)。实现使用 [Python hashlib.scrypt](https://docs.python.org/3.12/library/hashlib.html#hashlib.scrypt)。长报告流程见 [长报告重复建设检查](../docs/duplicate_long_report_review.md)。
