from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from uuid import uuid4

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_uuid() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    department: Mapped[Optional[str]] = mapped_column(String(200))
    description: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    function_points: Mapped[List[FunctionPoint]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
    )
    resource_items: Mapped[List[ResourceItem]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
    )
    price_items: Mapped[List[PriceItem]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
    )
    check_results: Mapped[List[CheckResult]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
    )


class FunctionPoint(Base):
    __tablename__ = "function_points"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[Optional[str]] = mapped_column(String(100))
    source: Mapped[str] = mapped_column(String(32), default="current")
    row_index: Mapped[Optional[int]] = mapped_column(Integer)
    text_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    project: Mapped[Project] = relationship(back_populates="function_points")


class ResourceItem(Base):
    __tablename__ = "resource_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), index=True)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    spec: Mapped[str] = mapped_column(Text, default="")
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit: Mapped[str] = mapped_column(String(32), default="项")
    justification: Mapped[Optional[str]] = mapped_column(Text)
    is_major_item: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    project: Mapped[Project] = relationship(back_populates="resource_items")


class PriceItem(Base):
    __tablename__ = "price_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), index=True)
    item_name: Mapped[str] = mapped_column(String(200), nullable=False)
    brand: Mapped[Optional[str]] = mapped_column(String(100))
    model: Mapped[Optional[str]] = mapped_column(String(100))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[float] = mapped_column(Float, default=0)
    total_price: Mapped[float] = mapped_column(Float, default=0)
    category: Mapped[Optional[str]] = mapped_column(String(64))
    remark: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    project: Mapped[Project] = relationship(back_populates="price_items")


class PriceBenchmark(Base):
    __tablename__ = "price_benchmarks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(64))
    item_name_std: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    brand: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    model: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    unit_price: Mapped[float] = mapped_column(Float, default=0)
    source_url: Mapped[Optional[str]] = mapped_column(Text)
    source_date: Mapped[Optional[str]] = mapped_column(String(20))
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    is_active: Mapped[int] = mapped_column(Integer, default=1)
    raw_data: Mapped[str] = mapped_column(Text, default="{}")


class KnowledgeDoc(Base):
    __tablename__ = "knowledge_docs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    doc_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[Optional[str]] = mapped_column(String(300))
    indexed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    chunks: Mapped[List[DocChunk]] = relationship(
        back_populates="doc",
        cascade="all, delete-orphan",
    )


class DocChunk(Base):
    __tablename__ = "doc_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    doc_id: Mapped[str] = mapped_column(String(36), ForeignKey("knowledge_docs.id"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(Text, default="")
    embedding_id: Mapped[Optional[str]] = mapped_column(String(100))
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")

    doc: Mapped[KnowledgeDoc] = relationship(back_populates="chunks")


class CheckResult(Base):
    __tablename__ = "check_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), index=True)
    module: Mapped[str] = mapped_column(String(32), nullable=False)
    check_subtype: Mapped[str] = mapped_column(String(64), nullable=False)
    item_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    related_item_id: Mapped[Optional[str]] = mapped_column(String(36), index=True)
    severity: Mapped[str] = mapped_column(String(32), default="warning")
    score: Mapped[Optional[float]] = mapped_column(Float)
    result_label: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    suggestion: Mapped[Optional[str]] = mapped_column(Text)
    reference_data: Mapped[str] = mapped_column(Text, default="{}")
    model_name: Mapped[str] = mapped_column(String(100), default="local-rule-judge")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    project: Mapped[Project] = relationship(back_populates="check_results")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("projects.id"), index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    operator: Mapped[Optional[str]] = mapped_column(String(100))
    module: Mapped[Optional[str]] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

