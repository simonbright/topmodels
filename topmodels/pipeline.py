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
from topmodels.calibration import SIGNAL_BUCKETS
from topmodels.normalize import aggregate_signals, normalize_records
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
    taxonomy: Taxonomy,
    *,
    refresh: bool,
) -> tuple[list, list[dict]]:
    from topmodels.models import SignalRecord

    records: list[SignalRecord] = []
    nhtsa_misses: list[dict] = []
    http = CachedHttpClient(config.cache_path)

    if sources.reports:
        records.extend(ReportsConnector(config).fetch(models))
    if sources.firstparty:
        records.extend(FirstPartyConnector(config, refresh=refresh).fetch(models))
    if sources.nhtsa:
        nhtsa = NhtsaConnector(config, http, taxonomy, refresh=refresh)
        records.extend(nhtsa.fetch(models))
        nhtsa_misses = list(nhtsa.miss_log)
    if sources.trends:
        records.extend(TrendsConnector(config, refresh=refresh).fetch(models))

    return records, nhtsa_misses


def _load_previous_ranks(output_dir: Path) -> dict[str, int]:
    prev_path = output_dir / "top_models.json"
    if not prev_path.exists():
        return {}
    try:
        payload = json.loads(prev_path.read_text(encoding="utf-8"))
        return {row["canonical_id"]: int(row["rank"]) for row in payload.get("models", [])}
    except (json.JSONDecodeError, KeyError, TypeError):
        return {}


def _count_cache_files(cache_root: Path, namespace: str) -> int:
    d = cache_root / namespace
    if not d.exists():
        return 0
    return sum(1 for p in d.rglob("*") if p.is_file())


def _trends_health(cache_root: Path) -> dict:
    d = cache_root / "trends"
    if not d.exists():
        return {"cached_keywords": 0, "with_data": 0, "failed": 0}
    with_data = failed = 0
    for f in d.glob("*.json"):
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
            if payload.get("mean_interest") is None or payload.get("error"):
                failed += 1
            else:
                with_data += 1
        except json.JSONDecodeError:
            failed += 1
    return {"cached_keywords": with_data + failed, "with_data": with_data, "failed": failed}


def _signal_coverage_counts(ranked: list, bucket: str) -> tuple[int, int, int]:
    """Return (with_data, missing, gated) for a signal bucket across ranked models."""
    with_data = missing = gated = 0
    for item in ranked:
        meta = item.signal_meta.get(bucket, {})
        if meta.get("eligible") and meta.get("data_present") and meta.get("normalized", 0) > 0:
            with_data += 1
        elif not meta.get("data_present", True):
            missing += 1
        elif not meta.get("eligible", True):
            gated += 1
    return with_data, missing, gated


