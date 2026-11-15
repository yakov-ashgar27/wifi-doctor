"""SQLAlchemy ORM models and Pydantic API schemas."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------- ORM
class IncidentLog(Base):
    """One degradation episode, from first unhealthy tick until recovery."""

    __tablename__ = "incident_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)

    severity: Mapped[str] = mapped_column(String(16))            # WARNING | CRITICAL (peak)
    root_cause_layer: Mapped[str] = mapped_column(String(24), index=True)
    confidence_score: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(160))
    explanation: Mapped[str] = mapped_column(Text)
    recommended_action: Mapped[str] = mapped_column(Text)

    # Network context
    gateway_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    wan_target: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dns_server: Mapped[str | None] = mapped_column(String(64), nullable=True)
    probe_backend: Mapped[str | None] = mapped_column(String(24), nullable=True)

    # Worst values observed during the incident (the "evidence")
    gateway_rtt_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    gateway_loss_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    wan_rtt_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    wan_jitter_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    wan_loss_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    dns_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    dns_loss_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    download_mbps: Mapped[float | None] = mapped_column(Float, nullable=True)
    upload_mbps: Mapped[float | None] = mapped_column(Float, nullable=True)
    top_process: Mapped[str | None] = mapped_column(String(128), nullable=True)

    simulated: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    chaos_mode: Mapped[str] = mapped_column(String(24), default="none")


# ----------------------------------------------------------------- Schemas
class IncidentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    started_at: datetime
    resolved_at: datetime | None
    duration_s: float | None
    severity: str
    root_cause_layer: str
    confidence_score: int
    title: str
    explanation: str
    recommended_action: str
    gateway_ip: str | None
    wan_target: str | None
    dns_server: str | None
    probe_backend: str | None
    gateway_rtt_ms: float | None
    gateway_loss_pct: float | None
    wan_rtt_ms: float | None
    wan_jitter_ms: float | None
    wan_loss_pct: float | None
    dns_ms: float | None
    dns_loss_pct: float | None
    download_mbps: float | None
    upload_mbps: float | None
    top_process: str | None
    simulated: bool
    chaos_mode: str

    @field_validator("started_at", "resolved_at", mode="after")
    @classmethod
    def _assume_utc(cls, value: datetime | None) -> datetime | None:
        # SQLite drops tzinfo; everything is stored in UTC.
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class IncidentPage(BaseModel):
    items: list[IncidentOut]
    total: int
    page: int
    page_size: int
    pages: int


class ChaosState(BaseModel):
    mode: str
    since: float | None = Field(default=None, description="Epoch seconds when the mode was set")
    available: list[str]
