# 2026-11-02 服务器历史传输 API 部署记录

已部署至 `10.60.150.179`，北京时间 2026-11-02 完成。接口地址仍为 `http://10.60.150.179:8000/api`，Swagger 为 `http://10.60.150.179:8000/docs`。

## 当前运行配置

| 项目 | 值 |
| --- | --- |
| 服务／容器 | `aiassist-api.service` / `aiassist-backend` |
| 镜像 | `aiassist-backend:history-20261102` |
| 镜像 ID | `sha256:4b0eff626b4a36e11d89b0c8243e5b49c08c3c9825f263d5ec3bac649b2cf054` |
| 模式 | `BACKEND_MODE=server` |
| 数据库 | 独立 MySQL，`127.0.0.1:3307`，库名 `aiassist` |
| 源码 | `/opt/aiassist/backend/app` |
| 本次发布快照 | `/opt/aiassist/releases/history-20261102` |
| 发布前备份 | `/var/backups/aiassist/before-history-20261102`，仅 root 可访问 |
| 管理员账号 | `aiassist_admin`；密码单独交付，不在源码、报告或文档中 |

镜像使用现有后端镜像作为基础，依赖未变，替换为经核验的应用源码。发布包包含新 API、角色 CLI 和传输编解码模块；未打包本地运行数据库、虚拟环境、日志或凭据。

## 数据与账号

- 新增 `users.role`，原有 5 个账号保持原身份与密码，并作为普通用户保留。
- 按用户要求新建独立管理员 `aiassist_admin`，现在共 6 个账号。
- 新建 `history_uploads`、`history_deliveries`、`history_imports`。
- 发布前后逐表内容校验一致：1 个项目、1 个审查批次、14 条检测结果、9 份原始附件。
- 验证用的两个临时用户、上传项目、分发记录和验证会话已清理；保留操作审计。

## 验证结果

1. 本地相关回归：201 项通过。
2. 从发布前数据库备份建立隔离 MySQL 测试库，完成迁移、上传、分发、越权、附件和回执验证。
3. MySQL `STRICT_TRANS_TABLES`、`REPEATABLE-READ` 下，6 路并发首次上传、6 路并发首次分发均返回成功并仅产生一份记录。
4. 32 MiB 原始附件成功上传；MySQL TEXT 字段 65,535 字节可接收，65,536 字节返回 422。
5. 正式服务器真实注册／登录、上传重试、内容冲突、定向分发、非接收用户拒绝访问、附件完整性和接收回执均通过。
6. 正式 API 服务重启后，原验证会话、上传和分发快照仍可读取；随后验证撤回 410 和精确清理。
7. 从正式服务器领取的包在本机独立 SQLite 测试库导入成功，重试去重，附件哈希和原文预览正常。
8. 本机通过内网地址使用新管理员真实登录，可读取原有项目；OpenAPI 包含全部管理／传输接口。服务最终为 running / healthy。

隔离测试容器、数据库与专用 MySQL 账号已删除。无凭据验证摘要见 `output/history-deployment-20261102.json`；完整调用协议见 [历史传输 API](history_transfer_api.md)。后续 WPF 已完成双后端登录与收件操作，见 [本地接收说明](local_history_receive.md)；上传与专用管理界面可继续接入现有 API。

## 备份与回滚

备份目录包含切换前数据库 `database-before-switch.sql`、初始在线备份 `database.sql`、原配置、原服务文件及旧源码。旧镜像保留为 `aiassist-backend:before-history-20261102`，对应原 `auth-20261020` 镜像。

需要回滚应用时，在维护窗口停止 API，恢复备份中的 `backend.env`、`container.env` 并启动旧镜像；新增表和 role 列可保留。不要直接恢复旧数据库覆盖上线后新增的真实记录。数据库恢复需先备份当前数据并确认恢复范围。

旧镜像没有角色隔离，恢复后普通登录用户可能访问全部业务记录。因此若新版已接收用户私有记录，回滚期间必须继续限制普通用户入口，不能把旧版本当作等价权限替代。
