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
    *_fraction  : frazioni di copertura WorldCover (forest, grassland, cropland, …)
    edge_density: float [0,1] — quota di confine bosco/prato nell'intorno (specie di ecotono)
"""

from __future__ import annotations

import math

from . import membership as m
from .profiles import SpeciesProfile

# Classi conifera (fra le 20 CFI): la chioma morta (canopy) le declassa (Vaia/bostrico).
CONIFER_HOSTS = {"pecceta", "pecceta_secondaria", "abetina", "larici_cembreto",
                 "mugheta", "pineta_silvestre", "pineta_nera"}

# Il pavimento dell'ospite sta nel PROFILO (`host_floor`, default 0 = veto secco), non qui.
# La domanda "quanto vale l'ospite sbagliato" sembrava una proprietà del motore — la CFI dà
# una categoria per poligono di gestione, quindi "faggeta" non vuol dire "non c'è un abete"
# — e invece è per specie, come tutto il resto. Misurato il 7 set 2026 col Boyce sull'intorno
# (background 4000, stesso operatore): all'ovolo un pavimento a 0.12 vale +0.433 → +0.688,
# al porcino costa +0.681 → +0.245. Un valore unico avrebbe pagato l'uno col doppio dell'altro.

# Classe di copertura del profilo → chiave di feature prodotta da WorldCoverProvider.
# `forest` resta il nome storico della classe 10 (tree cover) per non spezzare i profili.
HABITAT_KEYS = {
    "forest": "forest_fraction",
    "grassland": "grassland_fraction",
    "cropland": "cropland_fraction",
    "shrubland": "shrubland_fraction",
    "built_up": "built_up_fraction",
    "bare": "bare_fraction",
    "moss_lichen": "moss_lichen_fraction",
    "wetland": "wetland_fraction",
    "water": "water_fraction",
    "snow_ice": "snow_ice_fraction",
}

DEFAULT_WEIGHTS = {
    "elevation": 1.0,
    "slope": 0.5,
    "aspect": 0.8,
    "soil_ph": 0.8,
    "drainage": 0.5,
    "edge": 0.8,        # usato SOLO se il profilo dichiara static_envelope.edge_density
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
        return 0.0  # nessun bosco qui (non "il bosco sbagliato"): resta un veto secco
    floor = float(getattr(profile, "host_floor", 0.0) or 0.0)
    total = 0.0
    for cls, frac in comp.items():
        w = max(profile.host_genera.get(cls, 0.0), floor)   # la categoria è una generalizzazione
        if cls in CONIFER_HOSTS and canopy_alive is not None:
            w *= float(canopy_alive)          # declassa dove la chioma è morta, pavimento incluso
        total += float(frac) * w
    return m.clamp01(total)


def habitat_gate(profile: SpeciesProfile, cell: dict) -> float:
    """Gate «è l'habitat giusto?» dalle frazioni di copertura WorldCover (§3.1).

    `profile.habitat` è una stringa (una classe sola, peso 1) oppure un dizionario
    {classe: peso}: il gate è la somma pesata delle frazioni, in [0,1]. La forma a
    dizionario esiste per le specie di ECOTONO, che non stanno né nel bosco né nel prato
    ma sul confine: obbligarle a scegliere una classe sola azzera metà del loro habitat.

    Nessuna frazione nella cella (WorldCover assente) → 1.0, cioè nessun gate: è la
    stessa regola dell'host sconosciuto, «unknown ≠ absent».
    """
    weights = profile.habitat if isinstance(profile.habitat, dict) else {profile.habitat: 1.0}
    keys = [(HABITAT_KEYS.get(c, f"{c}_fraction"), w) for c, w in weights.items()]
    if not any(k in cell for k, _ in keys):
        return 1.0
    return m.clamp01(sum(float(w) * float(cell.get(k, 0.0)) for k, w in keys))


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
    # Fattore di BORDO, opt-in: entra nella media solo se il profilo lo dichiara, così i
    # profili che tacciono conservano il punteggio esatto di prima (un fattore neutro in
    # più sposterebbe comunque la media geometrica pesata). Serve alle specie di ecotono,
    # il cui habitat è il confine bosco/prato e non nessuna delle due coperture.
    if "edge_density" in env:
        factors["edge"] = m.envelope_membership(cell.get("edge_density"),
                                                env["edge_density"], default_margin=0.10)
    host = host_membership(profile, cell)
    gate = habitat_gate(profile, cell)
    score = host * gate * _weighted_geomean(factors, weights)   # gate moltiplicano, env smussa

    if breakdown:
        return score, {"host": host, "habitat_gate": gate, **factors}
    return score
