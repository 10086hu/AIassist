from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import JSONResponse

from app.api import auth, evaluate, projects, completeness
from app.core.auth import get_current_user
from app.core.config import settings
from app.db.session import init_db


app = FastAPI(
    title="可行性报告 AI 评估助手",
    description="可研报告智能审查后端。先通过 /api/auth/login 登录，再使用 Bearer 令牌访问业务接口。",
    version="0.2.0",
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


@app.middleware("http")
async def auth_cache_control(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/auth/") or request.headers.get("authorization"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
    return response


@app.exception_handler(RequestValidationError)
async def safe_validation_error(request: Request, exc: RequestValidationError):
    if request.url.path.startswith("/api/auth/"):
        # Pydantic's default error includes the rejected input, which could be a
        # plaintext password. Keep useful field diagnostics without that value.
        return JSONResponse(status_code=422, content={"detail": [
            {key: error[key] for key in ("loc", "msg", "type")} for error in exc.errors()
        ]})
    return await request_validation_exception_handler(request, exc)


app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
protected = [Depends(get_current_user)]
app.include_router(projects.router, prefix="/api/projects", tags=["projects"], dependencies=protected)
app.include_router(evaluate.router, prefix="/api/evaluate", tags=["evaluate"], dependencies=protected)
app.include_router(completeness.router, prefix="/api/completeness", tags=["completeness"], dependencies=protected)
