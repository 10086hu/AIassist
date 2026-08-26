from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

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
    comparison_type: str = "internal"
    item_report_name: Optional[str] = None
    related_report_name: Optional[str] = None
    item_stage: Optional[str] = None
    related_stage: Optional[str] = None
    item_source: Optional[str] = None
    related_source: Optional[str] = None
    model_name: Optional[str] = None


class DuplicateInternalResponse(BaseModel):
    project: ProjectOut
    imported_count: int
    threshold: float
    pairs: List[DuplicatePairOut]
    history_imported_count: int = 0
    internal_pairs: List[DuplicatePairOut] = Field(default_factory=list)
    cross_pairs: List[DuplicatePairOut] = Field(default_factory=list)


class ResourceCheckFindingOut(BaseModel):
    rule_code: str
    rule_name: str
    resource_name: str
    severity: str
    result_label: str
    reason: str
    suggestion: Optional[str] = None
    source_quantities: Dict[str, float] = Field(default_factory=dict)
    row_indexes: List[int] = Field(default_factory=list)
    evidence_examples: List[str] = Field(default_factory=list)
    source_section: str = "资源申请清单"


class ResourceCheckResponse(BaseModel):
    project: ProjectOut
    imported_count: int
    checked_rule_count: int
    findings: List[ResourceCheckFindingOut]
