"""Pipeline orchestrator — connectors → normalize → score → enrich → outputs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from topmodels.config import PipelineConfig, sources_for_phase
from topmodels.connectors.firstparty import FirstPartyConnector
from topmodels.connectors.nhtsa import NhtsaConnector
from topmodels.connectors.reports import ReportsConnector
from topmodels.connectors.trends import TrendsConnector
from topmodels.enrich import enrich_top_models
from topmodels.http_client import CachedHttpClient
from topmodels.models import RankedModel, VehicleKey
from topmodels.normalize import aggregate_by_model, normalize_records
from topmodels.score import score_models
from topmodels.taxonomy import Taxonomy


def _seed_models(config: PipelineConfig, sources) -> list[VehicleKey]:
    seeds: list[VehicleKey] = []
    if sources.reports:
        seeds.extend(ReportsConnector(config).discover_models())
    if sources.firstparty:
        seeds.extend(FirstPartyConnector(config).discover_models())
    return seeds


def _fetch_signals(
    config: PipelineConfig,
    sources,
    models: list[VehicleKey],
    *,
    refresh: bool,
) -> list:
    from topmodels.models import SignalRecord

    records: list[SignalRecord] = []
    http = CachedHttpClient(config.cache_path)

    if sources.reports:
        records.extend(ReportsConnector(config).fetch(models))
    if sources.firstparty:
        records.extend(FirstPartyConnector(config, refresh=refresh).fetch(models))
    if sources.nhtsa:
        records.extend(NhtsaConnector(config, http, refresh=refresh).fetch(models))
    if sources.trends:
        records.extend(TrendsConnector(config, refresh=refresh).fetch(models))

    return records


def _load_previous_ranks(output_dir: Path) -> dict[str, int]:
    prev_path = output_dir / "top_models.json"
    if not prev_path.exists():
        return {}
    try:
        payload = json.loads(prev_path.read_text(encoding="utf-8"))
        return {row["canonical_id"]: int(row["rank"]) for row in payload.get("models", [])}
    except (json.JSONDecodeError, KeyError, TypeError):
        return {}


def _write_backlog(path: Path, ranked: list[RankedModel]) -> None:
    lines = [
        "# Top Models Content Backlog",
        "",
        f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_",
        "",
        "Each brief is pre-filled with sourced facts. **Do not publish without human review.**",
        "",
    ]
    for item in ranked:
        v = item.vehicle
        label = v.display_label()
        riser = " 🔺 _riser_" if item.riser else ""
        lines.extend(
            [
                f"## {item.rank}. {label}{riser}",
                "",
                f"**Priority score:** {item.score.total:.3f} — {item.score.explanation}",
                "",
                "### Content briefs",
                "",
                f"1. **{label} common problems** — Ground in NHTSA complaints/recalls only.",
            ]
        )
        if item.enrichment:
            if item.enrichment.recall_count:
                lines.append(
                    f"   - Recalls: **{item.enrichment.recall_count.value}** "
                    f"(source: {item.enrichment.recall_count.source})"
                )
            for field in item.enrichment.top_complaint_components[:3]:
                comp = field.value.get("component") if isinstance(field.value, dict) else field.value
                count = field.value.get("count") if isinstance(field.value, dict) else ""
                lines.append(f"   - Top complaint: **{comp}** ({count} reports, source: {field.source})")
            if item.enrichment.first_party_scan_count:
                lines.append(
                    f"   - MotoMetrics scans: **{item.enrichment.first_party_scan_count.value}** "
                    f"(source: {item.enrichment.first_party_scan_count.source})"
                )
        lines.extend(
            [
                f"2. **Is $X fair for a {v.model.title()}?** — Use market band when MarketCheck enabled; "
                "otherwise cite scan exposure ranges from first-party data only.",
                f"3. **{label} buyer checklist** — Tie checklist items to sourced recall/complaint categories.",
                "",
                "---",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def run_pipeline(
    config: PipelineConfig,
    *,
    phase: int = 1,
    top_n: int | None = None,
    refresh: bool = False,
    dry_run: bool = False,
) -> list[RankedModel]:
    sources = sources_for_phase(config, phase)
    n = top_n or config.top_n

    taxonomy = Taxonomy(CachedHttpClient(config.cache_path / "vpic"), refresh=refresh)
    seeds = _seed_models(config, sources)
    if not seeds:
        raise RuntimeError(
            "No seed models found. Add data/curated_reports.json and/or a first-party telemetry export."
        )

    models = taxonomy.resolve_many(seeds)
    raw_records = _fetch_signals(config, sources, models, refresh=refresh)
    normalized = normalize_records(raw_records, taxonomy)
    aggregated = aggregate_by_model(normalized)

    vehicles = {m.canonical_id(): m for m in models}
    # Include any keys seen in signals but not in seed list
    for key in aggregated:
        if key not in vehicles:
            parts = key.split("|")
            if len(parts) >= 3:
                v = VehicleKey(year=int(parts[0]), make=parts[1], model=parts[2])
                vehicles[key] = v

    scored = score_models(vehicles, aggregated, config)
    previous = _load_previous_ranks(config.output_path)

    ranked: list[RankedModel] = []
    for idx, (key, breakdown, signals) in enumerate(scored[:n], start=1):
        prev_rank = previous.get(key)
        riser = prev_rank is not None and idx < prev_rank
        ranked.append(
            RankedModel(
                rank=idx,
                vehicle=vehicles[key],
                score=breakdown,
                signals=signals,
                riser=riser,
                previous_rank=prev_rank,
            )
        )

    if not dry_run and sources.nhtsa:
        ranked = enrich_top_models(ranked, config, refresh=refresh)

    if dry_run:
        return ranked

    config.output_path.mkdir(parents=True, exist_ok=True)
    _write_outputs(config, ranked)
    return ranked


def _write_outputs(config: PipelineConfig, ranked: list[RankedModel]) -> None:
    out = config.output_path
    rows = []
    for item in ranked:
        v = item.vehicle
        rows.append(
            {
                "rank": item.rank,
                "canonical_id": v.canonical_id(),
                "year": v.year,
                "make": v.make,
                "model": v.model,
                "priority_score": round(item.score.total, 4),
                "score_search": round(item.score.search, 4),
                "score_listings": round(item.score.listings, 4),
                "score_community": round(item.score.community, 4),
                "score_first_party": round(item.score.first_party, 4),
                "score_problems": round(item.score.problems, 4),
                "explanation": item.score.explanation,
                "riser": item.riser,
                "previous_rank": item.previous_rank,
                **{f"signal_{k}": v for k, v in item.signals.items()},
            }
        )

    df = pd.DataFrame(rows)
    df.to_csv(out / "top_models.csv", index=False)

    json_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "models": rows,
        "enrichment": [
            {
                "rank": item.rank,
                "canonical_id": item.vehicle.canonical_id(),
                "enrichment": item.enrichment.model_dump(mode="json") if item.enrichment else None,
            }
            for item in ranked
        ],
    }
    (out / "top_models.json").write_text(json.dumps(json_payload, indent=2), encoding="utf-8")
    _write_backlog(out / "backlog.md", ranked)
