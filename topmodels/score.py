"""Weighted, explainable priority scoring with calibration."""

from __future__ import annotations

from dataclasses import dataclass, field

from topmodels.calibration import (
    COUNT_BUCKETS,
    SIGNAL_BUCKETS,
    AggregationBundle,
    BucketMeta,
    compute_effective_weights,
    shrinkage_normalize,
)
from topmodels.config import PipelineConfig
from topmodels.models import ScoreBreakdown, VehicleKey


@dataclass
class ScoringResult:
    breakdown: ScoreBreakdown
    signals: dict[str, float]
    signal_meta: dict[str, dict] = field(default_factory=dict)
    model_confidence: float = 0.0
    effective_weights: dict[str, float] = field(default_factory=dict)
    gate_notes: dict[str, str] = field(default_factory=dict)


def _explain(components: dict[str, float], weights: dict[str, float]) -> str:
    ranked = sorted(
        ((k, components.get(k, 0.0) * weights.get(k, 0.0)) for k in SIGNAL_BUCKETS),
        key=lambda x: x[1],
        reverse=True,
    )
    parts = [f"{name.replace('_', ' ')} ({score:.2f})" for name, score in ranked if score > 0.01]
    return "Ranked high on " + ", ".join(parts[:3]) if parts else "Insufficient signal data"


def score_models(
    vehicles: dict[str, VehicleKey],
    bundle: AggregationBundle,
    config: PipelineConfig,
) -> tuple[list[tuple[str, ScoringResult]], dict[str, str], dict[str, float]]:
    """Return sorted (key, ScoringResult) list plus gate_notes and effective_weights."""
    gate_notes: dict[str, str] = {}
    _, effective_weights = compute_effective_weights(config, bundle, gate_notes=gate_notes)
    prior = config.calibration.shrinkage_prior

    # Build per-bucket raw + eligibility across all models
    norm_by_bucket: dict[str, dict[str, float]] = {b: {} for b in SIGNAL_BUCKETS}
    for bucket in SIGNAL_BUCKETS:
        raw_map: dict[str, float] = {}
        eligible: dict[str, bool] = {}
        for key in vehicles:
            meta = bundle.meta_by_key.get(key, {}).get(bucket, BucketMeta())
            raw_map[key] = bundle.raw_by_key.get(key, {}).get(bucket, 0.0)
            eligible[key] = meta.eligible and meta.data_present
        if bucket in COUNT_BUCKETS:
            norm_by_bucket[bucket] = shrinkage_normalize(raw_map, eligible, prior=prior)
        else:
            norm_by_bucket[bucket] = shrinkage_normalize(raw_map, eligible, prior=prior * 0.5)

    scored: list[tuple[str, ScoringResult]] = []
    for key in vehicles:
        metas = bundle.meta_by_key.get(key, {})
        components = {b: norm_by_bucket[b].get(key, 0.0) for b in SIGNAL_BUCKETS}
        total = sum(components[b] * effective_weights.get(b, 0.0) for b in SIGNAL_BUCKETS)

        breakdown = ScoreBreakdown(
            search=components["search"],
            listings=components["listings"],
            community=components["community"],
            first_party=components["first_party"],
            problems=components["problems"],
            total=total,
            explanation=_explain(components, effective_weights),
        )

        flat_signals = dict(bundle.raw_by_key.get(key, {}))
        signal_meta: dict[str, dict] = {}
        model_conf_vals: list[float] = []
        for bucket in SIGNAL_BUCKETS:
            meta = metas.get(bucket, BucketMeta())
            signal_meta[bucket] = {
                "matched": meta.matched,
                "data_present": meta.data_present,
                "eligible": meta.eligible,
                "sample_size": meta.sample_size,
                "confidence": meta.confidence,
                "normalized": components[bucket],
            }
            if meta.data_present:
                model_conf_vals.append(meta.confidence)

        model_confidence = round(sum(model_conf_vals) / len(model_conf_vals), 3) if model_conf_vals else 0.2

        scored.append(
            (
                key,
                ScoringResult(
                    breakdown=breakdown,
                    signals=flat_signals,
                    signal_meta=signal_meta,
                    model_confidence=model_confidence,
                    effective_weights=effective_weights,
                    gate_notes=gate_notes,
                ),
            )
        )

    scored.sort(key=lambda x: x[1].breakdown.total, reverse=True)
    return scored, gate_notes, effective_weights
