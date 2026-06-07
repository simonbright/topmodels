from topmodels.calibration import (
    AggregationBundle,
    BucketMeta,
    compute_effective_weights,
    shrinkage_normalize,
)
from topmodels.config import PipelineConfig
from topmodels.models import SignalRecord, VehicleKey
from topmodels.normalize import aggregate_signals
from topmodels.score import score_models


def test_shrinkage_single_point_never_reaches_one():
    raw = {"a": 2.0, "b": 0.0}
    eligible = {"a": True, "b": False}
    normed = shrinkage_normalize(raw, eligible, prior=1.0)
    assert normed["a"] < 1.0
    assert normed["a"] <= 0.95


def test_first_party_gated_below_activation_threshold():
    config = PipelineConfig()
    bundle = AggregationBundle(total_first_party_scans=2)
    notes: dict[str, str] = {}
    _, effective = compute_effective_weights(config, bundle, gate_notes=notes)
    assert effective["first_party"] == 0.0
    assert "gated" in notes["first_party"]


def test_small_n_first_party_not_inflated():
    config = PipelineConfig()
    vehicle = VehicleKey(year=2020, make="TOYOTA", model="TACOMA")
    records = [
        SignalRecord.from_vehicle(
            vehicle, signal="first_party_scans", value=2, source="telemetry"
        ),
        SignalRecord.from_vehicle(vehicle, signal="search_interest", value=50, source="trends"),
    ]
    bundle = aggregate_signals(records, config)
    vehicles = {vehicle.canonical_id(): vehicle}
    scored, _, _ = score_models(vehicles, bundle, config)
    fp_norm = scored[0][1].signal_meta["first_party"]["normalized"]
    assert fp_norm == 0.0
    meta = bundle.meta_by_key[vehicle.canonical_id()]["first_party"]
    assert not meta.eligible


def test_missing_problems_excluded_not_zero():
    config = PipelineConfig()
    vehicle = VehicleKey(year=2020, make="FORD", model="F-150")
    records = [
        SignalRecord.from_vehicle(
            vehicle,
            signal="recall_count",
            value=0,
            source="NHTSA",
            metadata={"data_present": False, "matched": False},
        ),
        SignalRecord.from_vehicle(
            vehicle,
            signal="complaint_count",
            value=0,
            source="NHTSA",
            metadata={"data_present": False, "matched": False},
        ),
        SignalRecord.from_vehicle(
            vehicle,
            signal="investigation_count",
            value=0,
            source="NHTSA",
            metadata={"data_present": False, "matched": False},
        ),
        SignalRecord.from_vehicle(vehicle, signal="search_interest", value=80, source="trends"),
    ]
    bundle = aggregate_signals(records, config)
    assert "problems" not in bundle.raw_by_key.get(vehicle.canonical_id(), {})
    meta = bundle.meta_by_key[vehicle.canonical_id()]["problems"]
    assert not meta.data_present
