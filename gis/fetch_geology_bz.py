"""Scarica la geologia dell'Alto Adige/Südtirol via WFS (GeoServer PAB, no auth).

Layer GeologicalUnits-Detailed (66k unità, CRS EPSG:25832, CC BY-SA). Campo litologico
TEG_DESC_I (descrizione italiana: dolomia/calcare→calcareo, porfido/granito→acido).
Paginato → GeoPackage locale. Chiude il suolo mancante per Bolzano.

    python -m gis.fetch_geology_bz
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "geology" / "bolzano" / "geologia_bz.gpkg"
OWS = "https://geoservices1.civis.bz.it/geoserver/p_bz-GeologicalMap/ows"
TYPE = "p_bz-GeologicalMap:GeologicalUnits-Detailed"
PAGE = 5000


def _page(start: int) -> list:
    p = urllib.parse.urlencode({"service": "WFS", "version": "2.0.0", "request": "GetFeature",
                                "typeNames": TYPE, "outputFormat": "application/json",
                                "srsName": "EPSG:4326", "count": PAGE, "startIndex": start})
    d = json.loads(urllib.request.urlopen(f"{OWS}?{p}", timeout=120).read())
    return d.get("features", [])


def fetch(dest: Path = OUT) -> Path:
    import geopandas as gpd
    from shapely.geometry import shape
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        print(f"  già presente: {dest.name}"); return dest
    rows, start, skip = [], 0, 0
    while True:
        page = _page(start)
        if not page:
            break
        for f in page:
            g, t = f.get("geometry"), (f.get("properties") or {}).get("NOME_IT_1")
            if not g:
                skip += 1; continue
            try:
                geom = shape(g)
                if not geom.is_valid:
                    geom = geom.buffer(0)
            except Exception:
                skip += 1; continue
            rows.append({"litho": (t or "").strip(), "geometry": geom})
        start += PAGE
        print(f"    {start} …", flush=True)
    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    gdf.to_file(dest, driver="GPKG")
    print(f"  salvato {dest.name}: {len(gdf)} unità ({skip} scartate)")
    return dest


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(f"Geologia Alto Adige/Südtirol → {OUT}")
    fetch()
