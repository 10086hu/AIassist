from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import evaluate, projects
from app.core.config import settings
from app.db.session import init_db


app = FastAPI(
    title="可行性报告 AI 评估助手",
    description="模块2/5/8 MVP backend. Week 1-2 implements project skeleton and internal duplicate check.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(projects.router, prefix="/api/projects", tags=["projects"])
app.include_router(evaluate.router, prefix="/api/evaluate", tags=["evaluate"])
