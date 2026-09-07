"""Validazione della mappa di idoneità coi punti GBIF (spec §6.3).

Continuous Boyce Index per specie: presenze GBIF vs background casuale, usando i
provider di feature disponibili. Oggi = solo DEM (quota/pendenza/esposizione): misura
quanta discriminazione porta il SOLO terreno, prima di forestale/suolo/disturbo.
Man mano che si aggiungono provider, lo stesso comando dà un Boyce più alto.

    python -m gis.validate            # pixel E intorno 250 m affiancati, piu' il divario
    python -m gis.validate --pixel    # solo il pixel esatto (piu' veloce, numeri storici)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from engine.profiles import load_profiles

from . import occurrences
from .providers import (AOIProvider, CanopyProvider, CompositeFeatureProvider, DEMProvider,
                        ForestProvider, GeologyProvider, SoilProvider,
                        WorldCoverProvider)
from .suitability import (NEIGHBOURHOOD_M, cells_at, random_background,
                          validate_species)


def build_provider(include_soil: bool = False,
                   include_geology: bool = False,
                   include_aoi: bool = True) -> tuple[CompositeFeatureProvider, list[str]]:
    """Compone i provider (AOI e DEM sempre; forestale se ci sono i dati; soil_ph opzionale).

    L'AOI (ON di default, `--no-aoi` per spegnerlo) taglia presenze E background fuori
    da BZ+TN+VE. Cambia il RIFERIMENTO del Boyce, non solo il numero di punti: il
    "disponibile" non è più il rettangolo del bbox ma l'area davvero rilevata, quindi i
    valori non sono confrontabili con quelli misurati prima del ritaglio.

    Due sorgenti soil_ph, entrambe OFF di default:
    - --geology (CARG, `GeologyProvider`): substrato litologico TN. MIGLIORA il segnale
      (edulis +0.74→+0.80) anche col 25% di copertura → è la fonte PREFERITA. Per-punto
      via REST (lenta, con cache) → per ora fuori dal default finché non è bulk+VE.
    - --soil (SoilGrids, `SoilProvider`): pH globale, troppo levigato, DEGRADA (edulis
      +0.71→+0.62). Tenuto solo per confronto.
    """
    providers, active = [], []
    if include_aoi:
        providers.append(AOIProvider())           # taglia il fuori BZ+TN+VE (presenze E background)
        active.append("AOI (BZ+TN+VE)")
    providers.append(DEMProvider())
    active.append("DEM (quota/pendenza/esposizione)")
    providers.append(ForestProvider.cfi())        # forestale: SOLO CFI2020 (VE+TN+BZ completo)
    active.append("forestale/host (CFI)")
    try:
        providers.append(WorldCoverProvider())    # gate "è bosco?" completo
        active.append("worldcover-gate")
    except FileNotFoundError:
        pass
    try:
        providers.append(CanopyProvider())       # declassa conifere stale (Vaia/bostrico)
        active.append("canopy_alive (Sentinel-2)")
    except FileNotFoundError:
        pass
    if include_geology:
        for name, ctor in [("TN", GeologyProvider.trentino), ("VE", GeologyProvider.veneto)]:
            try:
                providers.append(ctor())
                active.append(f"soil_ph CARG/substrato ({name})")
            except FileNotFoundError:
                pass
    if include_soil:
        try:
            providers.append(SoilProvider())
            active.append("soil_ph da SoilGrids (provvisorio)")
        except FileNotFoundError:
            pass
    return CompositeFeatureProvider(providers), active

GEOJSON = Path(__file__).resolve().parent.parent / "data" / "gbif_occurrences.geojson"


def load_presence(cfg_bbox: dict) -> dict[str, list[dict]]:
    """Presenze per specie: dal GeoJSON cache se c'è, altrimenti scarica da GBIF."""
    if GEOJSON.exists():
        gj = json.loads(GEOJSON.read_text(encoding="utf-8"))
        pts: dict[str, list[dict]] = {}
        for f in gj["features"]:
            lon, lat = f["geometry"]["coordinates"]
            pts.setdefault(f["properties"]["species"], []).append({"lat": lat, "lon": lon})
        return pts
    reg = load_profiles()
    return {sid: occurrences.fetch_occurrences(occurrences.scientific_name(sid), cfg_bbox)
            for sid in reg}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    import yaml
    cfg = yaml.safe_load(open(Path(__file__).resolve().parent.parent / "config" / "grid.yaml",
                              encoding="utf-8"))
    reg = load_profiles()
    provider, active = build_provider(include_soil="--soil" in sys.argv,
                                      include_geology="--geology" in sys.argv,
                                      include_aoi="--no-aoi" not in sys.argv)
    presence = load_presence(cfg["bbox_wgs84"])
    # Si misura con ENTRAMBI gli operatori, e si stampano affiancati. L'intorno di 250 m è
    # quello giusto per giudicare il modello (una segnalazione GBIF non è un pixel), ma
    # prende il MASSIMO di un 3×3: se un gate azzera la cella giusta e quella a 250 m è
    # buona, l'intorno non se ne accorge. Il DIVARIO fra i due numeri è quindi la cosa più
    # informativa delle due — è il budget di errore SPAZIALE dei gate, cioè quanto il
    # modello sbaglia di posto invece che di specie. `--pixel` calcola solo la colonna
    # sinistra, per chi vuole solo confrontarsi coi numeri storici.
    only_pixel = "--pixel" in sys.argv
    bg_points = random_background(5000, cfg)
    pres_points = {sid: [(p["lat"], p["lon"]) for p in pts] for sid, pts in presence.items()}

    def measure(radius):
        # le feature sono species-agnostic: si interrogano una volta e si scorano tutte
        bg = cells_at(provider, bg_points, radius)
        return {sid: validate_species(reg[sid], cells_at(provider, pts, radius), bg)
                for sid, pts in pres_points.items() if sid in reg}

    px = measure(None)
    nb = {} if only_pixel else measure(NEIGHBOURHOOD_M)

    def fmt(res, key="boyce"):
        if res is None:
            return "    n/d"
        b = res[key]
        return "    n/d" if b != b else f"{b:+.3f}"   # NaN-safe

    print("Continuous Boyce Index — " + " + ".join(active))
    print(f"{'specie':24s} {'n_pres':>7s} {'pixel':>7s} {'intorno':>8s} {'divario':>8s}")
    for sid in sorted(reg):
        p, n = px.get(sid), nb.get(sid)
        gap = ("" if p is None or n is None or p["boyce"] != p["boyce"] or n["boyce"] != n["boyce"]
               else f"{n['boyce'] - p['boyce']:+.3f}")
        npres = p["n_presence"] if p else 0
        print(f"{sid:24s} {npres:7d} {fmt(p):>7s} {fmt(n):>8s} {gap:>8s}")
    print("\nAtteso: positivo dove il modello ordina bene. Due valori sono confrontabili solo")
    print("a parità di background E di colonna. Il divario grande dice che i punti buoni")
    print("stanno ACCANTO alle celle premiate: imprecisione di GBIF, o gate troppo stretti.")


if __name__ == "__main__":
    main()
