# 管理员与用户客户端：审查历史传输 API

本协议供后续两种客户端接入。服务器保存集中管理数据，用户客户端通过本地后端保存 SQLite 数据。客户端通过 HTTP API 交换记录，不直接连接 MySQL。

## 身份、运行模式与升级

- 账号角色为 `admin` 或 `user`。注册只创建 `user`，请求中传入 `role` 会被拒绝；登录和 `/api/auth/me` 返回真实角色。权限由后端校验，不能通过换用管理员界面获得权限。
- 服务器设置 `BACKEND_MODE=server`；本地后端设置 `BACKEND_MODE=local`。未设置时，`APP_ENV=production` 默认为 server，其余为 local。生产部署必须确认模式为 server。
- server 模式下，现有 `/api/projects/*`、`/api/evaluate/*`、`/api/completeness/*` 统一管理接口仅管理员可访问。用户使用自己的上传列表和定向收件箱，不能通过旧接口绕过隔离。
- local 模式下，已登录的本地账号可以审查、查看、导出、导入本机记录。本地数据库是同一台电脑的共享工作区，不提供多个本地账号之间的数据隔离。
- 服务器账号令牌与本地账号令牌必须分开保存和使用。两端账号不必具有相同 UUID，上传者和接收者均以服务器实际登录账号为准。
- 升级时自动新增 `users.role` 和三张传输表。已有账号均保留为普通用户，不自动提权。部署前备份数据库；新版启动完成迁移后，用同一数据库环境执行下面命令指定管理员，然后重新登录：

```bash
# 在 backend 目录中，使用对应后端的 Python 和数据库环境
python -m app.cli.manage_admin 已注册的用户名 --role admin

# 已更新的服务器容器中也可以执行
docker exec aiassist-backend python -m app.cli.manage_admin 已注册的用户名 --role admin
```

降级使用 `--role user`；角色变化撤销该账号旧会话。不能降级最后一名启用的管理员。管理 CLI 不创建账号，也不打印凭据。

**兼容性变化：** 旧服务器“所有登录用户共享全部记录”的行为已被上述角色隔离替代。WPF 已接入真实登录和双后端接收：审查记录 → 服务器收件箱 → 保存到本地，支持原文预览和原始文件另存，见 [本地接收说明](local_history_receive.md)。管理员分发、用户上传等接口可供后续专用客户端界面接入。

## 接口表

以下地址均相对于 `/api`，全部携带 `Authorization: Bearer <该后端的令牌>`。列表使用 `offset`（默认 0）、`limit`（默认 50，最大 100）。数据库管理限定为业务数据管理，不提供任意 SQL 执行接口。

| 方法与路径 | 权限／位置 | 用途 |
| --- | --- | --- |
| `GET /auth/me` | 登录用户 | 查询当前账号和角色 |
| `GET /admin/users` | 管理员 | 分页查找接收用户；支持 `q`、`role`、`is_active` |
| `PATCH /admin/users/{user_id}` | 管理员 | `{"is_active":false}` 停用普通用户，true 启用；停用撤销会话；管理员须先由 CLI 降级才能停用 |
| `GET /history/exports/{project_id}` | 本地登录用户／服务器管理员 | 导出项目，可用 `check_run_id` 选择一个真实审查批次 |
| `POST /history/uploads` | 服务器登录用户 | 上传本地历史，关联实际上传账号并写入服务器业务表 |
| `GET /history/uploads` | 服务器登录用户 | 仅列出自己的上传回执 |
| `GET /admin/history/uploads` | 服务器管理员 | 列出所有上传；可按 `user_id` 筛选 |
| `GET /admin/history/uploads/{id}/package` | 服务器管理员 | 读取上传时的原始不可变传输包 |
| `DELETE /admin/history/uploads/{id}` | 服务器管理员 | 删除该上传及其导入项目、结果、附件；已发出的分发快照保留 |
| `POST /admin/history/deliveries` | 服务器管理员 | 按接收用户分发选定项目／批次的快照 |
| `GET /admin/history/deliveries` | 服务器管理员 | 查看分发及接收状态，可按 `recipient_user_id` 筛选 |
| `POST /admin/history/deliveries/{id}/revoke` | 服务器管理员 | 撤回分发，阻止后续下载和接收确认 |
| `GET /history/inbox` | 服务器登录用户 | 仅列出发给自己的记录；支持 `status=pending/received/revoked` |
| `GET /history/inbox/{id}/package` | 对应接收用户 | 下载分发快照，返回 `X-Package-SHA256` 校验值 |
| `POST /history/imports` | 本地登录用户 | 把领取的包写入本地 SQLite 并保存导入回执 |
| `POST /history/inbox/{id}/ack` | 服务器对应接收用户 | 本地导入成功后确认接收，状态变为 received |

服务器管理员通过原有 `/projects/tree`、结果详情、附件、项目／批次删除 API 统一管理原生记录和用户上传的记录。上传响应中的 `project_id` 是导入后的服务器项目 ID。

## 用户上传流程

1. 使用本地令牌调用 `GET 本地/api/history/exports/{project_id}`，可指定 `check_run_id`。
2. 为这份审查版本生成并持久保存一个 UUID `client_record_id`。重试必须复用该编号和相同数据包。
3. 使用服务器令牌提交：

