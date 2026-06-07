"""NHTSA recalls, complaints, and investigations (free REST APIs)."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import time
from typing import Any

import httpx

from topmodels.config import PipelineConfig
from topmodels.connectors.base import BaseConnector
from topmodels.http_client import CachedHttpClient
from topmodels.models import SignalRecord, VehicleKey
from topmodels.taxonomy import Taxonomy

NHTSA_API = "https://api.nhtsa.gov"


class NhtsaConnector(BaseConnector):
    name = "nhtsa"

    def __init__(
        self,
        config: PipelineConfig,
        http: CachedHttpClient,
        taxonomy: Taxonomy,
        *,
        refresh: bool = False,
    ) -> None:
        self.config = config
        self.http = CachedHttpClient(
            config.cache_path / "nhtsa",
            pause_sec=config.nhtsa.request_pause_sec,
        )
        self.taxonomy = taxonomy
        self.refresh = refresh
        self.miss_log: list[dict[str, Any]] = []

    def _query_params(self, resolution, model_token: str) -> dict[str, str | int]:
        return {
            "make": resolution.api_make,
            "model": model_token,
            "modelYear": resolution.api_make and 0 or 0,  # placeholder fix below
        }

    def _request_endpoint(
        self,
        namespace: str,
        endpoint: str,
        params: dict[str, str | int],
    ) -> dict | None:
        """GET JSON; NHTSA sometimes returns 400 with a valid empty results payload."""
        self.http._throttle()
        last_err: Exception | None = None
        for attempt in range(self.http.max_retries):
            try:
                with httpx.Client(timeout=self.http.timeout_sec, follow_redirects=True) as client:
                    resp = client.get(endpoint, params=params)
                    if resp.status_code == 200:
                        body = resp.json()
                        self.http._last_request_at = time.monotonic()
                        return body
                    if resp.status_code == 400:
                        body = resp.json()
                        if isinstance(body, dict) and "results" in body:
                            self.http._last_request_at = time.monotonic()
                            return body
                    resp.raise_for_status()
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                time.sleep(min(2**attempt, 8))
        raise RuntimeError(f"HTTP failed after {self.http.max_retries} tries: {endpoint}") from last_err

    def _try_endpoint(
        self,
        namespace: str,
        endpoint: str,
        resolution,
        vehicle: VehicleKey,
        *,
        optional: bool = False,
    ) -> tuple[dict | list | None, str | None, bool]:
        """Return (body, model_token_used, data_present). data_present=False on HTTP error."""
        last_err: Exception | None = None
        for token in resolution.candidates:
            params = {
                "make": resolution.api_make,
                "model": token,
                "modelYear": vehicle.year,
            }
            try:
                body = self._request_endpoint(namespace, endpoint, params)
                return body, token, True
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                continue
        self.miss_log.append(
            {
                "vehicle": vehicle.display_label(),
                "canonical_id": vehicle.canonical_id(),
                "endpoint": endpoint,
                "vpic_matched": resolution.vpic_matched,
                "candidates_tried": resolution.candidates,
                "error": str(last_err) if last_err else "all candidates failed",
                "optional": optional,
            }
        )
        return None, None, False

    def _emit_missing(
        self,
        vehicle: VehicleKey,
        resolution,
        now: datetime,
        *,
        reason: str,
    ) -> list[SignalRecord]:
        base_meta = {
            "matched": resolution.vpic_matched,
            "data_present": False,
            "vpic_model": resolution.vpic_model_name,
            "query_model": None,
            "reason": reason,
        }
        self.miss_log.append(
            {
                "vehicle": vehicle.display_label(),
                "canonical_id": vehicle.canonical_id(),
                "vpic_matched": resolution.vpic_matched,
                "vpic_model": resolution.vpic_model_name,
                "reason": reason,
            }
        )
        records: list[SignalRecord] = []
        for signal in ("recall_count", "complaint_count", "investigation_count", "problem_volume"):
            records.append(
                SignalRecord.from_vehicle(
                    vehicle,
                    signal=signal,
                    value=0.0,
                    source="NHTSA (missing — not scored as zero)",
                    as_of=now,
                    metadata=dict(base_meta),
                )
            )
        return records

    def fetch(self, models: list[VehicleKey] | None = None) -> list[SignalRecord]:
        if not models:
            return []
        now = datetime.now(timezone.utc)
        records: list[SignalRecord] = []

        for vehicle in models:
            resolution = self.taxonomy.resolve_nhtsa_query(vehicle)
            if not resolution.candidates:
                records.extend(
                    self._emit_missing(
                        vehicle,
                        resolution,
                        now,
                        reason="no NHTSA model token candidates",
                    )
                )
                continue

            recall_body, recall_model, recall_ok = self._try_endpoint(
                "recalls",
                f"{NHTSA_API}/recalls/recallsByVehicle",
                resolution,
                vehicle,
            )
            complaint_body, complaint_model, complaint_ok = self._try_endpoint(
                "complaints",
                f"{NHTSA_API}/complaints/complaintsByVehicle",
                resolution,
                vehicle,
            )
            inv_body, inv_model, inv_ok = self._try_endpoint(
                "investigations",
                f"{NHTSA_API}/investigations/investigationsByVehicle",
                resolution,
                vehicle,
                optional=True,
            )

            if not (recall_ok or complaint_ok):
                reason = (
                    "model not found in vPIC for make/year"
                    if not resolution.vpic_matched
                    else "NHTSA API query failed for all model token candidates"
                )
                records.extend(self._emit_missing(vehicle, resolution, now, reason=reason))
                continue

            recalls = (recall_body or {}).get("results") or [] if recall_ok else []
            complaints = (complaint_body or {}).get("results") or [] if complaint_ok else []
            investigations = (inv_body or {}).get("results") or [] if inv_ok else []
            capped = complaints[: self.config.nhtsa.max_complaints_per_model]
            components = Counter(
                str(r.get("components") or "Unknown").strip() or "Unknown" for r in capped
            )
            query_model = recall_model or complaint_model or inv_model

            recall_count = len(recalls)
            complaint_count = len(complaints)
            investigation_count = len(investigations)
            problem_volume = float(recall_count + complaint_count + investigation_count)

            records.append(
                SignalRecord.from_vehicle(
                    vehicle,
                    signal="recall_count",
                    value=float(recall_count),
                    source="NHTSA Recalls API",
                    as_of=now,
                    metadata={
                        "matched": resolution.vpic_matched,
                        "data_present": recall_ok,
                        "vpic_model": resolution.vpic_model_name,
                        "query_model": recall_model,
                        "endpoint": f"{NHTSA_API}/recalls/recallsByVehicle",
                    },
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
                        "matched": resolution.vpic_matched,
                        "data_present": complaint_ok,
                        "vpic_model": resolution.vpic_model_name,
                        "query_model": complaint_model,
                        "endpoint": f"{NHTSA_API}/complaints/complaintsByVehicle",
                        "top_components": components.most_common(5),
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
                    metadata={
                        "matched": resolution.vpic_matched,
                        "data_present": inv_ok,
                        "vpic_model": resolution.vpic_model_name,
                        "query_model": inv_model,
                        "endpoint": f"{NHTSA_API}/investigations/investigationsByVehicle",
                    },
                )
            )
            records.append(
                SignalRecord.from_vehicle(
                    vehicle,
                    signal="problem_volume",
                    value=problem_volume,
                    source="NHTSA (recalls + complaints + investigations)",
                    as_of=now,
                    metadata={
                        "matched": resolution.vpic_matched,
                        "data_present": recall_ok or complaint_ok,
                        "vpic_model": resolution.vpic_model_name,
                        "query_model": query_model,
                    },
                )
            )

        return records

    def fetch_enrichment_data(self, vehicle: VehicleKey) -> dict[str, Any] | None:
        """Top-N enrichment — returns None when data is missing (not zero)."""
        resolution = self.taxonomy.resolve_nhtsa_query(vehicle)
        if not resolution.vpic_matched:
            return None

        recall_body, _, recall_ok = self._try_endpoint(
            "recalls",
            f"{NHTSA_API}/recalls/recallsByVehicle",
            resolution,
            vehicle,
        )
        complaint_body, _, complaint_ok = self._try_endpoint(
            "complaints",
            f"{NHTSA_API}/complaints/complaintsByVehicle",
            resolution,
            vehicle,
        )
        inv_body, _, inv_ok = self._try_endpoint(
            "investigations",
            f"{NHTSA_API}/investigations/investigationsByVehicle",
            resolution,
            vehicle,
        )
        if not (recall_ok or complaint_ok):
            return None

        recalls = (recall_body or {}).get("results") or [] if recall_ok else []
        complaints = (complaint_body or {}).get("results") or [] if complaint_ok else []
        capped = complaints[: self.config.nhtsa.max_complaints_per_model]
        components = Counter(
            str(r.get("components") or "Unknown").strip() or "Unknown" for r in capped
        )
        return {
            "recall_count": len(recalls),
            "recalls": recalls,
            "complaint_count": len(complaints),
            "components": components,
            "investigation_count": len((inv_body or {}).get("results") or []),
            "query_model": resolution.vpic_model_name,
        }
