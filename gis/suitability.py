"""Driver della mappa di idoneità statica (traccia A).

Lega motore (engine.static_scorer) + griglia (gis.grid) + validazione (gis.boyce).
La sorgente delle feature per cella è dietro l'interfaccia `FeatureProvider`: il
motore non sa da dove vengono. Oggi c'è solo lo `StubFeatureProvider` (wiring
end-to-end); i provider raster reali (DEM/forestale/suolo/disturbo) si innestano
implementando `features()` — vedi gis/README.md.
"""

from __future__ import annotations

import math
import random
from abc import ABC, abstractmethod

from engine.profiles import SpeciesProfile
from engine.static_scorer import static_suitability

from . import boyce, grid


class FeatureProvider(ABC):
    """Fornisce il dict di feature statiche per una cella (spec §7.5)."""

    #: Se True, un None di questo provider annulla l'intera cella nel composite.
    #: Default False: un layer tematico che tace lascia il fattore neutro
    #: ("unknown != absent"). Lo alzano solo i provider che definiscono *dove*
    #: ha senso scorare — l'AOI (area con dati tematici) e il DEM.
    is_required = False

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


# Raggio dell'intorno usato in validazione: l'ordine di grandezza dell'incertezza di una
# segnalazione GBIF. Le celle sono da 200 m e le coordinate valgono spesso qualche
# centinaio di metri: scorare il pixel esatto chiede al modello di indovinare un posto
# dove il fungo non era, e dal 25 al 47% delle presenze finisce a zero. Vedi
# docs/COME-FUNZIONA.md, "un punto GBIF non è un pixel".
NEIGHBOURHOOD_M = 250.0


def neighbourhood(lat: float, lon: float, radius_m: float = NEIGHBOURHOOD_M):
    """I 9 punti di un intorno 3×3 attorno a (lat, lon)."""
    dlat = radius_m / 110_540.0
    dlon = radius_m / (111_320.0 * math.cos(math.radians(lat)))
    return [(lat + i * dlat, lon + j * dlon) for i in (-1, 0, 1) for j in (-1, 0, 1)]


def cells_at(provider: FeatureProvider, points, radius_m: float | None = NEIGHBOURHOOD_M):
    """Feature per ogni punto: una lista di celle (l'intorno) o la singola cella.

    Le feature sono species-agnostic, quindi si interrogano UNA volta e poi si scorano
    tutte le specie: senza questo, l'intorno moltiplicherebbe per nove il costo di ogni
    validazione. Ritorna [] per i punti fuori copertura, che il chiamante salta.
    """
    out = []
    for lat, lon in points:
        pts = [(lat, lon)] if radius_m is None else neighbourhood(lat, lon, radius_m)
        out.append([c for c in (provider.features(a, b) for a, b in pts) if c is not None])
    return out


def score_cells(profile: SpeciesProfile, cells) -> list[float]:
    """Punteggio per punto: il MASSIMO dell'intorno (col pixel singolo è il pixel)."""
    return [max(static_suitability(profile, c) for c in group) for group in cells if group]


def validate_species(profile: SpeciesProfile, cells_presence, cells_background) -> dict:
    """Boyce index per una specie, da celle già interrogate (vedi `cells_at`).

    Presenze e background devono passare per lo STESSO operatore: applicare l'intorno solo
    alle presenze gonfia il numeratore e misura l'operatore invece del modello (+0.9
    contro +0.57 sul porcino).
    """
    pres = score_cells(profile, cells_presence)
    bg = score_cells(profile, cells_background)
    if not pres or not bg:
        return {"boyce": float("nan"), "n_presence": len(pres), "n_background": len(bg)}
    result = boyce.continuous_boyce(pres, bg)
    result.update({"n_presence": len(pres), "n_background": len(bg)})
    return result
