from topmodels.calibration import AggregationBundle, BucketMeta
from topmodels.config import PipelineConfig
from topmodels.models import VehicleKey
from topmodels.score import score_models


def _bundle_for(aggregated: dict[str, dict[str, float]]) -> AggregationBundle:
    raw_by_key = {}
    meta_by_key = {}
    for key, buckets in aggregated.items():
        raw_by_key[key] = dict(buckets)
        meta_by_key[key] = {}
        for bucket, val in buckets.items():
            meta_by_key[key][bucket] = BucketMeta(
                raw=val,
                data_present=True,
                matched=True,
                eligible=True,
                sample_size=int(val) if bucket in ("first_party", "problems") else 0,
                confidence=0.85,
            )
    return AggregationBundle(raw_by_key=raw_by_key, meta_by_key=meta_by_key, total_first_party_scans=5)


def test_score_models_explainable():
    config = PipelineConfig()
    vehicles = {
        "2020|TOYOTA|TACOMA": VehicleKey(year=2020, make="TOYOTA", model="TACOMA"),
        "2017|HONDA|CIVIC": VehicleKey(year=2017, make="HONDA", model="CIVIC"),
    }
    aggregated = {
        "2020|TOYOTA|TACOMA": {"search": 80, "first_party": 5, "problems": 12},
        "2017|HONDA|CIVIC": {"search": 40, "first_party": 0, "problems": 30},
    }
    scored, gate_notes, effective = score_models(vehicles, _bundle_for(aggregated), config)
    assert len(scored) == 2
    assert scored[0][1].breakdown.total >= scored[1][1].breakdown.total
    assert scored[0][1].breakdown.explanation.startswith("Ranked high on")
    assert "first_party" in gate_notes
