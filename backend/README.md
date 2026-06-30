# 后端服务

本目录是可研报告智能审查助手的服务器端后端服务。后端应部署在服务器上，为 Windows WPF 桌面客户端提供接口服务；数据库也应部署在服务器上，不应依赖用户本机数据库作为正式数据源。

## 技术栈

```text
Python
FastAPI
SQLAlchemy
```

## 服务职责

后端服务主要负责：

- 用户、项目、审查任务等业务接口
- 可研报告及相关材料的接收与解析
- 九项审查能力的任务创建和调度
- 重复建设检查、规则检查、模型检查等审查逻辑
- 审查结果、任务状态和操作记录的持久化
- 向 Windows WPF 客户端返回任务状态和审查结果

## 数据库

生产环境数据库应部署在服务器上，建议使用：

```text
MySQL / PostgreSQL / SQL Server
```

当前默认配置中的 SQLite 仅用于本地开发调试：

```text
DATABASE_URL=sqlite:///./data/app.db
```

服务器环境应通过环境变量或服务器 `.env` 文件配置真实数据库连接，例如：

```text
DATABASE_URL=mysql+pymysql://用户名:密码@数据库服务器地址:3306/数据库名
```

数据库密码、接口密钥、模型密钥等敏感信息不得提交到 Git 仓库。

## 本地开发启动

进入后端目录：

```powershell
cd backend
```

安装依赖：

```powershell
pip install -r requirements.txt
```

启动开发服务：

```powershell
uvicorn app.main:app --reload
```

本地启动仅用于开发调试。正式使用时，Windows WPF 客户端应访问服务器上的后端地址。

## 当前接口

当前已保留的接口包括：

```text
GET  /api/health
POST /api/projects
GET  /api/projects
POST /api/evaluate/duplicate/internal
```

后续应继续补充登录认证、任务管理、文件上传、结果查询和报告导出等接口。
