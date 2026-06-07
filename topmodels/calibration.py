"""Calibration helpers — sample floors, shrinkage norm, weight gating."""

from __future__ import annotations

from dataclasses import dataclass, field

from topmodels.config import CalibrationConfig, PipelineConfig

SIGNAL_BUCKETS = ["search", "listings", "community", "first_party", "problems"]

COUNT_BUCKETS = frozenset({"first_party", "problems"})


@dataclass
class BucketMeta:
    raw: float = 0.0
    sample_size: int = 0
    data_present: bool = True
    matched: bool = True
    eligible: bool = True
    confidence: float = 0.0


@dataclass
class AggregationBundle:
    """Per-model raw values + metadata for scoring."""

    raw_by_key: dict[str, dict[str, float]] = field(default_factory=dict)
    meta_by_key: dict[str, dict[str, BucketMeta]] = field(default_factory=dict)
    nhtsa_misses: list[dict] = field(default_factory=list)
    total_first_party_scans: int = 0


def min_sample_for_bucket(bucket: str, config: CalibrationConfig) -> int:
    if bucket == "first_party":
        return config.min_first_party_scans
    return int(config.min_sample_by_signal.get(bucket, 0))


def shrinkage_normalize(
    raw: dict[str, float],
    eligible: dict[str, bool],
    *,
    prior: float,
    cap: float = 0.95,
) -> dict[str, float]:
    """Count/continuous shrinkage — a single nonzero point cannot reach 1.0."""
    elig_vals = [raw[k] for k in raw if eligible.get(k, False) and raw[k] > 0]
    if not elig_vals:
        return {k: 0.0 for k in raw}

    sorted_vals = sorted(elig_vals)
    idx = min(len(sorted_vals) - 1, int(0.9 * (len(sorted_vals) - 1)))
    p90 = sorted_vals[idx]
    denom = max(p90, 1.0)

    out: dict[str, float] = {}
    for key, value in raw.items():
        if not eligible.get(key, False) or value <= 0:
            out[key] = 0.0
            continue
        # v / (v + prior + p90) — bounded; cap below 1.0
        norm = value / (value + prior + denom)
        out[key] = min(cap, norm)
    return out


def bucket_confidence(meta: BucketMeta, *, is_count: bool) -> float:
    if not meta.data_present:
        return 0.0
    if not meta.matched:
        return 0.25
    if not meta.eligible:
        return 0.35 if is_count else 0.5
    if is_count and meta.sample_size > 0:
        return min(1.0, 0.5 + 0.1 * meta.sample_size)
    return 0.85 if meta.raw > 0 else 0.5


def compute_effective_weights(
    config: PipelineConfig,
    bundle: AggregationBundle,
    *,
    gate_notes: dict[str, str],
) -> tuple[dict[str, float], dict[str, float]]:
    """Return (configured weights, effective renormalized weights over active signals)."""
    configured = config.weights.model_dump()
    effective = dict(configured)

    # Signal-level activation gate (first-party total volume)
    threshold = config.calibration.activation_threshold.first_party_total_scans
    if bundle.total_first_party_scans < threshold:
        effective["first_party"] = 0.0
        gate_notes["first_party"] = (
            f"gated: {bundle.total_first_party_scans} scans < {threshold}"
        )

    # Zero out config-disabled signals (listings/community at 0)
    for bucket in SIGNAL_BUCKETS:
        if configured.get(bucket, 0) <= 0:
            effective[bucket] = 0.0

    def bucket_has_coverage(bucket: str) -> bool:
        for key, metas in bundle.meta_by_key.items():
            meta = metas.get(bucket)
            if not meta or not meta.data_present:
                continue
            raw_val = bundle.raw_by_key.get(key, {}).get(bucket, 0.0)
            if bucket == "search" and raw_val > 0:
                return True
            if meta.eligible and raw_val > 0:
                return True
        return False

    active = {
        b: effective[b]
        for b in SIGNAL_BUCKETS
        if effective.get(b, 0) > 0 and bucket_has_coverage(b)
    }
    total = sum(active.values())
    if total <= 0:
        return configured, {b: 0.0 for b in SIGNAL_BUCKETS}

    renormalized = {b: (active[b] / total if b in active else 0.0) for b in SIGNAL_BUCKETS}
    return configured, renormalized


def model_confidence(metas: dict[str, BucketMeta]) -> float:
    if not metas:
        return 0.0
    vals = [m.confidence for m in metas.values() if m.data_present]
    if not vals:
        return 0.2
    return round(sum(vals) / len(vals), 3)
