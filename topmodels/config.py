"""Load config.yaml and .env secrets."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field


ROOT = Path(__file__).resolve().parent.parent


class WeightsConfig(BaseModel):
    search: float = 0.55
    problems: float = 0.40
    first_party: float = 0.05
    listings: float = 0.0
    community: float = 0.0


class ActivationThresholdConfig(BaseModel):
    first_party_total_scans: int = 50


class CalibrationConfig(BaseModel):
    min_first_party_scans: int = 5
    min_sample_by_signal: dict[str, int] = Field(default_factory=lambda: {"first_party": 5})
    activation_threshold: ActivationThresholdConfig = Field(default_factory=ActivationThresholdConfig)
    shrinkage_prior: float = 1.0


class SourcesConfig(BaseModel):
    nhtsa: bool = True
    trends: bool = True
    firstparty: bool = True
    reports: bool = True
    reddit: bool = False
    keywordplanner: bool = False
    marketcheck: bool = False


class BudgetConfig(BaseModel):
    max_paid_calls: int = 0


class ModelYearWindow(BaseModel):
    min_years_ago: int = 3
    max_years_ago: int = 12


class FirstPartyConfig(BaseModel):
    export_path: str = "data/sample_telemetry_export.json"
    exclude_demo: bool = True


class TrendsConfig(BaseModel):
    geo: str = "US"
    timeframe: str = "today 12-m"
    request_pause_sec: float = 2.0


class NhtsaConfig(BaseModel):
    request_pause_sec: float = 0.35
    max_complaints_per_model: int = 500


class ReportsConfig(BaseModel):
    curated_path: str = "data/curated_reports.json"


class PipelineConfig(BaseModel):
    top_n: int = 10
    weights: WeightsConfig = Field(default_factory=WeightsConfig)
    calibration: CalibrationConfig = Field(default_factory=CalibrationConfig)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    model_year_window: ModelYearWindow = Field(default_factory=ModelYearWindow)
    firstparty: FirstPartyConfig = Field(default_factory=FirstPartyConfig)
    trends: TrendsConfig = Field(default_factory=TrendsConfig)
    nhtsa: NhtsaConfig = Field(default_factory=NhtsaConfig)
    reports: ReportsConfig = Field(default_factory=ReportsConfig)
    cache_dir: str = "cache"
    output_dir: str = "out"

    @property
    def cache_path(self) -> Path:
        return ROOT / self.cache_dir

    @property
    def output_path(self) -> Path:
        return ROOT / self.output_dir

    def firstparty_export_path(self) -> Path:
        override = os.getenv("FIRSTPARTY_EXPORT_PATH", "").strip()
        rel = override or self.firstparty.export_path
        p = Path(rel)
        return p if p.is_absolute() else ROOT / p

    def reports_path(self) -> Path:
        p = Path(self.reports.curated_path)
        return p if p.is_absolute() else ROOT / p


def load_config(config_path: Path | None = None) -> PipelineConfig:
    load_dotenv(ROOT / ".env")
    path = config_path or (ROOT / "config.yaml")
    raw: dict[str, Any] = {}
    if path.exists():
        with path.open(encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    return PipelineConfig.model_validate(raw)


def sources_for_phase(config: PipelineConfig, phase: int) -> SourcesConfig:
    """Enable connectors appropriate for the requested phase."""
    s = config.sources.model_copy(deep=True)
    if phase < 2:
        s.reddit = False
        s.keywordplanner = False
    if phase < 3:
        s.marketcheck = False
    return s
