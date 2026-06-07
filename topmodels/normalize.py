"""Map every connector record onto the canonical vehicle key."""

from __future__ import annotations

from collections import defaultdict

from topmodels.calibration import (
    AggregationBundle,
    BucketMeta,
    bucket_confidence,
    min_sample_for_bucket,
)
from topmodels.config import PipelineConfig
from topmodels.models import SignalRecord, VehicleKey
from topmodels.taxonomy import Taxonomy


SIGNAL_BUCKETS: dict[str, str] = {
    "search_interest": "search",
    "keyword_volume": "search",
    "report_rank_score": "search",
    "listing_volume": "listings",
    "market_days_supply": "listings",
    "reddit_mentions": "community",
    "first_party_scans": "first_party",
    "problem_volume": "problems",
    "recall_count": "problems",
    "complaint_count": "problems",
    "investigation_count": "problems",
}

COUNT_SIGNALS = frozenset({"first_party_scans", "recall_count", "complaint_count", "investigation_count"})


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


def _meta_default() -> BucketMeta:
    return BucketMeta()


def aggregate_signals(
    records: list[SignalRecord],
    config: PipelineConfig,
    *,
    nhtsa_misses: list[dict] | None = None,
) -> AggregationBundle:
    """Build raw values + per-bucket metadata for calibrated scoring."""
    raw_by_key: dict[str, dict[str, float]] = defaultdict(dict)
    def _fresh_meta() -> dict[str, BucketMeta]:
        return {
            "search": _meta_default(),
            "listings": BucketMeta(data_present=False),
            "community": BucketMeta(data_present=False),
            "first_party": BucketMeta(data_present=False),
            "problems": BucketMeta(data_present=False, matched=False),
        }

    meta_by_key: dict[str, dict[str, BucketMeta]] = defaultdict(_fresh_meta)
    problems_sub: dict[str, dict[str, float]] = defaultdict(dict)
    problems_meta: dict[str, dict[str, bool]] = defaultdict(dict)

    total_fp_scans = 0

    for rec in records:
        bucket = bucket_for_signal(rec.signal)
        if not bucket:
            continue

        key = rec.key
        meta = rec.metadata or {}
        data_present = meta.get("data_present", True)
        matched = meta.get("matched", True)

        if rec.signal == "first_party_scans":
            total_fp_scans += int(rec.value)

        if bucket == "problems" and rec.signal in {
            "recall_count",
            "complaint_count",
            "investigation_count",
        }:
            problems_sub[key][rec.signal] = rec.value
            problems_meta[key][rec.signal] = data_present
            continue

        if bucket == "problems" and rec.signal == "problem_volume":
            continue

        bm = meta_by_key[key][bucket]
        if rec.signal in COUNT_SIGNALS:
            bm.sample_size = max(bm.sample_size, int(rec.value))

        if not data_present:
            bm.data_present = False
            bm.matched = matched
            continue

        if bucket not in ("listings", "community"):
            bm.data_present = True
        bm.matched = bm.matched and matched
        current = raw_by_key[key].get(bucket, 0.0)
        new_val = max(current, rec.value)
        raw_by_key[key][bucket] = new_val
        bm.raw = new_val

        if rec.signal in {"first_party_scans", "first_party_avg_score"}:
            raw_by_key[key][rec.signal] = rec.value

    # Problems bucket: recalls + complaints required; investigations optional; missing ≠ zero
    for key, subs in problems_sub.items():
        bm = meta_by_key[key]["problems"]
        recall_ok = problems_meta[key].get("recall_count", False)
        complaint_ok = problems_meta[key].get("complaint_count", False)
        if not (recall_ok and complaint_ok):
            bm.data_present = False
            bm.matched = recall_ok or complaint_ok or problems_meta[key].get("investigation_count", False)
            continue
        bm.data_present = True
        bm.matched = True
        prob_total = sum(
            val for sig, val in subs.items() if problems_meta[key].get(sig, False)
        )
        bm.sample_size = int(prob_total)
        raw_by_key[key]["problems"] = prob_total
        bm.raw = prob_total
        for sig, val in subs.items():
            raw_by_key[key][sig] = val

    # Apply per-signal minimum sample floors
    for key, buckets in meta_by_key.items():
        for bucket, bm in buckets.items():
            floor = min_sample_for_bucket(bucket, config.calibration)
            raw = raw_by_key.get(key, {}).get(bucket, 0.0)
            if bucket == "first_party":
                scans = int(raw_by_key.get(key, {}).get("first_party_scans", raw))
                bm.sample_size = max(bm.sample_size, scans)
                bm.eligible = bm.data_present and scans >= floor
            elif bucket == "problems":
                bm.eligible = bm.data_present
            else:
                bm.eligible = bm.data_present and (raw > 0 or bucket == "search")

            is_count = bucket in ("first_party", "problems")
            bm.confidence = bucket_confidence(bm, is_count=is_count)
            if not bm.data_present:
                raw_by_key.get(key, {}).pop(bucket, None)

    return AggregationBundle(
        raw_by_key=dict(raw_by_key),
        meta_by_key={k: dict(v) for k, v in meta_by_key.items()},
        nhtsa_misses=nhtsa_misses or [],
        total_first_party_scans=total_fp_scans,
    )


def aggregate_by_model(records: list[SignalRecord]) -> dict[str, dict[str, float]]:
    """Legacy flat aggregate (tests)."""
    bundle = aggregate_signals(records, PipelineConfig())
    return bundle.raw_by_key
