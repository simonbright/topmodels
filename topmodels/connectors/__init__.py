"""Data source connectors — each returns list[SignalRecord]."""

from topmodels.connectors.firstparty import FirstPartyConnector
from topmodels.connectors.nhtsa import NhtsaConnector
from topmodels.connectors.reports import ReportsConnector
from topmodels.connectors.trends import TrendsConnector

__all__ = [
    "FirstPartyConnector",
    "NhtsaConnector",
    "ReportsConnector",
    "TrendsConnector",
]
