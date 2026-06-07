"""Canonical vehicle key + NHTSA vPIC decode and alias resolution."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
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
def nhtsa_api_model_candidates(model_name: str) -> list[str]:
    """Generate NHTSA API model tokens to try (hyphen/spacing variants)."""
    raw = str(model_name).strip()
    if not raw:
        return []
    variants: list[str] = []
    seen: set[str] = set()

    def add(s: str) -> None:
        t = s.strip()
        if not t:
            return
        for fmt in (t, t.title(), t.upper()):
            key = fmt.lower()
            if key not in seen:
                seen.add(key)
                variants.append(fmt)

    add(raw)
    add(raw.replace("-", ""))
    add(raw.replace("-", " "))
    add(raw.replace(" ", ""))
    add(re.sub(r"\s+", " ", raw))
    return variants


@dataclass
class NhtsaModelResolution:
    api_make: str
    api_model: str
    vpic_matched: bool
    vpic_model_name: str | None = None
    candidates: list[str] = field(default_factory=list)


# Editorial names that differ from vPIC but NHTSA API accepts
NHTSA_EDITORIAL_ALIASES: dict[str, list[str]] = {
    "SILVERADO 1500": ["Silverado 1500", "Silverado LD", "Silverado"],
    "SILVERADO": ["Silverado", "Silverado LD"],
    "F-150": ["F-150", "F150", "F 150"],
    "F150": ["F-150", "F150", "F 150"],
}

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
        self._vpic_api_names: dict[tuple[str, int], dict[str, str]] = {}

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

    def _load_vpic_models(self, make: str, year: int) -> tuple[set[str], dict[str, str]]:
        cache_key = (make.upper(), year)
        if cache_key in self._make_model_cache:
            return self._make_model_cache[cache_key], self._vpic_api_names[cache_key]

        makes = self.all_makes()
        canonical = normalize_make(make)
        if not canonical:
            self._make_model_cache[cache_key] = set()
            self._vpic_api_names[cache_key] = {}
            return set(), {}

        make_name = makes.get(canonical, canonical.title())
        data = self.http.get_json(
            "vpic",
            f"{VPIC_BASE}/GetModelsForMakeYear/make/{make_name}/modelyear/{year}?format=json",
            refresh=self.refresh,
        )
        models: set[str] = set()
        api_names: dict[str, str] = {}
        for row in data.get("Results", []):
            model_name = str(row.get("Model_Name", "")).strip()
            norm = normalize_model(model_name)
            if norm:
                models.add(norm)
                api_names[norm] = model_name
        self._make_model_cache[cache_key] = models
        self._vpic_api_names[cache_key] = api_names
        return models, api_names

    def models_for_make_year(self, make: str, year: int) -> set[str]:
        models, _ = self._load_vpic_models(make, year)
        return models

    def resolve_nhtsa_query(self, vehicle: VehicleKey) -> NhtsaModelResolution:
        """Map canonical model to vPIC + NHTSA API model token."""
        mk = normalize_make(vehicle.make)
        md = normalize_model(vehicle.model)
        makes = self.all_makes()
        api_make = makes.get(mk or "", (mk or vehicle.make).title())

        vpic_models, api_names = self._load_vpic_models(vehicle.make, vehicle.year)
        vpic_matched = False
        vpic_name: str | None = None
        resolved_norm = md or vehicle.model.upper()

        if vpic_models and md:
            if md in vpic_models:
                vpic_matched = True
                vpic_name = api_names.get(md)
                resolved_norm = md
            else:
                prefix_hits = [
                    m
                    for m in vpic_models
                    if m.startswith(md) or md.startswith(m.split()[0])
                ]
                if len(prefix_hits) == 1:
                    vpic_matched = True
                    resolved_norm = prefix_hits[0]
                    vpic_name = api_names.get(resolved_norm)
                else:
                    token_hits = [m for m in vpic_models if md.split()[0] in m.split()]
                    if len(token_hits) == 1:
                        vpic_matched = True
                        resolved_norm = token_hits[0]
                        vpic_name = api_names.get(resolved_norm)

        seed = vpic_name or vehicle.model
        candidates = nhtsa_api_model_candidates(seed)
        if vpic_name and vpic_name not in candidates:
            candidates.insert(0, vpic_name)

        md_key = (md or vehicle.model.upper()).strip()
        for alias_key, alias_tokens in NHTSA_EDITORIAL_ALIASES.items():
            if alias_key in md_key or md_key.startswith(alias_key.split()[0]):
                for token in reversed(alias_tokens):
                    if token not in candidates:
                        candidates.insert(0, token)

        api_model = candidates[0] if candidates else vehicle.model.title()
        return NhtsaModelResolution(
            api_make=api_make,
            api_model=api_model,
            vpic_matched=vpic_matched,
            vpic_model_name=vpic_name,
            candidates=candidates,
        )

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
