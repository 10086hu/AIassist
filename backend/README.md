# 后端 MVP

第一周和第二周已覆盖：

- FastAPI 项目骨架
- SQLite 默认开发库，保留 `DATABASE_URL` 切换能力
- 项目创建/列表/详情 API
- 模块2内部去重 API
- `.xlsx` / `.csv` 功能点清单导入
- 本地确定性 embedding + 相似度候选 + 规则判定兜底

启动：

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

接口：

- `GET /api/health`
- `POST /api/projects`
- `GET /api/projects`
- `POST /api/evaluate/duplicate/internal`
