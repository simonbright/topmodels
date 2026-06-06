"""Canonical vehicle key + NHTSA vPIC decode and alias resolution."""

from __future__ import annotations

import re
from typing import Iterable

from topmodels.http_client import CachedHttpClient
from topmodels.models import VehicleKey

VPIC_BASE = "https://vpic.nhtsa.dot.gov/api/vehicles"

# Common marketplace / editorial aliases → canonical make
MAKE_ALIASES: dict[str, str] = {
    "CHEVROLET": "CHEVROLET",
    "CHEVY": "CHEVROLET",
    "GMC": "GMC",
    "MERCEDES-BENZ": "MERCEDES-BENZ",
    "MERCEDES BENZ": "MERCEDES-BENZ",
    "MERCEDES": "MERCEDES-BENZ",
    "BMW": "BMW",
    "VW": "VOLKSWAGEN",
    "VOLKSWAGEN": "VOLKSWAGEN",
    "TOYOTA": "TOYOTA",
    "HONDA": "HONDA",
    "FORD": "FORD",
    "NISSAN": "NISSAN",
    "HYUNDAI": "HYUNDAI",
    "KIA": "KIA",
    "SUBARU": "SUBARU",
    "MAZDA": "MAZDA",
    "JEEP": "JEEP",
    "RAM": "RAM",
    "DODGE": "DODGE",
    "LEXUS": "LEXUS",
    "ACURA": "ACURA",
    "INFINITI": "INFINITI",
    "AUDI": "AUDI",
    "VOLVO": "VOLVO",
    "TESLA": "TESLA",
}

# Trim / body-style tokens stripped for model-level joins
MODEL_NOISE = re.compile(
    r"\b(sedan|coupe|hatchback|wagon|suv|awd|fwd|4wd|2wd|hybrid|plug-in|phev|ev|"
    r"limited|premium|sport|touring|lx|ex|se|le|xle|sr5|trd|crew cab|extended cab)\b",
    re.I,
)


def normalize_make(make: str | None) -> str | None:
    if not make:
        return None
    key = re.sub(r"[^A-Za-z0-9\- ]", "", str(make).strip()).upper()
    key = re.sub(r"\s+", " ", key)
    return MAKE_ALIASES.get(key, key)


def normalize_model(model: str | None) -> str | None:
    if not model:
        return None
    m = re.sub(r"[^A-Za-z0-9\- /]", "", str(model).strip())
    m = MODEL_NOISE.sub("", m)
    m = re.sub(r"\s+", " ", m).strip()
    return m.upper() if m else None


def normalize_vehicle(
    year: int | str | None,
    make: str | None,
    model: str | None,
    *,
    generation: str | None = None,
    trim: str | None = None,
) -> VehicleKey | None:
    mk = normalize_make(make)
    md = normalize_model(model)
    if not mk or not md:
        return None
    return VehicleKey.from_parts(year, mk, md, generation=generation, trim=trim)


class Taxonomy:
    """Resolve aliases and optionally validate against NHTSA vPIC."""

    def __init__(self, http: CachedHttpClient, *, refresh: bool = False) -> None:
        self.http = http
        self.refresh = refresh
        self._make_model_cache: dict[tuple[str, int], set[str]] = {}

    def all_makes(self) -> dict[str, str]:
        """Return upper make name → NHTSA Make_Name."""
        if getattr(self, "_all_makes_cache", None) and not self.refresh:
            return self._all_makes_cache
        data = self.http.get_json(
            "vpic",
            f"{VPIC_BASE}/GetAllMakes?format=json",
            refresh=self.refresh,
        )
        results: dict[str, str] = {}
        for row in data.get("Results", []):
            name = str(row.get("Make_Name", "")).strip()
            if not name:
                continue
            norm = normalize_make(name)
            if norm:
                results[norm] = name
        self._all_makes_cache = results
        return results

    def models_for_make_year(self, make: str, year: int) -> set[str]:
        cache_key = (make.upper(), year)
        if cache_key in self._make_model_cache:
            return self._make_model_cache[cache_key]

        makes = self.all_makes()
        canonical = normalize_make(make)
        if not canonical:
            return set()
        make_name = makes.get(canonical, canonical.title())

        data = self.http.get_json(
            "vpic",
            f"{VPIC_BASE}/GetModelsForMakeYear/make/{make_name}/modelyear/{year}?format=json",
            refresh=self.refresh,
        )
        models: set[str] = set()
        for row in data.get("Results", []):
            model_name = str(row.get("Model_Name", "")).strip()
            norm = normalize_model(model_name)
            if norm:
                models.add(norm)
        self._make_model_cache[cache_key] = models
        return models

    def resolve(self, vehicle: VehicleKey) -> VehicleKey:
        """Map editorial aliases onto canonical make/model; fuzzy-match model to vPIC when possible."""
        mk = normalize_make(vehicle.make)
        md = normalize_model(vehicle.model)
        if not mk or not md:
            return vehicle

        vpic_models = self.models_for_make_year(mk, vehicle.year)
        resolved_model = md
        if vpic_models and md not in vpic_models:
            # Prefix match: "CIVIC" matches "CIVIC SI"
            prefix_hits = [m for m in vpic_models if m.startswith(md) or md.startswith(m.split()[0])]
            if len(prefix_hits) == 1:
                resolved_model = prefix_hits[0]
            elif md in {m.split()[0] for m in vpic_models}:
                hits = [m for m in vpic_models if m.split()[0] == md]
                if len(hits) == 1:
                    resolved_model = hits[0]

        return VehicleKey(
            year=vehicle.year,
            make=mk,
            model=resolved_model,
            generation=vehicle.generation,
            trim=vehicle.trim,
        )

    def resolve_many(self, vehicles: Iterable[VehicleKey]) -> list[VehicleKey]:
        seen: set[str] = set()
        out: list[VehicleKey] = []
        for v in vehicles:
            resolved = self.resolve(v)
            cid = resolved.canonical_id()
            if cid in seen:
                continue
            seen.add(cid)
            out.append(resolved)
        return out

    def decode_vin(self, vin: str) -> VehicleKey | None:
        vin_clean = re.sub(r"[^A-HJ-NPR-Z0-9]", "", str(vin).upper())
        if len(vin_clean) != 17:
            return None
        data = self.http.get_json(
            "vpic",
            f"{VPIC_BASE}/DecodeVinValues/{vin_clean}?format=json",
            refresh=self.refresh,
        )
        rows = data.get("Results") or []
        if not rows:
            return None
        row = rows[0]
        return normalize_vehicle(
            row.get("ModelYear"),
            row.get("Make"),
            row.get("Model"),
            trim=row.get("Trim") or None,
        )
