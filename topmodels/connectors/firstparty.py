"""First-party MotoMetrics telemetry — models our users actually scan."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from topmodels.config import PipelineConfig
from topmodels.connectors.base import BaseConnector
from topmodels.models import SignalRecord, VehicleKey
from topmodels.taxonomy import normalize_vehicle


def _is_demo_record(record: dict) -> bool:
    if record.get("demoShowcase"):
        return True
    conn = record.get("connection") or {}
    if conn.get("mode") == "simulation" or conn.get("simProfile"):
        return True
    if record.get("simMeta", {}).get("profileId"):
        return True
    return False


def _identity_from_record(record: dict) -> tuple[int | None, str | None, str | None]:
    vin = record.get("vin")
    chassis = (record.get("orchestratorState") or {}).get("chassisProfile") or {}
    stored = record.get("vehicle") or (record.get("scenario") or {}).get("vehicle") or {}

    candidates: list[tuple[int | None, str | None, str | None]] = []
    if chassis.get("year") and chassis.get("model"):
        candidates.append((chassis.get("year"), chassis.get("make"), chassis.get("model")))
    if stored.get("year") and stored.get("make") and stored.get("model"):
        candidates.append((stored.get("year"), stored.get("make"), stored.get("model")))

    for year, make, model in candidates:
        if year and make and model:
            return int(year), str(make), str(model)
    return None, None, None


def _score_from_record(record: dict) -> float | None:
    analysis = record.get("analysis") or {}
    score = analysis.get("score")
    if score is None:
        orch = record.get("orchestratorState") or {}
        score = (orch.get("lastAnalysis") or {}).get("score")
    try:
        return float(score) if score is not None else None
    except (TypeError, ValueError):
        return None


class FirstPartyConnector(BaseConnector):
    name = "firstparty"

    def __init__(self, config: PipelineConfig, *, refresh: bool = False) -> None:
        self.config = config
        self.refresh = refresh

    def _load_records(self) -> list[dict]:
        path = self.config.firstparty_export_path()
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return payload
        return payload.get("records") or []

    def discover_models(self) -> list[VehicleKey]:
        """Return vehicles seen in telemetry (also emits scan signals)."""
        records = self._load_records()
        vehicles: list[VehicleKey] = []
        seen: set[str] = set()
        for record in records:
            if self.config.firstparty.exclude_demo and _is_demo_record(record):
                continue
            year, make, model = _identity_from_record(record)
            vehicle = normalize_vehicle(year, make, model)
            if not vehicle:
                continue
            cid = vehicle.canonical_id()
            if cid in seen:
                continue
            seen.add(cid)
            vehicles.append(vehicle)
        return vehicles

    def fetch(self, models: list[VehicleKey] | None = None) -> list[SignalRecord]:
        records = self._load_records()
        if not records:
            return []

        now = datetime.now(timezone.utc)
        scan_counts: dict[str, int] = defaultdict(int)
        score_sums: dict[str, float] = defaultdict(float)
        score_counts: dict[str, int] = defaultdict(int)
        vehicle_by_key: dict[str, VehicleKey] = {}

        for record in records:
            if self.config.firstparty.exclude_demo and _is_demo_record(record):
                continue
            year, make, model = _identity_from_record(record)
            vehicle = normalize_vehicle(year, make, model)
            if not vehicle:
                continue
            key = vehicle.canonical_id()
            vehicle_by_key[key] = vehicle
            scan_counts[key] += 1
            score = _score_from_record(record)
            if score is not None:
                score_sums[key] += score
                score_counts[key] += 1

        target_keys: set[str] | None = None
        if models:
            target_keys = {m.canonical_id() for m in models}

        out: list[SignalRecord] = []
        for key, count in scan_counts.items():
            if target_keys is not None and key not in target_keys:
                continue
            vehicle = vehicle_by_key[key]
            out.append(
                SignalRecord.from_vehicle(
                    vehicle,
                    signal="first_party_scans",
                    value=float(count),
                    source="MotoMetrics telemetry export",
                    as_of=now,
                    metadata={"export_path": str(self.config.firstparty_export_path())},
                )
            )
            if score_counts[key]:
                out.append(
                    SignalRecord.from_vehicle(
                        vehicle,
                        signal="first_party_avg_score",
                        value=score_sums[key] / score_counts[key],
                        source="MotoMetrics telemetry export",
                        as_of=now,
                    )
                )
        return out
