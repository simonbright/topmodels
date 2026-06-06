"""Map every connector record onto the canonical vehicle key."""

from __future__ import annotations

from collections import defaultdict

from topmodels.models import SignalRecord, VehicleKey
from topmodels.taxonomy import Taxonomy


# Map raw connector signals → scoring bucket
SIGNAL_BUCKETS: dict[str, str] = {
    "search_interest": "search",
    "keyword_volume": "search",
    "report_rank_score": "search",
    "listing_volume": "listings",
    "market_days_supply": "listings",
    "reddit_mentions": "community",
    "first_party_scans": "first_party",
    # Avg score is enrichment metadata only — never mix with scan counts in scoring.
    "problem_volume": "problems",
    "recall_count": "problems",
    "complaint_count": "problems",
    "investigation_count": "problems",
}


def bucket_for_signal(signal: str) -> str | None:
    return SIGNAL_BUCKETS.get(signal)


def normalize_records(
    records: list[SignalRecord],
    taxonomy: Taxonomy,
) -> list[SignalRecord]:
    """Resolve aliases on each record's vehicle key."""
    out: list[SignalRecord] = []
    for rec in records:
        vehicle = VehicleKey(
            year=rec.year,
            make=rec.make,
            model=rec.model,
        )
        resolved = taxonomy.resolve(vehicle)
        out.append(
            rec.model_copy(
                update={
                    "key": resolved.canonical_id(),
                    "year": resolved.year,
                    "make": resolved.make,
                    "model": resolved.model,
                }
            )
        )
    return out


def aggregate_by_model(records: list[SignalRecord]) -> dict[str, dict[str, float]]:
    """Collapse to canonical key → bucket → max signal value in bucket."""
    per_key: dict[str, dict[str, float]] = defaultdict(dict)
    for rec in records:
        bucket = bucket_for_signal(rec.signal)
        if not bucket:
            continue
        current = per_key[rec.key].get(bucket, 0.0)
        # For problems, sum sub-signals; for others take max
        if bucket == "problems" and rec.signal in {
            "recall_count",
            "complaint_count",
            "investigation_count",
        }:
            per_key[rec.key][rec.signal] = rec.value
            per_key[rec.key][bucket] = (
                per_key[rec.key].get("recall_count", 0)
                + per_key[rec.key].get("complaint_count", 0)
                + per_key[rec.key].get("investigation_count", 0)
            )
        else:
            per_key[rec.key][bucket] = max(current, rec.value)
        # Preserve raw signal names for enrichment (not used in scoring buckets).
        if rec.signal in {"first_party_scans", "first_party_avg_score"}:
            per_key[rec.key][rec.signal] = rec.value
    return dict(per_key)
