"""Driver della mappa di idoneità statica (traccia A).

Lega motore (engine.static_scorer) + griglia (gis.grid) + validazione (gis.boyce).
La sorgente delle feature per cella è dietro l'interfaccia `FeatureProvider`: il
motore non sa da dove vengono. Oggi c'è solo lo `StubFeatureProvider` (wiring
end-to-end); i provider raster reali (DEM/forestale/suolo/disturbo) si innestano
implementando `features()` — vedi gis/README.md.
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod

from engine.profiles import SpeciesProfile
from engine.static_scorer import static_suitability

from . import boyce, grid


class FeatureProvider(ABC):
    """Fornisce il dict di feature statiche per una cella (spec §7.5)."""

    @abstractmethod
    def features(self, lat: float, lon: float) -> dict | None:
        """Feature per il punto, o None se fuori copertura/dati mancanti."""


class StubFeatureProvider(FeatureProvider):
    """Segnaposto: feature plausibili ma FISSE. Serve solo a far girare il driver.

    Sostituire con provider reali: un provider per layer (DEM, forestale, suolo,
    disturbo Sentinel-2), poi comporli in un CompositeFeatureProvider.
    """

    def __init__(self, cell: dict | None = None):
        self.cell = cell or {
            "host_class": "querce", "elevation_m": 450, "slope_deg": 15,
            "aspect": "warm", "soil_ph": "acidic", "drainage": "well_drained",
            "canopy_alive": 1.0,
        }

    def features(self, lat: float, lon: float) -> dict:
        return dict(self.cell)


def suitability_at(profile: SpeciesProfile, provider: FeatureProvider,
                   lat: float, lon: float) -> float | None:
    cell = provider.features(lat, lon)
    return None if cell is None else static_suitability(profile, cell)


def random_background(n: int, cfg: dict | None = None, seed: int = 0) -> list[tuple[float, float]]:
    """n punti (lat, lon) casuali nel bbox — il 'disponibile' per il Boyce (§6.3)."""
    cfg = cfg or grid._config()
    bb = cfg["bbox_wgs84"]
    rng = random.Random(seed)
    return [(rng.uniform(bb["lat_min"], bb["lat_max"]),
             rng.uniform(bb["lon_min"], bb["lon_max"])) for _ in range(n)]


def validate_species(profile: SpeciesProfile, provider: FeatureProvider,
                     presence_points: list[dict], n_background: int = 5000,
                     cfg: dict | None = None) -> dict:
    """Boyce index per una specie: presenze (GBIF) vs background casuale.

    NB: con lo StubFeatureProvider tutte le idoneità sono identiche → Boyce = NaN
    (nessuna discriminazione). Diventa informativo coi provider raster reali.
    """
    pres = [s for p in presence_points
            if (s := suitability_at(profile, provider, p["lat"], p["lon"])) is not None]
    bg = [s for lat, lon in random_background(n_background, cfg)
          if (s := suitability_at(profile, provider, lat, lon)) is not None]
    if not pres or not bg:
        return {"boyce": float("nan"), "n_presence": len(pres), "n_background": len(bg)}
    result = boyce.continuous_boyce(pres, bg)
    result.update({"n_presence": len(pres), "n_background": len(bg)})
    return result
