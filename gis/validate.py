"""Validazione della mappa di idoneità coi punti GBIF (spec §6.3).

Continuous Boyce Index per specie: presenze GBIF vs background casuale, usando i
provider di feature disponibili. Oggi = solo DEM (quota/pendenza/esposizione): misura
quanta discriminazione porta il SOLO terreno, prima di forestale/suolo/disturbo.
Man mano che si aggiungono provider, lo stesso comando dà un Boyce più alto.

    python -m gis.validate            # usa data/gbif_occurrences.geojson (o lo scarica)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from engine.profiles import load_profiles

from . import occurrences
from .providers import (CanopyProvider, CompositeFeatureProvider, DEMProvider,
                        ForestProvider, GeologyProvider, SoilProvider,
                        WorldCoverProvider)
from .suitability import validate_species


def build_provider(include_soil: bool = False,
                   include_geology: bool = False) -> tuple[CompositeFeatureProvider, list[str]]:
    """Compone i provider (DEM sempre; forestale se ci sono i dati; soil_ph opzionale).

    Due sorgenti soil_ph, entrambe OFF di default:
    - --geology (CARG, `GeologyProvider`): substrato litologico TN. MIGLIORA il segnale
      (edulis +0.74→+0.80) anche col 25% di copertura → è la fonte PREFERITA. Per-punto
      via REST (lenta, con cache) → per ora fuori dal default finché non è bulk+VE.
    - --soil (SoilGrids, `SoilProvider`): pH globale, troppo levigato, DEGRADA (edulis
      +0.71→+0.62). Tenuto solo per confronto.
    """
    providers, active = [DEMProvider()], ["DEM (quota/pendenza/esposizione)"]
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
                                      include_geology="--geology" in sys.argv)
    presence = load_presence(cfg["bbox_wgs84"])

    print("Continuous Boyce Index — provider attivi: " + " + ".join(active))
    print(f"{'specie':24s} {'n_pres':>7s} {'n_bg':>6s} {'boyce':>7s}")
    for sid, profile in sorted(reg.items()):
        res = validate_species(profile, provider, presence.get(sid, []), n_background=5000, cfg=cfg)
        b = res["boyce"]
        bs = "  n/d" if b != b else f"{b:+.3f}"        # NaN-safe
        print(f"{sid:24s} {res['n_presence']:7d} {res['n_background']:6d} {bs:>7s}")
    print("\nAtteso: positivo dove il terreno già discrimina (es. edulis, quota alta);")
    print("debole/n.d. dove i punti GBIF sono pochi (es. aereus). Salirà con forestale+suolo+disturbo.")


if __name__ == "__main__":
    main()
