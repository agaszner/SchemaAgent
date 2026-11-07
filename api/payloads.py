"""Request and response models."""

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from core.sql_guard import DEFAULT_MAX_ROWS, DEFAULT_TIMEOUT_S


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
    step: Literal["parse", "profile", "graph", "docs", "all"]


class RunOut(BaseModel):
    id: int
    step: str
    status: Literal["queued", "running", "done", "error"]
    started_at: datetime | None
    finished_at: datetime | None
    duration_s: float | None
    error: str | None
    has_result: bool
    done: int | None  # docs and benchmark progress; NULL for other steps
    total: int | None


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


class AppSettings(BaseModel):
    max_rows: int = Field(DEFAULT_MAX_ROWS, ge=1, le=1000)
    timeout_s: float = Field(DEFAULT_TIMEOUT_S, ge=0.1, le=60)
    llm_model: str = Field("gpt-6-luna", min_length=1)
    reasoning_effort: Literal["none", "low", "medium", "high"] = "low"
    input_usd_per_mtok: float = Field(0.10, ge=0)
    output_usd_per_mtok: float = Field(0.50, ge=0)


class SqlIn(BaseModel):
    sql: str = Field(max_length=20_000)


class ColumnDoc(BaseModel):
    name: str
    description: str
    certainty: Literal["certain", "likely", "uncertain"]


class DocOut(BaseModel):
    table: str
    summary: str
    columns: list[ColumnDoc]
    status: Literal["generated", "edited", "approved"]
    stale: bool
    llm_model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    updated_at: datetime


class DocIn(BaseModel):
    summary: str
    columns: list[ColumnDoc]
    status: Literal["edited", "approved"]


class RegenerateIn(BaseModel):
    tables: list[str] = Field(min_length=1)


class BenchmarkOut(BaseModel):
    run: RunOut
    summary: dict | None  # {config: metrics} once the run is done
    rows: list[dict]      # one per (config, question), as in results.csv


class ChatIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class ChatOut(BaseModel):
    id: int
    question: str
    kind: Literal["data", "schema", "abstain", "refuse", "failed"]
    answer: str
    evidence: dict  # the turn dict minus kind, answer, tokens, cost and latency
    llm_model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_s: float
    feedback: Literal["up", "down"] | None
    created_at: datetime


class FeedbackIn(BaseModel):
    feedback: Literal["up", "down"] | None
