"""
Canonical schema definitions for the FrontierAtlas Intelligence Graph.

These map 1:1 to the "Expected Schemas" section of the assignment doc.
Every record produced by any scraper/LLM extractor MUST validate against
one of these before it's written to output. Invalid records are dropped
and logged, not coerced or hallucinated into shape.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, HttpUrl, field_validator

SCHEMA_VERSION = "1.0"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PricingModel(str, Enum):
    FREE = "FREE"
    FREEMIUM = "FREEMIUM"
    PAID = "PAID"
    ENTERPRISE = "ENTERPRISE"


class Source(BaseModel):
    name: str
    url: str  # kept as str, not HttpUrl, so we never silently drop a record
    # over a strict URL parse failure -- we validate URLs separately in
    # utils/validators.py so we can log *why* a record was rejected.


class StartupContentData(BaseModel):
    employeeCount: Optional[int] = None


class StartupContent(BaseModel):
    entityName: str
    data: StartupContentData = Field(default_factory=StartupContentData)


class StartupRecord(BaseModel):
    schemaVersion: str = SCHEMA_VERSION
    recordType: str = "STARTUP"
    source: Source
    content: StartupContent
    collectedAt: str = Field(default_factory=utcnow_iso)

    @field_validator("recordType")
    @classmethod
    def _lock_record_type(cls, v):
        return "STARTUP"


class ProductContent(BaseModel):
    startupName: str
    pricingModel: Optional[PricingModel] = None


class ProductRecord(BaseModel):
    schemaVersion: str = SCHEMA_VERSION
    recordType: str = "PRODUCT"
    source: Source
    content: ProductContent
    collectedAt: str = Field(default_factory=utcnow_iso)

    @field_validator("recordType")
    @classmethod
    def _lock_record_type(cls, v):
        return "PRODUCT"


class ResearchPaperContent(BaseModel):
    title: str
    authors: list[str] = Field(default_factory=list)
    paper_url: str
    github_url: Optional[str] = None
    github_stars: Optional[int] = None
    published_date: Optional[str] = None  # ISO-8601


class ResearchPaperRecord(BaseModel):
    schemaVersion: str = SCHEMA_VERSION
    recordType: str = "RESEARCH_PAPER"
    source: Source
    content: ResearchPaperContent
    collectedAt: str = Field(default_factory=utcnow_iso)

    @field_validator("recordType")
    @classmethod
    def _lock_record_type(cls, v):
        return "RESEARCH_PAPER"


class JobContent(BaseModel):
    company: str
    date: Optional[str] = None  # ISO-8601
    is_remote: Optional[bool] = None
    role_family: Optional[str] = None
    title: Optional[str] = None
    url: Optional[str] = None


class JobRecord(BaseModel):
    schemaVersion: str = SCHEMA_VERSION
    recordType: str = "JOB"
    source: Source
    content: JobContent
    collectedAt: str = Field(default_factory=utcnow_iso)

    @field_validator("recordType")
    @classmethod
    def _lock_record_type(cls, v):
        return "JOB"


class NewsContent(BaseModel):
    title: str
    url: str
    published_date: Optional[str] = None
    full_text: Optional[str] = None
    summary: Optional[str] = None


class NewsRecord(BaseModel):
    schemaVersion: str = SCHEMA_VERSION
    recordType: str = "NEWS"
    source: Source
    content: NewsContent
    collectedAt: str = Field(default_factory=utcnow_iso)

    @field_validator("recordType")
    @classmethod
    def _lock_record_type(cls, v):
        return "NEWS"


class EntityMappingLogRecord(BaseModel):
    """One row per raw string -> canonical resolution decision."""
    raw_name: str
    canonical_name: str
    entity_type: str  # "STARTUP" | "PRODUCT"
    method: str  # "exact" | "normalized" | "fuzzy" | "alias" | "unresolved"
    confidence: float
    source_url: Optional[str] = None
    resolvedAt: str = Field(default_factory=utcnow_iso)