def _build_coverage_report(
    bundle,
    ranked: list,
    vehicles: dict,
    gate_notes: dict,
    effective_weights: dict,
    nhtsa_misses: list,
) -> dict:
    per_signal = {}
    for bucket in SIGNAL_BUCKETS:
        with_data, missing, gated = _signal_coverage_counts(ranked, bucket)
        per_signal[bucket] = {
            "with_data": with_data,
            "missing": missing,
            "gated": gated,
            "effective_weight": effective_weights.get(bucket, 0.0),
            "gate_note": gate_notes.get(bucket, ""),
        }

    models_detail = []
    for item in ranked:
        v = item.vehicle
        models_detail.append(
            {
                "canonical_id": v.canonical_id(),
                "label": v.display_label(),
                "model_confidence": item.model_confidence,
                "signals": item.signal_meta,
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "per_signal": per_signal,
        "nhtsa_unmatched_or_empty": nhtsa_misses,
        "gate_notes": gate_notes,
        "effective_weights": effective_weights,
        "total_first_party_scans": bundle.total_first_party_scans,
        "models": models_detail,
        "seed_count": len(vehicles),
    }


def _coverage_summary_line(coverage_report: dict, ranked_count: int) -> str:
    parts = []
    for bucket in SIGNAL_BUCKETS:
        ps = coverage_report["per_signal"][bucket]
        note = ps.get("gate_note") or ""
        if note and "gated" in note:
            parts.append(f"{bucket} gated")
        elif ps["effective_weight"] <= 0:
            parts.append(f"{bucket} off")
        elif ps["missing"]:
            parts.append(f"{bucket} {ps['with_data']}/{ranked_count} ({ps['missing']} missing)")
        else:
            parts.append(f"{bucket} {ps['with_data']}/{ranked_count}")
    return "Coverage: " + " · ".join(parts)


def _write_coverage_report(out: Path, report: dict) -> None:
    (out / "coverage_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    lines = [
        "# Coverage Report",
        "",
        f"_Generated {report['generated_at']}_",
        "",
        "## Per signal",
        "",
    ]
    for bucket, ps in report["per_signal"].items():
        gate = f" — {ps['gate_note']}" if ps.get("gate_note") else ""
        lines.append(
            f"- **{bucket}**: {ps['with_data']} with data, {ps['missing']} missing, "
            f"{ps['gated']} gated · effective weight {ps['effective_weight']:.2f}{gate}"
        )
    if report.get("nhtsa_unmatched_or_empty"):
        lines.extend(["", "## NHTSA unmatched / empty queries", ""])
        for miss in report["nhtsa_unmatched_or_empty"]:
            lines.append(f"- {miss.get('vehicle', '?')}: {miss.get('reason', miss.get('error', 'unknown'))}")
    (out / "coverage_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _build_run_meta(
    config: PipelineConfig,
    *,
    phase: int,
    sources,
    seeds: list,
    models: list,
    raw_records: list,
    ranked: list,
    top_n: int,
    gate_notes: dict | None = None,
    effective_weights: dict | None = None,
    coverage_summary: str = "",
) -> dict:
    from collections import Counter

    from topmodels.models import SignalRecord

    by_source = Counter()
    by_signal = Counter()
    for rec in raw_records:
        if isinstance(rec, SignalRecord):
            by_source[rec.source.split("(")[0].strip()] += 1
            by_signal[rec.signal] += 1

    cache_root = config.cache_path
    fp_path = config.firstparty_export_path()
    reports_path = config.reports_path()

    source_rows = []
    connector_specs = [
        ("reports", sources.reports, "data/curated_reports.json", None),
        ("firstparty", sources.firstparty, str(fp_path), None),
        ("nhtsa", sources.nhtsa, "cache/nhtsa", ["recalls", "complaints", "investigations"]),
        ("trends", sources.trends, "cache/trends", None),
        ("reddit", sources.reddit, None, None),
        ("keywordplanner", sources.keywordplanner, None, None),
        ("marketcheck", sources.marketcheck, None, None),
    ]

    for name, enabled, path_hint, cache_ns_list in connector_specs:
        row = {
            "connector": name,
            "enabled": bool(enabled),
            "status": "disabled",
            "records": 0,
            "cache_files": 0,
            "notes": "",
        }
        if not enabled:
            row["notes"] = "Off in config.yaml (Phase 2/3 or manual toggle)."
            source_rows.append(row)
            continue

        if name == "reports":
            row["records"] = sum(1 for r in raw_records if getattr(r, "signal", "") == "report_rank_score")
            row["cache_files"] = 0
            row["status"] = "ok" if reports_path.exists() else "missing_input"
            if not reports_path.exists():
                row["notes"] = f"Missing {reports_path}"
        elif name == "firstparty":
            row["records"] = sum(
                1 for r in raw_records if getattr(r, "signal", "").startswith("first_party")
            )
            row["status"] = "ok" if fp_path.exists() else "missing_input"
            if not fp_path.exists():
                row["notes"] = f"No telemetry export at {fp_path}"
            else:
                row["notes"] = f"Reading {fp_path.name}"
        elif name == "nhtsa":
            row["records"] = sum(
                1
                for r in raw_records
                if "NHTSA" in getattr(r, "source", "")
            )
            row["cache_files"] = sum(_count_cache_files(cache_root, ns) for ns in (cache_ns_list or []))
            row["cache_files"] += _count_cache_files(cache_root, "vpic")
            row["status"] = "ok" if row["records"] else "no_data"
        elif name == "trends":
            row["records"] = sum(1 for r in raw_records if getattr(r, "signal", "") == "search_interest")
            th = _trends_health(cache_root)
            row["cache_files"] = th["cached_keywords"]
            if th["failed"] and th["with_data"]:
                row["status"] = "partial"
                row["notes"] = f"pytrends: {th['with_data']} ok, {th['failed']} failed (rate limit/brittle)"
            elif th["failed"]:
                row["status"] = "degraded"
                row["notes"] = "pytrends requests failed — search scores may be empty"
            elif row["records"]:
                row["status"] = "ok"
            else:
                row["status"] = "no_data"
                row["notes"] = "No search_interest records emitted"

        source_rows.append(row)

    warnings = []
    if not any(r["status"] == "ok" for r in source_rows if r["enabled"]):
        warnings.append("No connector reported ok status.")
    if sum(1 for r in source_rows if r.get("status") == "partial") > 0:
        warnings.append("Some connectors returned partial data — see notes.")
    disabled_paid = [n for n, e in sources.model_dump().items() if not e and n in ("reddit", "keywordplanner", "marketcheck")]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "phase": phase,
        "top_n": top_n,
        "seed_count": len(seeds),
        "taxonomy_resolved_count": len(models),
        "ranked_count": len(ranked),
        "raw_record_count": len(raw_records),
        "signals_emitted": dict(by_signal),
        "sources": source_rows,
        "warnings": warnings,
        "disabled_by_phase": disabled_paid,
        "gate_notes": gate_notes or {},
        "effective_weights": effective_weights or {},
        "coverage_summary": coverage_summary,
        "inputs": {
            "curated_reports": str(reports_path),
            "curated_reports_exists": reports_path.exists(),
            "firstparty_export": str(fp_path),
            "firstparty_export_exists": fp_path.exists(),
        },
    }


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
    raw_records, nhtsa_misses = _fetch_signals(
        config, sources, models, taxonomy, refresh=refresh
    )
    normalized = normalize_records(raw_records, taxonomy)
    bundle = aggregate_signals(normalized, config, nhtsa_misses=nhtsa_misses)

    vehicles = {m.canonical_id(): m for m in models}
    for key in bundle.raw_by_key:
        if key not in vehicles:
            parts = key.split("|")
            if len(parts) >= 3:
                vehicles[key] = VehicleKey(year=int(parts[0]), make=parts[1], model=parts[2])

    scored, gate_notes, effective_weights = score_models(vehicles, bundle, config)
    previous = _load_previous_ranks(config.output_path)

    ranked: list[RankedModel] = []
    for idx, (key, result) in enumerate(scored[:n], start=1):
        prev_rank = previous.get(key)
        riser = prev_rank is not None and idx < prev_rank
        ranked.append(
            RankedModel(
                rank=idx,
                vehicle=vehicles[key],
                score=result.breakdown,
                signals=result.signals,
                signal_meta=result.signal_meta,
                model_confidence=result.model_confidence,
                riser=riser,
                previous_rank=prev_rank,
            )
        )

    if not dry_run and sources.nhtsa:
        ranked = enrich_top_models(ranked, config, taxonomy, refresh=refresh)

    coverage_report = _build_coverage_report(
        bundle, ranked, vehicles, gate_notes, effective_weights, nhtsa_misses
    )
    coverage_summary = _coverage_summary_line(coverage_report, len(ranked))

    if dry_run:
        return ranked

    config.output_path.mkdir(parents=True, exist_ok=True)
    run_meta = _build_run_meta(
        config,
        phase=phase,
        sources=sources,
        seeds=seeds,
        models=models,
        raw_records=raw_records,
        ranked=ranked,
        top_n=n,
        gate_notes=gate_notes,
        effective_weights=effective_weights,
        coverage_summary=coverage_summary,
    )
    _write_outputs(config, ranked, run_meta=run_meta, coverage_report=coverage_report)
    return ranked


def _write_outputs(
    config: PipelineConfig,
    ranked: list[RankedModel],
    *,
    run_meta: dict | None = None,
    coverage_report: dict | None = None,
) -> None:
    out = config.output_path
    rows = []
    for item in ranked:
        v = item.vehicle
        row = {
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
            "model_confidence": item.model_confidence,
            "explanation": item.score.explanation,
            "riser": item.riser,
            "previous_rank": item.previous_rank,
            **{f"signal_{k}": val for k, val in item.signals.items()},
        }
        for bucket, meta in item.signal_meta.items():
            row[f"matched_{bucket}"] = meta.get("matched")
            row[f"data_present_{bucket}"] = meta.get("data_present")
            row[f"confidence_{bucket}"] = meta.get("confidence")
            row[f"sample_size_{bucket}"] = meta.get("sample_size")
        rows.append(row)

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
    if run_meta:
        (out / "run_meta.json").write_text(json.dumps(run_meta, indent=2), encoding="utf-8")
    if coverage_report:
        _write_coverage_report(out, coverage_report)
    _write_backlog(out / "backlog.md", ranked)
