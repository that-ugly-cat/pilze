"""Scorer STATICO (spec §7.2, §7.5): idoneità dell'habitat ∈ [0,1].

f(feature_cella, profilo) → punteggio. Species-agnostic: la specie entra solo
via il profilo. L'host è un GATE moltiplicativo (niente ospite → niente funghi,
spec §1); i fattori ambientali si combinano in media geometrica pesata (morbida:
un singolo fattore mediocre non azzera).

Feature di cella attese (tutte opzionali, None => fattore neutro):
    host        : dict {classe_crosswalk: frazione}  oppure  host_class: str
    canopy_alive: float [0,1] — chioma viva (declassa ospiti conifera, Vaia/bostrico §3.1)
    elevation_m, slope_deg : float
    aspect      : 'warm'|'cool'|'neutral'
    soil_ph     : 'acidic'|'neutral'|'calcareous'
    drainage    : 'well_drained'|'moist'|'dry'|'waterlogged'
"""

from __future__ import annotations

import math

from . import membership as m
from .profiles import SpeciesProfile

CONIFER_HOSTS = {"abete", "pino", "altro_conifere"}

DEFAULT_WEIGHTS = {
    "elevation": 1.0,
    "slope": 0.5,
    "aspect": 0.8,
    "soil_ph": 0.8,
    "drainage": 0.5,
}


def host_membership(profile: SpeciesProfile, cell: dict) -> float:
    """Match ospite pesato dal crosswalk, declassato dalla chioma morta per le conifere."""
    if not profile.is_mycorrhizal:
        return 1.0  # saprotrofi/facoltative: l'host non è il gate (usano extra_static_layers)
    canopy_alive = cell.get("canopy_alive")
    comp = cell.get("host")
    if comp is None and cell.get("host_class") is not None:
        comp = {cell["host_class"]: 1.0}
    if comp is None:
        # host SCONOSCIUTO (layer forestale non ancora presente) → neutro, non gate.
        # Distinto da host noto-ma-assente (dict vuoto sotto → 0): unknown ≠ absent.
        return 1.0
    if not comp:
        return 0.0  # micorrizico con ospite noto e nessun genere compatibile → niente funghi
    total = 0.0
    for cls, frac in comp.items():
        w = profile.host_genera.get(cls, 0.0)
        if cls in CONIFER_HOSTS and canopy_alive is not None:
            w *= float(canopy_alive)          # declassa dove la chioma è morta
        total += float(frac) * w
    return m.clamp01(total)


def _weighted_geomean(factors: dict[str, float], weights: dict[str, float]) -> float:
    num = 0.0
    den = 0.0
    for k, v in factors.items():
        w = weights.get(k, 1.0)
        if w <= 0:
            continue
        num += w * math.log(max(v, 1e-6))     # floor per evitare log(0) = -inf
        den += w
    return math.exp(num / den) if den else 0.0


def static_suitability(profile: SpeciesProfile, cell: dict,
                       weights: dict[str, float] | None = None,
                       breakdown: bool = False):
    """Idoneità statica ∈ [0,1]. Se breakdown=True ritorna (score, {fattore: membership})."""
    weights = weights or DEFAULT_WEIGHTS
    env = profile.static_envelope

    factors = {
        "elevation": m.envelope_membership(cell.get("elevation_m"),
                                           env.get("elevation_m", {}), default_margin=200.0),
        "slope": m.envelope_membership(cell.get("slope_deg"),
                                       env.get("slope_deg", {}), default_margin=10.0),
        "aspect": m.categorical_membership("aspect", env.get("aspect"), cell.get("aspect")),
        "soil_ph": m.categorical_membership("soil_ph", env.get("soil_ph"), cell.get("soil_ph")),
        "drainage": m.categorical_membership("drainage", env.get("drainage"), cell.get("drainage")),
    }
    host = host_membership(profile, cell)
    score = host * _weighted_geomean(factors, weights)     # host = gate, env = graded
    # gate "è bosco?" a copertura completa (WorldCover): fuori-bosco → 0 (spec §3.1).
    # Assente → 1.0 (nessun gate), così resta valido senza il layer.
    score *= float(cell.get("forest_fraction", 1.0))

    if breakdown:
        return score, {"host": host, "forest_fraction": cell.get("forest_fraction", 1.0), **factors}
    return score
