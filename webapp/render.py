"""Rendering delle mappe idoneità → PNG overlay per Leaflet.

Il raster è in UTM 32N: si riproietta a EPSG:4326 così l'ImageOverlay si allinea alle
coordinate lat/lon. Colormap verde, trasparente dove idoneità 0. PNG cache-ati su disco.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import numpy as np

MAPS_DIR = Path(__file__).resolve().parent.parent / "data" / "maps"
CACHE_DIR = MAPS_DIR / "_png"

SCORE_MAX = 0.8      # normalizzazione comune (max osservato ~0.78); usata da PNG e griglia


def _reproject_4326(tif: Path, nearest: bool = False):
    """Riproietta il raster idoneità (UTM 32N) a una griglia regolare EPSG:4326.

    nearest=True preserva i valori esatti delle celle e bordi netti (per la griglia
    interattiva); bilinear (default) è per l'overlay PNG liscio.
    """
    import rasterio
    from rasterio.warp import Resampling, calculate_default_transform, reproject
    with rasterio.open(tif) as ds:
        dst_crs = "EPSG:4326"
        transform, w, h = calculate_default_transform(ds.crs, dst_crs, ds.width, ds.height, *ds.bounds)
        dst = np.zeros((h, w), dtype="float32")
        reproject(source=rasterio.band(ds, 1), destination=dst, src_transform=ds.transform,
                  src_crs=ds.crs, dst_transform=transform, dst_crs=dst_crs,
                  resampling=Resampling.nearest if nearest else Resampling.bilinear)
        left, top = transform.c, transform.f
        right, bottom = left + transform.a * w, top + transform.e * h
    return dst, (bottom, left, top, right)      # S, W, N, E


def suitability_grid(species: str):
    """Griglia compatta per l'overlay interattivo (canvas Leaflet).

    Ritorna un dict JSON-serializzabile: bbox 4326, dimensioni, e i punteggi
    quantizzati a uint8 (0 = fuori bosco/nodata, 1..255 = score su 0..SCORE_MAX)
    in base64 — ~150k byte prima della compressione. None se manca il raster.
    """
    tif = MAPS_DIR / f"idoneita_{species}.tif"
    if not tif.exists():
        return None
    arr, (s, w, n, e) = _reproject_4326(tif, nearest=True)
    ny, nx = arr.shape
    q = np.clip(np.rint(arr / SCORE_MAX * 255.0), 0, 255).astype("uint8")
    q[arr <= 0.0] = 0                            # nodata esplicito
    return {"bounds": [s, w, n, e], "nx": int(nx), "ny": int(ny),
            "score_max": SCORE_MAX,
            "cells": base64.b64encode(q.tobytes()).decode("ascii")}


def suitability_png(species: str):
    """Ritorna (png_bytes, bounds[[S,W],[N,E]]) per una specie. Cache su disco."""
    tif = MAPS_DIR / f"idoneita_{species}.tif"
    if not tif.exists():
        return None, None
    arr, (s, w, n, e) = _reproject_4326(tif)

    import matplotlib
    matplotlib.use("Agg")
    from PIL import Image

    cmap = matplotlib.colormaps["YlGn"]
    v = np.clip(arr / SCORE_MAX, 0, 1)           # normalizza (max osservato ~0.78)
    rgba = (cmap(v) * 255).astype("uint8")
    rgba[..., 3] = np.where(arr > 0.05, 200, 0)  # trasparente dove ~0
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, format="PNG")
    return buf.getvalue(), [[s, w], [n, e]]
