"""Rendering delle mappe idoneità → PNG overlay per Leaflet.

Il raster è in UTM 32N: si riproietta a EPSG:4326 così l'ImageOverlay si allinea alle
coordinate lat/lon. Colormap verde, trasparente dove idoneità 0. PNG cache-ati su disco.
"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np

MAPS_DIR = Path(__file__).resolve().parent.parent / "data" / "maps"
CACHE_DIR = MAPS_DIR / "_png"


def _reproject_4326(tif: Path):
    import rasterio
    from rasterio.warp import Resampling, calculate_default_transform, reproject
    with rasterio.open(tif) as ds:
        dst_crs = "EPSG:4326"
        transform, w, h = calculate_default_transform(ds.crs, dst_crs, ds.width, ds.height, *ds.bounds)
        dst = np.zeros((h, w), dtype="float32")
        reproject(source=rasterio.band(ds, 1), destination=dst, src_transform=ds.transform,
                  src_crs=ds.crs, dst_transform=transform, dst_crs=dst_crs,
                  resampling=Resampling.bilinear)
        left, top = transform.c, transform.f
        right, bottom = left + transform.a * w, top + transform.e * h
    return dst, (bottom, left, top, right)      # S, W, N, E


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
    v = np.clip(arr / 0.8, 0, 1)                 # normalizza (max osservato ~0.78)
    rgba = (cmap(v) * 255).astype("uint8")
    rgba[..., 3] = np.where(arr > 0.05, 200, 0)  # trasparente dove ~0
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, format="PNG")
    return buf.getvalue(), [[s, w], [n, e]]
