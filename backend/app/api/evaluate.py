from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.duplicate.service import (
    run_internal_duplicate_check,
    run_duplicate_check_from_document,
)
from app.schemas import DuplicateInternalResponse


router = APIRouter()


@router.post("/duplicate/internal", response_model=DuplicateInternalResponse)
async def evaluate_duplicate_internal(
    file: UploadFile = File(...),
    project_id: str | None = Form(default=None),
    project_name: str = Form(default="未命名可研项目"),
    department: str | None = Form(default=None),
    db: Session = Depends(get_db),
) -> DuplicateInternalResponse:
    """
    支持 Excel/Word/PDF 多格式输入的内部去重检查

    自动检测文件格式：
    - .xlsx / .csv → 直接解析为功能点
    - .docx / .pdf → 先用 LLM 提取功能点，再去重判定
    """
    filename = file.filename or ""
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传的文件为空")

    try:
        # 根据文件扩展名选择处理方式
        if filename.lower().endswith((".xlsx", ".csv")):
            return run_internal_duplicate_check(
                db=db,
                content=content,
                filename=filename,
                project_id=project_id,
                project_name=project_name,
                department=department,
            )
        elif filename.lower().endswith((".docx", ".pdf")):
            return run_duplicate_check_from_document(
                db=db,
                content=content,
                filename=filename,
                project_id=project_id,
                project_name=project_name,
                department=department,
            )
        else:
            raise HTTPException(
                status_code=400,
                detail="不支持的文件格式。支持：.xlsx, .csv, .docx, .pdf",
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{project_id}/resource")
def evaluate_resource(project_id: str, db: Session = Depends(get_db)) -> dict[str, object]:
    # Week 1 skeleton endpoint. Module 5 rules/RAG/LLM evaluation starts in week 4.
    return {
        "project_id": project_id,
        "module": "resource",
        "status": "not_implemented",
        "message": "资源申请合理性检查接口已预留，当前冲刺先交付模块2内部去重。",
    }


@router.post("/{project_id}/price")
def evaluate_price(project_id: str, db: Session = Depends(get_db)) -> dict[str, object]:
    # Week 1 skeleton endpoint. Module 8 benchmark import/matching starts after module 5.
    return {
        "project_id": project_id,
        "module": "price",
        "status": "not_implemented",
        "message": "软硬件产品价格参考接口已预留，当前冲刺先交付模块2内部去重。",
    }
