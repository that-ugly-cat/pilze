"""Replay dell'archivio meteo: cosa avrebbe detto l'asse dinamico, ogni giorno passato.

Falsificazione senza etichette. L'archivio (`data/meteo.db`) contiene la storia
giornaliera di migliaia di celle: rigirando `readiness_state` su ogni giorno passato si
vede quante volte, in tutta la stagione, una cella sarebbe mai stata "pronto" — e quale
fattore l'ha vetata. Se il numero resta vicino a zero mentre i funghi ci sono stati, il
modello è falsificato senza bisogno di un solo ritrovamento loggato.

Usa la stessa `features_from_daily` + `readiness_state` della produzione: cambia solo il
troncamento della serie al giorno simulato, quindi il verdetto vale per il codice vero.

    python -m gis.replay                       # tutte le specie, celle dentro l'AOI
    python -m gis.replay boletus_edulis        # una specie
    python -m gis.replay --all-cells           # anche fuori BZ+TN+VE
    python -m gis.replay --cell m2200_321_2323 # timeline di una cella sola
"""

from __future__ import annotations

import math
import sys
from collections import Counter, defaultdict
from datetime import date

from engine.dynamic_scorer import CHARGE_THR, TARDI_FACTOR, readiness, readiness_state
from engine.profiles import load_profiles

from . import grid, meteo

# Minimo di storia prima di simulare un giorno: senza finestra piena la pioggia cumulata
# è artificialmente bassa e il replay accuserebbe il modello di un difetto suo.
MIN_HISTORY_DAYS = 15


def load_archive(conn) -> dict[str, list]:
    """Tutto l'archivio in memoria, raggruppato per cella (una query, non 7000)."""
    cur = conn.execute("SELECT meteo_cell_id, date, precip_mm, soil_temp_c, soil_moist "
                       "FROM meteo ORDER BY meteo_cell_id, date")
    out: dict[str, list] = defaultdict(list)
    for cid, d, r, st, sm in cur:
        out[cid].append((date.fromisoformat(d), r, st, sm))
    return out


def binding_constraint(profile, feat: dict, bd: dict, charge: float) -> str:
    """Il PRIMO vincolo che impedisce lo stato, nell'ordine in cui il codice li applica."""
    if bd["phenology"] == 0:
        return "fenologia (mese fuori)"
    if bd["moisture_gate"] == 0:
        return "gate umidita' suolo"
    if charge < CHARGE_THR:
        worst = min(("pioggia", bd["rain"]), ("temp. suolo", bd["soil_temp"]),
                    ("shock termico", bd["shock"]), key=lambda kv: kv[1])
        return f"carica < {CHARGE_THR} (peggiore: {worst[0]})"
    if feat.get("days_since_trigger") is None:
        return "nessun trigger di pioggia"
    opt = (profile.dynamic_triggers.get("lag_days") or {}).get("opt")
    if not opt:
        return "profilo senza lag_days"
    if feat["days_since_trigger"] > TARDI_FACTOR * float(opt[1]):
        return "trigger troppo vecchio"
    return "?"


def replay_species(profile, archive: dict[str, list], cells: list[str]) -> dict:
    states = Counter()
    vetoes = Counter()
    per_day_pronto: Counter[date] = Counter()
    per_day_any: Counter[date] = Counter()
    factor_sum = defaultdict(float)
    n_eval = 0

    for cid in cells:
        daily = archive[cid]
        for i in range(MIN_HISTORY_DAYS, len(daily)):
            window = daily[:i + 1]
            feat = meteo.features_from_daily(profile, window)
            st = readiness_state(profile, feat)
            _, bd = readiness(profile, feat, breakdown=True)
            n_eval += 1
            day = window[-1][0]
            per_day_any[day] += 1
            for k, v in bd.items():
                factor_sum[k] += v
            if st["state"]:
                states[st["state"]] += 1
                if st["state"] == "pronto":
                    per_day_pronto[day] += 1
            else:
                states["(niente)"] += 1
                vetoes[binding_constraint(profile, feat, bd, st["charge"])] += 1

    return {"n_eval": n_eval, "states": states, "vetoes": vetoes,
            "per_day_pronto": per_day_pronto, "per_day_any": per_day_any,
            "factor_mean": {k: v / n_eval for k, v in factor_sum.items()} if n_eval else {}}


