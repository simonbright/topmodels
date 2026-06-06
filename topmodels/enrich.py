"""Top-N enrichment with per-field source tags."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from topmodels.config import PipelineConfig
from topmodels.connectors.nhtsa import NhtsaConnector
from topmodels.http_client import CachedHttpClient
from topmodels.models import EnrichmentField, ModelEnrichment, RankedModel, VehicleKey


def enrich_top_models(
    ranked: list[RankedModel],
    config: PipelineConfig,
    *,
    refresh: bool = False,
) -> list[RankedModel]:
    if not config.sources.nhtsa:
        return ranked

    nhtsa = NhtsaConnector(config, CachedHttpClient(config.cache_path / "nhtsa"), refresh=refresh)
    now = datetime.now(timezone.utc)

    for item in ranked:
        vehicle = item.vehicle
        recall_count, recalls = nhtsa._safe_fetch(
            nhtsa.fetch_recalls, vehicle, lambda _e: (0, [])
        )
        complaint_count, components = nhtsa._safe_fetch(
            nhtsa.fetch_complaints, vehicle, lambda _e: (0, Counter())
        )
        investigation_count = nhtsa._safe_fetch(
            nhtsa.fetch_investigations, vehicle, lambda _e: 0
        )

        enrichment = ModelEnrichment(
            recalls=[
                {
                    "campaign_number": r.get("NHTSACampaignNumber"),
                    "component": r.get("Component"),
                    "summary": r.get("Summary"),
                    "consequence": r.get("Consequence"),
                    "remedy": r.get("Remedy"),
                    "report_date": r.get("ReportReceivedDate"),
                    "source": "NHTSA Recalls API",
                }
                for r in recalls[:10]
            ],
            recall_count=EnrichmentField(
                value=recall_count,
                source="NHTSA Recalls API",
                as_of=now,
            ),
            top_complaint_components=[
                EnrichmentField(
                    value={"component": name, "count": count},
                    source="NHTSA Complaints API",
                    as_of=now,
                )
                for name, count in components.most_common(5)
            ],
            investigation_count=EnrichmentField(
                value=investigation_count,
                source="NHTSA Investigations API",
                as_of=now,
            ),
        )

        fp_scans = item.signals.get("first_party_scans")
        if fp_scans:
            enrichment.first_party_scan_count = EnrichmentField(
                value=int(fp_scans),
                source="MotoMetrics telemetry export",
                as_of=now,
            )

        enrichment.notes.append(
            "Common-problems summaries must be grounded in NHTSA complaints/recalls above — "
            "flag needs_review before publishing."
        )
        item.enrichment = enrichment

    return ranked
