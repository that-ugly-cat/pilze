"""Combiner (spec §1, §7.2): predizione = idoneità_statica × readiness_dinamica.

Il prodotto è voluto: uno zero in un fattore azzera l'output
(niente ospite → niente funghi comunque; suolo secco → nessuna buttata comunque).
"""

from __future__ import annotations

from .dynamic_scorer import readiness
from .profiles import SpeciesProfile
from .static_scorer import static_suitability


def predict(profile: SpeciesProfile, static_cell: dict, meteo_feat: dict) -> float:
    """predizione(cella, specie, giorno) ∈ [0,1]."""
    return static_suitability(profile, static_cell) * readiness(profile, meteo_feat)
