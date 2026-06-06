"""NHTSA recalls, complaints, and investigations (free REST APIs)."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from topmodels.config import PipelineConfig
from topmodels.connectors.base import BaseConnector
from topmodels.http_client import CachedHttpClient
from topmodels.models import SignalRecord, VehicleKey

NHTSA_API = "https://api.nhtsa.gov"


def nhtsa_api_model_name(model: str) -> str:
    """NHTSA vehicle endpoints reject some punctuation (e.g. F-150 → F150)."""
    return str(model).replace("-", "").replace("/", " ").strip().title()


class NhtsaConnector(BaseConnector):
    name = "nhtsa"

    def __init__(self, config: PipelineConfig, http: CachedHttpClient, *, refresh: bool = False) -> None:
        self.config = config
        self.http = CachedHttpClient(
            config.cache_path / "nhtsa",
            pause_sec=config.nhtsa.request_pause_sec,
        )
        self.refresh = refresh

    def _vehicle_params(self, vehicle: VehicleKey) -> dict[str, str | int]:
        return {
            "make": vehicle.make.title(),
            "model": nhtsa_api_model_name(vehicle.model),
            "modelYear": vehicle.year,
        }

    def _safe_fetch(self, fetcher, vehicle: VehicleKey, default):
        try:
            return fetcher(vehicle)
        except Exception as exc:  # noqa: BLE001 — per-model degrade, never fabricate
            return default(exc)

    def fetch_recalls(self, vehicle: VehicleKey) -> tuple[int, list[dict[str, Any]]]:
        data = self.http.get_json(
            "recalls",
            f"{NHTSA_API}/recalls/recallsByVehicle",
            params=self._vehicle_params(vehicle),
            refresh=self.refresh,
        )
        results = data.get("results") or []
        return len(results), results

    def fetch_complaints(self, vehicle: VehicleKey) -> tuple[int, Counter[str]]:
        data = self.http.get_json(
            "complaints",
            f"{NHTSA_API}/complaints/complaintsByVehicle",
            params=self._vehicle_params(vehicle),
            refresh=self.refresh,
        )
        results = data.get("results") or []
        capped = results[: self.config.nhtsa.max_complaints_per_model]
        components = Counter(
            str(r.get("components") or "Unknown").strip() or "Unknown" for r in capped
        )
        return len(results), components

    def fetch_investigations(self, vehicle: VehicleKey) -> int:
        data = self.http.get_json(
            "investigations",
            f"{NHTSA_API}/investigations/investigationsByVehicle",
            params=self._vehicle_params(vehicle),
            refresh=self.refresh,
        )
        return len(data.get("results") or [])

    def fetch(self, models: list[VehicleKey] | None = None) -> list[SignalRecord]:
        if not models:
            return []
        now = datetime.now(timezone.utc)
        records: list[SignalRecord] = []
        for vehicle in models:
            recall_count, _ = self._safe_fetch(
                self.fetch_recalls, vehicle, lambda e: (0, [])
            )
            complaint_count, top_components = self._safe_fetch(
                self.fetch_complaints, vehicle, lambda e: (0, Counter())
            )
            investigation_count = self._safe_fetch(
                self.fetch_investigations, vehicle, lambda e: 0
            )
            problem_volume = float(recall_count + complaint_count + investigation_count)

            records.append(
                SignalRecord.from_vehicle(
                    vehicle,
                    signal="recall_count",
                    value=float(recall_count),
                    source="NHTSA Recalls API",
                    as_of=now,
                    metadata={"endpoint": f"{NHTSA_API}/recalls/recallsByVehicle"},
                )
            )
            records.append(
                SignalRecord.from_vehicle(
                    vehicle,
                    signal="complaint_count",
                    value=float(complaint_count),
                    source="NHTSA Complaints API",
                    as_of=now,
                    metadata={
                        "endpoint": f"{NHTSA_API}/complaints/complaintsByVehicle",
                        "top_components": top_components.most_common(5),
                    },
                )
            )
            records.append(
                SignalRecord.from_vehicle(
                    vehicle,
                    signal="investigation_count",
                    value=float(investigation_count),
                    source="NHTSA Investigations API",
                    as_of=now,
                    metadata={"endpoint": f"{NHTSA_API}/investigations/investigationsByVehicle"},
                )
            )
            records.append(
                SignalRecord.from_vehicle(
                    vehicle,
                    signal="problem_volume",
                    value=problem_volume,
                    source="NHTSA (recalls + complaints + investigations)",
                    as_of=now,
                )
            )
        return records
