from topmodels.config import PipelineConfig
from topmodels.models import VehicleKey
from topmodels.score import score_models


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
    scored = score_models(vehicles, aggregated, config)
    assert len(scored) == 2
    assert scored[0][1].total >= scored[1][1].total
    assert scored[0][1].explanation.startswith("Ranked high on")
