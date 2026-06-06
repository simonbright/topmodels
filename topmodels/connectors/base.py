"""Base connector contract."""

from __future__ import annotations

from abc import ABC, abstractmethod

from topmodels.models import SignalRecord, VehicleKey


class BaseConnector(ABC):
    name: str = "base"

    @abstractmethod
    def fetch(self, models: list[VehicleKey] | None = None) -> list[SignalRecord]:
        """Return attributed signals for seed models (or discover seeds when models is None)."""
