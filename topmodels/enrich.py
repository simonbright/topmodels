"""Top-N enrichment with per-field source tags."""

from __future__ import annotations

from datetime import datetime, timezone

from topmodels.config import PipelineConfig
from topmodels.connectors.nhtsa import NhtsaConnector
from topmodels.http_client import CachedHttpClient
from topmodels.models import EnrichmentField, ModelEnrichment, RankedModel
from topmodels.taxonomy import Taxonomy


def enrich_top_models(
    ranked: list[RankedModel],
    config: PipelineConfig,
    taxonomy: Taxonomy,
    *,
    refresh: bool = False,
) -> list[RankedModel]:
    if not config.sources.nhtsa:
        return ranked

    nhtsa = NhtsaConnector(
        config,
        CachedHttpClient(config.cache_path / "nhtsa"),
        taxonomy,
        refresh=refresh,
    )
    now = datetime.now(timezone.utc)

    for item in ranked:
        vehicle = item.vehicle
        data = nhtsa.fetch_enrichment_data(vehicle)
        if not data:
            item.enrichment = ModelEnrichment(
                notes=[
                    "NHTSA enrichment missing — model token did not resolve or API query failed. "
                    "Do not treat as zero complaints."
                ]
            )
            continue

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
                for r in data["recalls"][:10]
            ],
            recall_count=EnrichmentField(
                value=data["recall_count"],
                source="NHTSA Recalls API",
                as_of=now,
            ),
            top_complaint_components=[
                EnrichmentField(
                    value={"component": name, "count": count},
                    source="NHTSA Complaints API",
                    as_of=now,
                )
                for name, count in data["components"].most_common(5)
            ],
            investigation_count=EnrichmentField(
                value=data["investigation_count"],
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
