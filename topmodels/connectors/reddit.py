"""Reddit model-mention counts — Phase 2 (disabled by default)."""

from __future__ import annotations

from topmodels.connectors.base import BaseConnector
from topmodels.models import SignalRecord, VehicleKey


class RedditConnector(BaseConnector):
    name = "reddit"

    def fetch(self, models: list[VehicleKey] | None = None) -> list[SignalRecord]:
        raise NotImplementedError(
            "Reddit connector is Phase 2. Enable sources.reddit in config after PRAW credentials are set."
        )
