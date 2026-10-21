"""Request and response models."""

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator


class ColumnRef(BaseModel):
    table: str
    column: str


class DatabaseIn(BaseModel):
    path: str
    name: str | None = None


class DatabaseOut(BaseModel):
    id: int
    name: str
    path: str
    masked_columns: list[ColumnRef]
    created_at: datetime


class RunIn(BaseModel):
    step: Literal["parse", "profile", "graph", "all"]


class RunOut(BaseModel):
    id: int
    step: str
    status: Literal["queued", "running", "done", "error"]
    started_at: datetime | None
    finished_at: datetime | None
    duration_s: float | None
    error: str | None
    has_result: bool


class MaskingIn(BaseModel):
    columns: list[ColumnRef]


class RelationshipOut(BaseModel):
    id: int
    from_table: str
    from_columns: list[str]
    to_table: str
    to_columns: list[str]
    source: Literal["declared", "inferred", "manual"]
    status: Literal["proposed", "accepted", "rejected"]
    confidence: float
    cardinality: str | None
    note: str | None


class StatusIn(BaseModel):
    status: Literal["accepted", "rejected"]


class BulkAcceptIn(BaseModel):
    min_confidence: float = Field(ge=0, le=1)


class LogEntry(BaseModel):
    id: int
    relationship_id: int
    action: str
    detail: str
    actor: str
    at: datetime


class PathStep(BaseModel):
    from_table: str
    from_columns: list[str]
    to_table: str
    to_columns: list[str]
    source: str
    status: str
    confidence: float


class RelationshipIn(BaseModel):
    from_table: str
    from_columns: list[str]
    to_table: str
    to_columns: list[str]
    cardinality: Literal["1:1", "1:N", "N:M"] | None = None
    note: str | None = None

    @model_validator(mode="after")
    def check_column_pairs(self) -> Self:
        if not self.from_columns or len(self.from_columns) != len(self.to_columns):
            raise ValueError("from_columns and to_columns must be non-empty and of equal length")
        return self


class RelationshipCheck(BaseModel):
    coverage: float
    from_types: list[str | None]
    to_types: list[str | None]
    warnings: list[str]
