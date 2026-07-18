"""Funzioni di appartenenza SFUMATE (spec §7.5).

Tutte restituiscono [0,1]. Nessun salto artificiale ai bordi: una faggeta a 1001 m
con max:1000 non deve crollare a zero. Solo stdlib — matematica scalare.
"""

from __future__ import annotations


def clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def trapezoid(x: float, lo0: float, lo1: float, hi1: float, hi0: float) -> float:
    """Appartenenza trapezoidale.

    0 fino a lo0, sale lineare a 1 su [lo0, lo1], resta 1 su [lo1, hi1],
    scende a 0 su [hi1, hi0], 0 oltre hi0. Spalle aperte se lo0==lo1 / hi1==hi0.
    """
    if x <= lo0:
        return 0.0 if lo0 != lo1 else 1.0  # spalla sinistra assente => 1 a sinistra
    if x < lo1:
        return clamp01((x - lo0) / (lo1 - lo0))
    if x <= hi1:
        return 1.0
    if x < hi0:
        return clamp01((hi0 - x) / (hi0 - hi1))
    return 0.0 if hi1 != hi0 else 1.0  # spalla destra assente => 1 a destra


def envelope_membership(x: float | None, env: dict, default_margin: float) -> float:
    """Trapezoide da una voce di static_envelope: {min, opt:[a,b], max} oppure {opt:[a,b]}.

    Se mancano min/max (es. slope), si sintetizza un margine `default_margin` oltre opt.
    x None => 1.0 (fattore non misurato, non penalizza).
    """
    if x is None:
        return 1.0
    opt = env.get("opt")
    if not opt or len(opt) != 2:
        return 1.0
    lo1, hi1 = float(opt[0]), float(opt[1])
    lo0 = float(env["min"]) if "min" in env else max(0.0, lo1 - default_margin)
    hi0 = float(env["max"]) if "max" in env else hi1 + default_margin
    return trapezoid(float(x), lo0, lo1, hi1, hi0)


# Match categoriale morbido: 1 preferito, intermedio se tollerato, ~0 avverso.
# tabelle {preferenza_profilo: {valore_cella: membership}}
_ASPECT = {
    "warm":    {"warm": 1.0, "neutral": 0.5, "cool": 0.1},
    "cool":    {"cool": 1.0, "neutral": 0.5, "warm": 0.1},
    "neutral": {"neutral": 1.0, "warm": 0.6, "cool": 0.6},
}
_SOIL_PH = {
    "acidic":     {"acidic": 1.0, "neutral": 0.6, "calcareous": 0.15},
    "calcareous": {"calcareous": 1.0, "neutral": 0.6, "acidic": 0.15},
    "neutral":    {"neutral": 1.0, "acidic": 0.6, "calcareous": 0.6},
    "tolerant":   {"acidic": 0.9, "neutral": 0.9, "calcareous": 0.8},
}
_DRAINAGE = {
    "well_drained": {"well_drained": 1.0, "moist": 0.6, "dry": 0.5, "waterlogged": 0.1},
    "moist":        {"moist": 1.0, "well_drained": 0.6, "waterlogged": 0.3, "dry": 0.2},
}


def categorical_membership(kind: str, preferred: str | None, measured: str | None,
                           neutral_default: float = 0.5) -> float:
    """Match morbido per aspect / soil_ph / drainage.

    preferred None => fattore non richiesto dal profilo => 1.0 (neutro).
    measured None  => cella non misurata => neutral_default.
    """
    if preferred is None:
        return 1.0
    if measured is None:
        return neutral_default
    table = {"aspect": _ASPECT, "soil_ph": _SOIL_PH, "drainage": _DRAINAGE}.get(kind, {})
    return float(table.get(preferred, {}).get(measured, 0.0))


def band(x: float | None, lo: float, hi: float, softness: float) -> float:
    """Appartenenza a una banda [lo, hi] con bordi sfumati di ampiezza `softness`.

    Usata per soil_temp in range. x None => 0.5 (ignoto, neutro-basso).
    """
    if x is None:
        return 0.5
    return trapezoid(float(x), lo - softness, lo, hi, hi + softness)


def ramp_up(x: float | None, threshold: float, softness: float) -> float:
    """Sale verso 1 avvicinandosi/superando `threshold` (es. pioggia cumulata, shock).

    Pieno a threshold, metà a threshold-softness. x None => 0.0.
    """
    if x is None:
        return 0.0
    return clamp01((float(x) - (threshold - softness)) / softness) if softness > 0 else \
        (1.0 if float(x) >= threshold else 0.0)
