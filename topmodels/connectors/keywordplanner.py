"""Google Keyword Planner search volume — Phase 2 (disabled by default)."""

from __future__ import annotations

from topmodels.connectors.base import BaseConnector
from topmodels.models import SignalRecord, VehicleKey


class KeywordPlannerConnector(BaseConnector):
    name = "keywordplanner"

    def fetch(self, models: list[VehicleKey] | None = None) -> list[SignalRecord]:
        raise NotImplementedError(
            "Keyword Planner connector is Phase 2. Enable sources.keywordplanner after Google Ads API setup."
        )
