"""Motore generico Mappa Funghi (species-agnostic).

Le specie entrano solo come profili dichiarativi (profiles/*.yaml, spec §7).
"""

from .combiner import predict
from .dynamic_scorer import readiness
from .profiles import SpeciesProfile, load_profiles, species_buttons
from .static_scorer import static_suitability

__all__ = [
    "load_profiles",
    "species_buttons",
    "SpeciesProfile",
    "static_suitability",
    "readiness",
    "predict",
]
