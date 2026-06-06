from topmodels.taxonomy import normalize_make, normalize_model, normalize_vehicle


def test_normalize_make_aliases():
    assert normalize_make("Chevy") == "CHEVROLET"
    assert normalize_make("Mercedes-Benz") == "MERCEDES-BENZ"


def test_normalize_model_strips_noise():
    assert normalize_model("Civic Sedan LX") == "CIVIC"


def test_normalize_vehicle():
    v = normalize_vehicle(2020, "Toyota", "Tacoma TRD")
    assert v is not None
    assert v.year == 2020
    assert v.make == "TOYOTA"
    assert "TACOMA" in v.model
