from pathlib import Path

from topmodels.config import PipelineConfig
from topmodels.connectors.reports import ReportsConnector


def test_reports_connector_loads_curated():
    config = PipelineConfig()
    connector = ReportsConnector(config)
    models = connector.discover_models()
    assert len(models) >= 10
    records = connector.fetch(models[:3])
    assert all(r.signal == "report_rank_score" for r in records)
    assert all(r.source for r in records)
    assert all(r.metadata.get("source_url") for r in records)