```json
{
  "client_record_id": "同一份上传重试时保持不变的UUID",
  "package": "第一步返回的完整JSON对象，实际请求中不是字符串"
}
```

4. `POST 服务器/api/history/uploads` 返回 `id`、`user_id`、`client_record_id`、`project_id`、`package_sha256`、`created_at`、`duplicate`。成功表示回执及全部业务数据已经在同一事务中提交。
5. 管理员从上传列表查询记录，再根据 `project_id` 查看结果或进行分发。

相同上传者＋相同 `client_record_id`＋相同内容返回既有回执（`duplicate=true`）。相同编号换内容返回 409；新的审查版本使用新的编号。不同用户的来源 UUID 不会相互覆盖。服务端为导入副本生成隔离后的 UUID，并更新包内结构化关联和附件路径；原始包保留在上传回执中。每次独立上传形成一个独立项目副本，不按项目名称自动合并。

## 管理员分发、用户领取流程

1. 管理员通过 `/admin/users` 找到接收用户 ID，再提交：

```json
{
  "recipient_user_id": "接收用户的服务器账号UUID",
  "project_id": "服务器项目UUID",
  "check_run_id": "可选：服务器审查批次UUID"
}
```

2. `POST /admin/history/deliveries` 将当前数据保存成不可变快照并进入接收用户收件箱。接收用户不必在线；响应含分发 `id`、`package_sha256`、`status=pending`。
3. 用户客户端登录服务器后轮询 `/history/inbox?status=pending`，下载 `/history/inbox/{id}/package`。下载本身不算确认接收。
4. 使用本地令牌提交 `POST 本地/api/history/imports`：

```json
{
  "delivery_id": "服务器分发UUID",
  "package_sha256": "下载响应头X-Package-SHA256或收件箱中的64位校验值",
  "package": "下载得到的完整JSON对象，实际请求中不是字符串"
}
```

5. 本地接口提交成功后，客户端使用服务器令牌调用 `POST /history/inbox/{id}/ack`，请求体为 `{"package_sha256":"同一校验值"}`。管理员可看到 `received_at`。
6. 本地导入成功而网络中断时，重试导入会返回既有回执，然后重新确认接收；不必重新审查。

同一用户、同一快照重复分发返回既有分发，不产生重复收件。撤回后重新分发同一快照会重新开放该收件。撤回只限制未来下载，不能远程删除已经保存到用户电脑的记录。当前按**用户账号**分发，接收状态也是账号级；尚未引入设备注册或逐设备回执。同一账号可以从另一台已登录设备下载尚未撤回的记录。

## 传输包与存储

`RecordPackage` 采用 `schema_version=1`。完整字段、类型和请求模型可在新版后端 `/docs` 或 `/openapi.json` 查询。

| 包字段 | 内容 |
| --- | --- |
| `project` | 来源项目 UUID、名称、部门、状态和时间 |
| `check_runs` | 来源审查批次、报告名、状态和时间 |
| `results` | 检测结果、JSON 对象形式的 `reference_data`、原始模型和审查时间 |
| `sources` | 原始文件名、关联结果、Base64 二进制、每份文件 SHA-256、时间 |

不传输账号密码、登录令牌或客户端指定的管理员角色。审查结果和报告原文直接复制，不重新调用模型。原有时间保留并规范化为 UTC；原始文本中的 UUID 不进行替换，仅重写明确的结构化 ID 与本包附件 API 路径。

- `history_uploads`：上传者、客户端传输编号、校验值、原始包、导入后的项目 ID。
- `history_deliveries`：管理员、接收者、分发快照、pending/received/revoked 状态及时间。
- `history_imports`：本地导入回执，用于中断后的重复请求处理。
- 业务数据继续写入 `projects`、`check_runs`、`check_results`、`source_artifacts`。
- 上传、导入、分发、撤回、接收、管理删除和账号管理动作写入 `audit_logs`，不记录报告正文或凭据。

每包最多 200 个批次、2000 条结果、500 个附件；附件解码后的总量不超过 32 MiB，规范化 JSON 包不超过 48 MiB，请求体不超过 50 MiB。运行中的批次不能传输。超限时需缩小传输范围；当前没有分片续传或文件去重压缩功能。

## 错误和客户端处理

| 状态码 | 含义／处理 |
| --- | --- |
| 401 | 当前后端令牌无效、过期或账号停用，重新登录 |
| 403 | 角色不足，或把服务器／本地专用请求发到了另一种后端 |
| 404 | 记录不存在，或该收件不属于当前用户 |
| 409 | 同一传输编号内容冲突、目标已有冲突数据、审查尚在运行等；不要直接覆盖 |
| 410 | 管理员已撤回该分发 |
| 413 | 请求／导出范围过大 |
| 422 | 包结构、附件 SHA-256、外键关联、来源链接或整体校验值无效 |

自动重试时复用编号和包，不能每次生成新的编号。界面应区分“已上传”“待领取”“已导入本地但待确认”“已接收”“已撤回”。确认接收只是客户端申报已完成导入，不是服务器对远端磁盘的独立验证。
