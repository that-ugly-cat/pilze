"""Scorer DINAMICO (spec §7.2, §7.5): readiness meteo ∈ [0,1].

f(feature_meteo, profilo.dynamic_triggers) → readiness. Due gate duri (fenologia,
moisture_floor) e alcuni fattori graduati combinati in media geometrica.

NB: le feature meteo (spec §4) arrivano dalla pipeline v2 (poller ICON-D2, non
ancora scritta). Questo scorer lavora già su un dict di feature qualsiasi, così è
testabile ora con valori sintetici.

Feature attese (None => trattate come da docstring dei singoli fattori):
    month              : int 1..12                      (gate fenologia)
    soil_moisture      : float m3/m3                    (gate moisture_floor)
    cumulative_rain_mm : float, sulla finestra del profilo
    soil_temp_c        : float
    thermal_shock_c    : float — entità del calo recente di temp. del SUOLO (§5)
    days_since_trigger : int
"""

from __future__ import annotations

import math

from . import membership as m
from .profiles import SpeciesProfile


def _phenology_gate(month: int | None, months: list[int]) -> float:
    """Fuori da phenology_months la readiness è ~0 anche con habitat perfetto (§7.5)."""
    if month is None or not months:
        return 1.0
    if month in months:
        return 1.0
    # ramp morbido ai mesi adiacenti, per non spaccare al confine di mese
    if ((month % 12) + 1) in months or ((month - 2) % 12 + 1) in months:
        return 0.15
    return 0.0


def _moisture_gate(soil_moisture: float | None, floor: float | None) -> float:
    """Sotto moisture_floor la buttata aborta → readiness → 0 (§7.1)."""
    if soil_moisture is None or floor is None:
        return 1.0
    return m.trapezoid(float(soil_moisture), floor - 0.05, floor, 1.0, 1.0)


def readiness(profile: SpeciesProfile, feat: dict, breakdown: bool = False):
    """Readiness dinamica ∈ [0,1]. Se breakdown=True ritorna (score, dettaglio)."""
    t = profile.dynamic_triggers

    pheno = _phenology_gate(feat.get("month"), profile.phenology_months)
    moist = _moisture_gate(feat.get("soil_moisture"), t.get("moisture_floor"))

    rain = m.ramp_up(feat.get("cumulative_rain_mm"),
                     float(t.get("cumulative_rain_mm", 0)), softness=15.0)

    st = t.get("soil_temp_c", {})
    temp = m.band(feat.get("soil_temp_c"),
                  float(st.get("min", -50)), float(st.get("max", 60)), softness=3.0)

    # shock è l'innesco: assente => neutro-basso (0.4), non azzera
    shock_obs = feat.get("thermal_shock_c")
    shock = m.ramp_up(shock_obs, float(t.get("thermal_shock_c", 0)), softness=3.0) \
        if shock_obs is not None else 0.4

    lag = m.envelope_membership(feat.get("days_since_trigger"),
                                t.get("lag_days", {}), default_margin=6.0)

    graded = {"rain": rain, "soil_temp": temp, "shock": shock, "lag": lag}
    num = sum(math.log(max(v, 1e-6)) for v in graded.values())
    geo = math.exp(num / len(graded))
    score = pheno * moist * geo

    if breakdown:
        return score, {"phenology": pheno, "moisture_gate": moist, **graded}
    return score


# Soglia di "carica" per mostrare una cella (readiness senza il fattore lag) e fattore
# della finestra "tardi" (Lmax < dst ≤ TARDI_FACTOR·Lmax). Entrambi tarabili.
CHARGE_THR = 0.5
TARDI_FACTOR = 2.0


def readiness_state(profile: SpeciesProfile, feat: dict, charge_thr: float = CHARGE_THR) -> dict:
    """Fase della buttata per la viz. Ritorna {state, readiness, charge, dst, eta?, days_past?}.

    charge = readiness SENZA il fattore lag (pioggia×temp×shock, coi gate) → dice se le
    condizioni ci sono, a prescindere dal timing. La FASE viene da days_since_trigger (dst)
    rispetto a lag_days.opt=[Lmin,Lmax]: in_fieri (dst<Lmin) · pronto (in finestra) ·
    tardi (dst≤2·Lmax). state=None se gate chiusi, carica sotto soglia, o niente trigger.
    """
    score, bd = readiness(profile, feat, breakdown=True)
    non_lag = [bd["rain"], bd["soil_temp"], bd["shock"]]
    charge = bd["phenology"] * bd["moisture_gate"] * \
        math.exp(sum(math.log(max(v, 1e-6)) for v in non_lag) / len(non_lag))
    out = {"state": None, "readiness": round(score, 3), "charge": round(charge, 3)}
    if bd["phenology"] == 0 or bd["moisture_gate"] == 0 or charge < charge_thr:
        return out
    dst = feat.get("days_since_trigger")
    opt = (profile.dynamic_triggers.get("lag_days") or {}).get("opt")
    if dst is None or not opt:
        return out
    lmin, lmax = float(opt[0]), float(opt[1])
    out["dst"] = int(dst)
    if dst < lmin:
        out["state"], out["eta"] = "in_fieri", int(round(lmin - dst))
    elif dst <= lmax:
        out["state"] = "pronto"
    elif dst <= TARDI_FACTOR * lmax:
        out["state"], out["days_past"] = "tardi", int(round(dst - lmax))
    return out
