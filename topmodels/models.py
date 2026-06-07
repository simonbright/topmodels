"""Pydantic record types shared across connectors and pipeline."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class VehicleKey(BaseModel):
    """Canonical join key for all sources."""

    year: int
    make: str
    model: str
    generation: str | None = None
    trim: str | None = None

    def canonical_id(self) -> str:
        parts = [str(self.year), self.make.upper(), self.model.upper()]
        if self.generation:
            parts.append(self.generation.upper())
        if self.trim:
            parts.append(self.trim.upper())
        return "|".join(parts)

    def display_label(self) -> str:
        return f"{self.year} {self.make} {self.model}"

    @classmethod
    def from_parts(
        cls,
        year: int | str | None,
        make: str | None,
        model: str | None,
        *,
        generation: str | None = None,
        trim: str | None = None,
    ) -> VehicleKey | None:
        if year is None or make is None or model is None:
            return None
        try:
            y = int(year)
        except (TypeError, ValueError):
            return None
        mk = str(make).strip()
        md = str(model).strip()
        if not mk or not md or y < 1980 or y > 2100:
            return None
        return cls(year=y, make=mk, model=md, generation=generation, trim=trim)


class SignalRecord(BaseModel):
    """One attributed signal from a connector, keyed by canonical vehicle."""

    key: str
    year: int
    make: str
    model: str
    signal: str
    value: float
    source: str
    as_of: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_vehicle(
        cls,
        vehicle: VehicleKey,
        *,
        signal: str,
        value: float,
        source: str,
        as_of: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SignalRecord:
        return cls(
            key=vehicle.canonical_id(),
            year=vehicle.year,
            make=vehicle.make,
            model=vehicle.model,
            signal=signal,
            value=float(value),
            source=source,
            as_of=as_of or utc_now(),
            metadata=metadata or {},
        )


class ScoreBreakdown(BaseModel):
    search: float = 0.0
    listings: float = 0.0
    community: float = 0.0
    first_party: float = 0.0
    problems: float = 0.0
    total: float = 0.0
    explanation: str = ""


class EnrichmentField(BaseModel):
    value: Any
    source: str
    as_of: datetime | None = None
    needs_review: bool = False


class ModelEnrichment(BaseModel):
    recalls: list[dict[str, Any]] = Field(default_factory=list)
    recall_count: EnrichmentField | None = None
    top_complaint_components: list[EnrichmentField] = Field(default_factory=list)
    investigation_count: EnrichmentField | None = None
    first_party_scan_count: EnrichmentField | None = None
    first_party_avg_score: EnrichmentField | None = None
    notes: list[str] = Field(default_factory=list)


class RankedModel(BaseModel):
    rank: int
    vehicle: VehicleKey
    score: ScoreBreakdown
    signals: dict[str, float] = Field(default_factory=dict)
    signal_meta: dict[str, dict] = Field(default_factory=dict)
    model_confidence: float = 0.0
    enrichment: ModelEnrichment | None = None
    riser: bool = False
    previous_rank: int | None = None
