"""Scarica i confini amministrativi ISTAT e ne ricava l'AOI — l'area con dati tematici.

I layer tematici (forestale CFI2020, geologia) coprono ESATTAMENTE tre unità
amministrative: Bolzano, Trento, Veneto. I layer di base (DEM, WorldCover, canopy)
coprono invece tutto il bbox rettangolare, che è più largo — dentro ci cadono la
sponda lombarda, il Friuli occidentale, il Polesine e il Tirolo austriaco.

Il guaio è che nello scorer host sconosciuto vale 1.0 (neutro, non gate: §7.5
"unknown != absent"). Regola giusta DENTRO l'area rilevata, dove il buco è un piano
di gestione mancante; fuori si ribalta nel suo contrario e promuove il fuori-copertura
come se l'ospite fosse quello giusto. L'AOI è il confine di validità di quella regola.

Sorgente: ISTAT, "Limiti delle unità amministrative" (generalizzati), già in EPSG:32632.

    python -m gis.fetch_boundaries

Due prodotti, entrambi piccoli e versionati (il VPS li ha dal git clone, senza
riscaricare l'ISTAT):
    data/aoi/aoi.gpkg      maschera per i provider (UTM 32N, geometria piena)
    data/aoi/aoi.geojson   contorni per Leaflet (WGS84, semplificati)
"""

from __future__ import annotations

import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

AOI_DIR = Path(__file__).resolve().parent.parent / "data" / "aoi"
GPKG_PATH = AOI_DIR / "aoi.gpkg"
GEOJSON_PATH = AOI_DIR / "aoi.geojson"

YEAR = "2025"
ZIP_URL = ("https://www.istat.it/storage/cartografia/confini_amministrativi/"
           f"generalizzati/{YEAR}/Limiti01012025_g.zip")
PROV_SHP = "ProvCM01012025_g/ProvCM01012025_g_WGS84.shp"

# Le tre unità con dati tematici. Il Veneto è una regione (7 province da fondere),
# Trento e Bolzano sono province autonome — quindi si parte sempre dal layer province.
VENETO_COD_REG = 5
AUTONOMOUS_COD_PROV = {21: "Bolzano", 22: "Trento"}

# Tolleranza di semplificazione per il solo disegno (metri). La maschera usa la
# geometria piena: semplificare quella sposterebbe il confine di validità.
DISPLAY_TOLERANCE_M = 100.0


def download_zip(dest_dir: Path = AOI_DIR) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / ZIP_URL.rsplit("/", 1)[-1]
    if dest.exists():
        print(f"  già presente: {dest.name}")
        return dest
    print(f"  scarico {dest.name} …", flush=True)
    urllib.request.urlretrieve(ZIP_URL, dest)
    print(f"    ok ({dest.stat().st_size // (1024 * 1024)} MB)")
    return dest


def build(zip_path: Path, keep_zip: bool = False) -> None:
    import geopandas as gpd

    work = AOI_DIR / "_istat"
    with zipfile.ZipFile(zip_path) as z:
        stem = PROV_SHP.rsplit(".", 1)[0]
        z.extractall(work, members=[n for n in z.namelist() if n.startswith(stem)])

    prov = gpd.read_file(work / PROV_SHP)
    if prov.crs is None or prov.crs.to_epsg() != 32632:
        prov = prov.to_crs("EPSG:32632")

    rows = []
    for cod, name in AUTONOMOUS_COD_PROV.items():
        rows.append({"unit": name, "geometry": prov.loc[prov["COD_PROV"] == cod, "geometry"].union_all()})
    veneto = prov.loc[prov["COD_REG"] == VENETO_COD_REG]
    if veneto.empty:
        raise SystemExit("Veneto non trovato nel layer province ISTAT — schema cambiato?")
    rows.append({"unit": "Veneto", "geometry": veneto.geometry.union_all()})

    aoi = gpd.GeoDataFrame(rows, crs="EPSG:32632")
    aoi.to_file(GPKG_PATH, driver="GPKG", layer="aoi")

    display = aoi.copy()
    display["geometry"] = display.geometry.simplify(DISPLAY_TOLERANCE_M, preserve_topology=True)
    display.to_crs("EPSG:4326").to_file(GEOJSON_PATH, driver="GeoJSON")

    shutil.rmtree(work, ignore_errors=True)
    if not keep_zip:
        zip_path.unlink(missing_ok=True)

    total_km2 = aoi.geometry.area.sum() / 1e6
    for _, r in aoi.iterrows():
        print(f"  {r['unit']:10s} {r.geometry.area / 1e6:8,.0f} km²")
    print(f"  {'totale':10s} {total_km2:8,.0f} km²")
    print(f"  maschera  → {GPKG_PATH}  ({GPKG_PATH.stat().st_size // 1024} KB)")
    print(f"  contorni  → {GEOJSON_PATH}  ({GEOJSON_PATH.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(f"Confini ISTAT {YEAR} → {AOI_DIR}:")
    build(download_zip(), keep_zip="--keep-zip" in sys.argv)
