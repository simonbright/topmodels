from topmodels.models import SignalRecord, VehicleKey
from topmodels.normalize import aggregate_by_model, bucket_for_signal


def test_bucket_mapping():
    assert bucket_for_signal("search_interest") == "search"
    assert bucket_for_signal("problem_volume") == "problems"


def test_aggregate_problems_sums():
    vehicle = VehicleKey(year=2020, make="TOYOTA", model="TACOMA")
    records = [
        SignalRecord.from_vehicle(vehicle, signal="recall_count", value=2, source="NHTSA"),
        SignalRecord.from_vehicle(vehicle, signal="complaint_count", value=10, source="NHTSA"),
        SignalRecord.from_vehicle(vehicle, signal="investigation_count", value=0, source="NHTSA"),
        SignalRecord.from_vehicle(vehicle, signal="first_party_scans", value=3, source="telemetry"),
    ]
    agg = aggregate_by_model(records)
    assert agg[vehicle.canonical_id()]["problems"] == 12
    assert agg[vehicle.canonical_id()]["first_party"] == 3
