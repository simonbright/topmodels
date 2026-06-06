from topmodels.config import PipelineConfig
from topmodels.connectors.firstparty import FirstPartyConnector


def test_firstparty_excludes_demo():
    config = PipelineConfig()
    connector = FirstPartyConnector(config)
    models = connector.discover_models()
    labels = {m.display_label() for m in models}
    assert any("TACOMA" in label.upper() for label in labels)
    assert not any("CIVIC" in label.upper() for label in labels)

    records = connector.fetch(models)
    scan_recs = [r for r in records if r.signal == "first_party_scans"]
    assert sum(r.value for r in scan_recs) == 2
