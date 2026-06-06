"""Curated rankings from published 'most popular used car' findings (attributed, not scraped)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from topmodels.config import PipelineConfig
from topmodels.connectors.base import BaseConnector
from topmodels.models import SignalRecord, VehicleKey
from topmodels.taxonomy import normalize_vehicle


class ReportsConnector(BaseConnector):
    name = "reports"

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config

    def _load_curated(self) -> list[dict]:
        path = self.config.reports_path()
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8")).get("rankings", [])

    def discover_models(self) -> list[VehicleKey]:
        vehicles: list[VehicleKey] = []
        seen: set[str] = set()
        for row in self._load_curated():
            vehicle = normalize_vehicle(row.get("year"), row.get("make"), row.get("model"))
            if not vehicle:
                continue
            cid = vehicle.canonical_id()
            if cid in seen:
                continue
            seen.add(cid)
            vehicles.append(vehicle)
        return vehicles

    def fetch(self, models: list[VehicleKey] | None = None) -> list[SignalRecord]:
        curated = self._load_curated()
        if not curated:
            return []

        target_keys = {m.canonical_id() for m in models} if models else None
        # Invert rank so higher = better (rank 1 → score 100)
        max_rank = max(int(r.get("rank", 0)) for r in curated) or 1
        records: list[SignalRecord] = []

        for row in curated:
            vehicle = normalize_vehicle(row.get("year"), row.get("make"), row.get("model"))
            if not vehicle:
                continue
            if target_keys is not None and vehicle.canonical_id() not in target_keys:
                continue

            rank = int(row.get("rank", max_rank))
            inverted = float(max_rank - rank + 1)
            as_of_raw = row.get("as_of") or row.get("published")
            as_of = datetime.fromisoformat(as_of_raw.replace("Z", "+00:00")) if as_of_raw else None

            records.append(
                SignalRecord.from_vehicle(
                    vehicle,
                    signal="report_rank_score",
                    value=inverted,
                    source=row.get("source_name", "Published industry report"),
                    as_of=as_of,
                    metadata={
                        "rank": rank,
                        "source_url": row.get("source_url"),
                        "report_title": row.get("report_title"),
                        "notes": row.get("notes"),
                    },
                )
            )
        return records
