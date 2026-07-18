"""Scarica in blocco il SUBSTRATO litologico del Trentino (CARG, spec §3.1).

Layer geologico PAT (ArcGIS REST, BDG12_Geologia/6 = SUBSTRATO, ~121k poligoni),
paginato a 1000, output GeoJSON in EPSG:4326 → salvato come GeoPackage locale.
Il GeologyProvider fa poi point-in-polygon LOCALE (veloce) invece del per-punto REST.

    python -m gis.fetch_geology
"""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

GEO_DIR = Path(__file__).resolve().parent.parent / "data" / "geology"
DEST = GEO_DIR / "substrato_tn.gpkg"
LAYER = ("https://geoservices.provincia.tn.it/agol/rest/services/geologico/"
         "BDG12_Geologia/MapServer/6/query")
PAGE = 1000


def _max_oid() -> int:
    # NB: il server rifiuta where=1=1 + geometria; si pagina per finestre di OBJECTID.
    stats = json.dumps([{"statisticType": "max", "onStatisticField": "OBJECTID",
                         "outStatisticFieldName": "mx"}])
    p = urllib.parse.urlencode({"where": "1=1", "outStatistics": stats, "f": "json"})
    return json.loads(urllib.request.urlopen(f"{LAYER}?{p}", timeout=40).read())[
        "features"][0]["attributes"]["mx"]


def _page(lo: int, hi: int, retries: int = 5) -> list[dict] | None:
    """Features della finestra, o None se il server la rifiuta dopo i retry (finestra saltata)."""
    import time
    p = urllib.parse.urlencode({
        "where": f"OBJECTID>={lo} AND OBJECTID<{hi}", "outFields": "NOME",
        "returnGeometry": "true", "f": "geojson",
    })
    for attempt in range(retries):
        try:
            d = json.loads(urllib.request.urlopen(f"{LAYER}?{p}", timeout=90).read())
            if "error" not in d:
                return d.get("features", [])
        except Exception:
            pass
        time.sleep(1.5 * (attempt + 1))            # backoff crescente sul throttling
    return None


def fetch(dest: Path = DEST) -> Path:
    import geopandas as gpd
    GEO_DIR.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        print(f"  già presente: {dest.name}")
        return dest
    import time

    from shapely.geometry import shape

    total = _max_oid()
    print(f"  OBJECTID max {total}, finestre da {PAGE} …", flush=True)
    rows, skipped, failed = [], 0, []
    lo = 1
    while lo <= total:
        page = _page(lo, lo + PAGE)
        if page is None:                           # finestra rifiutata dal server → salto
            failed.append(lo)
            lo += PAGE
            continue
        for f in page:
            geom_json, nome = f.get("geometry"), (f.get("properties") or {}).get("NOME")
            if not geom_json or not nome:
                skipped += 1
                continue
            try:                                   # difensivo: coord nulle/anelli invalidi
                geom = shape(geom_json)
                if not geom.is_valid:
                    geom = geom.buffer(0)
                if geom.is_empty:
                    skipped += 1
                    continue
            except Exception:
                skipped += 1
                continue
            rows.append({"NOME": str(nome).strip(), "geometry": geom})
        lo += PAGE
        time.sleep(0.4)                            # ritmo: non farsi throttlare dal server
        if (lo - 1) % 20000 == 0:
            print(f"    {min(lo - 1, total)}/{total}  (finestre saltate: {len(failed)})", flush=True)
    if not rows:
        raise RuntimeError("nessun poligono valido scaricato")
    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    gdf.to_file(dest, driver="GPKG")
    print(f"  salvato {dest.name}: {len(gdf)} poligoni "
          f"({skipped} geom. invalide, {len(failed)} finestre saltate ~{len(failed)*PAGE} poligoni persi)")
    return dest


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(f"CARG substrato Trentino → {DEST}")
    fetch()
