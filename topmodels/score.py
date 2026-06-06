"""Weighted, explainable priority scoring."""

from __future__ import annotations

from topmodels.config import PipelineConfig
from topmodels.models import ScoreBreakdown, VehicleKey


def _min_max_norm(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    vmin = min(values.values())
    vmax = max(values.values())
    if vmax == vmin:
        return {k: (1.0 if vmax > 0 else 0.0) for k in values}
    return {k: (v - vmin) / (vmax - vmin) for k, v in values.items()}


def _explain(components: dict[str, float], weights: dict[str, float]) -> str:
    ranked = sorted(
        ((k, components.get(k, 0.0) * weights.get(k, 0.0)) for k in weights),
        key=lambda x: x[1],
        reverse=True,
    )
    parts = [f"{name.replace('_', ' ')} ({score:.2f})" for name, score in ranked if score > 0.01]
    return "Ranked high on " + ", ".join(parts[:3]) if parts else "Insufficient signal data"


def score_models(
    vehicles: dict[str, VehicleKey],
    aggregated: dict[str, dict[str, float]],
    config: PipelineConfig,
) -> list[tuple[str, ScoreBreakdown, dict[str, float]]]:
    """Return sorted list of (canonical_key, breakdown, raw_signals)."""
    weights = config.weights.model_dump()
    buckets = ["search", "listings", "community", "first_party", "problems"]

    raw_by_bucket: dict[str, dict[str, float]] = {b: {} for b in buckets}
    for key, signals in aggregated.items():
        for bucket in buckets:
            if bucket in signals:
                raw_by_bucket[bucket][key] = signals[bucket]

    norm_by_bucket = {b: _min_max_norm(raw_by_bucket[b]) for b in buckets}

    scored: list[tuple[str, ScoreBreakdown, dict[str, float]]] = []
    for key, vehicle in vehicles.items():
        signals = aggregated.get(key, {})
        components = {b: norm_by_bucket[b].get(key, 0.0) for b in buckets}
        total = sum(components[b] * weights.get(b, 0.0) for b in buckets)
        breakdown = ScoreBreakdown(
            search=components["search"],
            listings=components["listings"],
            community=components["community"],
            first_party=components["first_party"],
            problems=components["problems"],
            total=total,
            explanation=_explain(components, weights),
        )
        scored.append((key, breakdown, signals))

    scored.sort(key=lambda x: x[1].total, reverse=True)
    return scored
