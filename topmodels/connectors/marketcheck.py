"""MarketCheck listing volume / MDS / price stats — Phase 3 (paid, gated)."""

from __future__ import annotations

from topmodels.config import PipelineConfig
from topmodels.connectors.base import BaseConnector
from topmodels.models import SignalRecord, VehicleKey


class MarketCheckConnector(BaseConnector):
    name = "marketcheck"

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config

    def fetch(self, models: list[VehicleKey] | None = None) -> list[SignalRecord]:
        if not self.config.sources.marketcheck:
            return []
        if self.config.budget.max_paid_calls <= 0:
            raise RuntimeError(
                "MarketCheck is paid. Set budget.max_paid_calls > 0 and MARKETCHECK_API_KEY in .env (Phase 3)."
            )
        raise NotImplementedError("MarketCheck connector ships in Phase 3.")