def print_report(sid: str, r: dict) -> None:
    n = r["n_eval"]
    if not n:
        print(f"\n### {sid}: nessuna cella-giorno valutabile")
        return
    print(f"\n### {sid} — {n:,} celle-giorno simulate")
    for k in ("pronto", "in_fieri", "tardi", "(niente)"):
        c = r["states"][k]
        print(f"    {k:10s} {c:8,}  ({c / n:6.2%})")
    days = sorted(r["per_day_any"])
    lit = [d for d in days if r["per_day_pronto"][d]]
    print(f"    giorni simulati: {len(days)}  |  con almeno una cella PRONTO: {len(lit)}"
          f"  |  picco in un giorno: {max(r['per_day_pronto'].values(), default=0):,}"
          f" celle su {max(r['per_day_any'].values(), default=0):,}")
    print("    medie dei fattori: " + "  ".join(f"{k}={v:.3f}" for k, v in r["factor_mean"].items()))
    print("    vincolo che blocca (primo che scatta):")
    for name, c in r["vetoes"].most_common(6):
        print(f"      {name:38s} {c:8,}  ({c / n:6.2%})")


def timeline(profile, daily: list) -> None:
    """Storia giorno per giorno di UNA cella: cosa avrebbe detto la mappa, e perche'."""
    print(f"{'data':12s} {'pioggia15':>9s} {'umid':>6s} {'Tsuolo':>7s} {'shock':>6s} "
          f"{'dst':>4s} {'ready':>6s} {'carica':>7s}  stato / veto")
    for i in range(MIN_HISTORY_DAYS, len(daily)):
        window = daily[:i + 1]
        feat = meteo.features_from_daily(profile, window)
        st = readiness_state(profile, feat)
        _, bd = readiness(profile, feat, breakdown=True)
        why = st["state"] or binding_constraint(profile, feat, bd, st["charge"])
        print(f"{window[-1][0]!s:12s} {feat['cumulative_rain_mm']:9.1f} "
              f"{feat['soil_moisture'] if feat['soil_moisture'] is not None else float('nan'):6.3f} "
              f"{feat['soil_temp_c'] if feat['soil_temp_c'] is not None else float('nan'):7.1f} "
              f"{feat['thermal_shock_c']:6.1f} "
              f"{feat['days_since_trigger'] if feat['days_since_trigger'] is not None else -1:4d} "
              f"{st['readiness']:6.3f} {st['charge']:7.3f}  {why}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    all_cells = "--all-cells" in sys.argv
    one_cell = next((sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--cell"), None)

    reg = load_profiles()
    conn = meteo.connect()
    archive = load_archive(conn)
    conn.close()
    print(f"archivio: {len(archive):,} celle, "
          f"{sum(len(v) for v in archive.values()):,} righe giornaliere")

    if one_cell:
        sid = args[0] if args else "boletus_edulis"
        print(f"\n=== timeline {one_cell} — {sid} ===")
        timeline(reg[sid], archive[one_cell])
        return

    cells = sorted(archive)
    if not all_cells:
        from .providers import AOIProvider
        aoi = AOIProvider()
        cells = [c for c in cells if _cell_in_aoi(c, aoi)]
        print(f"celle dentro l'AOI (BZ+TN+VE): {len(cells):,} su {len(archive):,}")

    for sid in (args or sorted(reg)):
        print_report(sid, replay_species(reg[sid], archive, cells))


def _cell_in_aoi(cell_id: str, aoi) -> bool:
    lat, lon = grid.cell_center(cell_id)
    return aoi.features(lat, lon) is not None


if __name__ == "__main__":
    main()
