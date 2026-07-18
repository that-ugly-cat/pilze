"""Chioma viva da Sentinel-2 → canopy_alive (spec §3.1) — approccio "latest, agnostico".

Invece di un change-detection Vaia-specifico, un reality-check sul layer forestale:
prende l'ULTIMA estate Sentinel-2 e misura se una cella segnata a bosco ha ancora
chioma forestale VIVA. Agnostico alla causa (Vaia, bostrico, taglio, frana), auto-
aggiornante. Punto cieco noto: abete morto IN PIEDI (bostrico non ancora schiantato)
— struttura ancora "ad albero"; se peserà si aggiunge un indice di stress.

canopy_alive ∈ [0,1]: chioma densa viva → 1; nudo/prato/rasato → ~0. In host_membership
declassa gli host conifera. Solo stdlib RS: STAC (Earth Search) + COG (rioxarray).

Composite: per ogni scena estiva maschero nuvole/neve (SCL), calcolo NDVI e NBR,
poi mediana per pixel fra le scene (robusta ai residui).
"""

from __future__ import annotations

import warnings

import numpy as np

STAC = "https://earth-search.aws.element84.com/v1"
COLLECTION = "sentinel-2-l2a"
SCL_KEEP = (4, 5, 6, 7)          # veg, non-veg, water, unclassified (scarto nuvole/ombre/neve)

# mapping (NDVI, NBR) → canopy_alive. Soglie TARATE su Paneveggio (calibrazione:
# cleared NDVI~0.47/NBR~0.18, intact NDVI~0.82/NBR~0.60 su 23k px conifera mappati).
NDVI_LO, NDVI_HI = 0.45, 0.78    # <lo nudo/sparso, >hi chioma densa
NBR_LO, NBR_HI = 0.20, 0.55      # struttura/umidità: cleared~0.18→0, intatto~0.60→1


def search(bbox, datetime, max_cloud=25, limit=12):
    from pystac_client import Client
    s = Client.open(STAC).search(
        collections=[COLLECTION], bbox=bbox, datetime=datetime,
        query={"eo:cloud_cover": {"lt": max_cloud}}, max_items=limit)
    return list(s.items())


def _read(href, bbox, match=None):
    import rioxarray
    da = rioxarray.open_rasterio(href, masked=True).rio.clip_box(*bbox, crs="EPSG:4326").squeeze()
    return da.rio.reproject_match(match) if match is not None else da


def scene_ndvi_nbr(item, bbox):
    """(ndvi, nbr) di una scena, mascherati via SCL, sulla griglia 20 m (B8A). None se illeggibile."""
    try:
        b8a = _read(item.assets["nir08"].href, bbox)               # 20 m
        red = _read(item.assets["red"].href, bbox, match=b8a)      # 10 m → 20 m
        b12 = _read(item.assets["swir22"].href, bbox, match=b8a)   # 20 m
        scl = _read(item.assets["scl"].href, bbox, match=b8a)
    except Exception:
        return None
    keep = scl.isin(SCL_KEEP)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ndvi = ((b8a - red) / (b8a + red)).where(keep)
        nbr = ((b8a - b12) / (b8a + b12)).where(keep)
    return ndvi, nbr


def composite(bbox, datetime, max_cloud=25):
    """Mediana per pixel di NDVI e NBR sulle scene estive → (ndvi, nbr, n_scene)."""
    items = search(bbox, datetime, max_cloud)
    ndvis, nbrs, ref = [], [], None
    for it in items:
        r = scene_ndvi_nbr(it, bbox)
        if r is None:
            continue
        ndvi, nbr = r
        if ref is None:
            ref = ndvi
        else:
            ndvi = ndvi.rio.reproject_match(ref)
            nbr = nbr.rio.reproject_match(ref)
        ndvis.append(ndvi)
        nbrs.append(nbr)
    if not ndvis:
        return None, None, 0
    import xarray as xr
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ndvi_med = xr.concat(ndvis, dim="t").median("t", skipna=True)
        nbr_med = xr.concat(nbrs, dim="t").median("t", skipna=True)
    return ndvi_med, nbr_med, len(ndvis)


def _ramp(x, lo, hi):
    return np.clip((x - lo) / (hi - lo), 0.0, 1.0)


def canopy_alive(ndvi, nbr):
    """canopy_alive ∈ [0,1] = chioma viva richiede verde E struttura (prodotto di due rampe)."""
    return _ramp(ndvi, NDVI_LO, NDVI_HI) * _ramp(nbr, NBR_LO, NBR_HI)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # Finestra Paneveggio / Val Travignolo — sito-simbolo devastazione Vaia (2018)
    bbox = [11.70, 46.27, 11.85, 46.36]
    print(f"Composite Sentinel-2 (ultima estate) su Paneveggio {bbox} …")
    ndvi, nbr, n = composite(bbox, "2025-06-15/2025-09-20", max_cloud=25)
    if n == 0:
        print("nessuna scena utile"); sys.exit(1)
    ca = canopy_alive(ndvi.values, nbr.values)
    ca = ca[np.isfinite(ca)]
    print(f"{n} scene composited | {ca.size} pixel validi @20 m")
    print(f"  NDVI mediano {float(ndvi.median()):.2f} | NBR mediano {float(nbr.median()):.2f}")
    print(f"  canopy_alive: media {ca.mean():.2f} | "
          f"cleared(<0.2) {100*(ca<0.2).mean():.0f}% | intact(>0.8) {100*(ca>0.8).mean():.0f}%")
    # salva il raster canopy_alive
    from pathlib import Path
    out = Path(__file__).resolve().parent.parent / "data" / "canopy" / "proto_paneveggio.tif"
    out.parent.mkdir(parents=True, exist_ok=True)
    ca_da = (_ramp(ndvi, NDVI_LO, NDVI_HI) * _ramp(nbr, NBR_LO, NBR_HI)).rio.write_crs(ndvi.rio.crs)
    ca_da.rio.to_raster(out)
    print(f"  raster canopy_alive → {out}")
