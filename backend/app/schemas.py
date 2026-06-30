from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    department: Optional[str] = None
    description: Optional[str] = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    department: Optional[str] = None
    description: Optional[str] = None
    status: str
    created_at: datetime
    updated_at: datetime


class FunctionPointOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    name: str
    description: str
    category: Optional[str] = None
    source: str
    row_index: Optional[int] = None


class DuplicatePairOut(BaseModel):
    item_id: str
    related_item_id: str
    item_name: str
    related_item_name: str
    similarity: float
    result_label: str
    severity: str
    reason: str
    suggestion: Optional[str] = None


class DuplicateInternalResponse(BaseModel):
    project: ProjectOut
    imported_count: int
    threshold: float
    pairs: List[DuplicatePairOut]
