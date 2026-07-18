"""Calibrazione canopy (scratch): cross-tab con le celle conifera del forestale TN +
distribuzioni NDVI/NBR intatto vs cleared per tarare la soglia. Finestra Paneveggio.

    python -m gis._calib_canopy
"""
from __future__ import annotations

import sys

import numpy as np
import rasterio.features

from gis import canopy
from gis.providers import ForestProvider

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BBOX = [11.70, 46.27, 11.85, 46.36]


def _hist(vals, lo=-0.2, hi=0.9, bins=11, width=40):
    edges = np.linspace(lo, hi, bins + 1)
    h, _ = np.histogram(vals, bins=edges)
    top = h.max() or 1
    for i in range(bins):
        bar = "█" * int(width * h[i] / top)
        print(f"    [{edges[i]:+.2f},{edges[i+1]:+.2f})  {h[i]:6d} {bar}")


def main():
    ndvi, nbr, n = canopy.composite(BBOX, "2025-06-15/2025-09-20", max_cloud=25)
    print(f"composite: {n} scene")
    ndvi_v, nbr_v = ndvi.values, nbr.values
    ca = canopy.canopy_alive(ndvi_v, nbr_v)

    # maschera conifera dal forestale TN (SIGFAT), rasterizzata sulla griglia del composite
    fp = ForestProvider.trentino()

    def is_conifer(t):
        c = fp.crosswalk.get(t, {})
        return (c.get("abete", 0) + c.get("pino", 0) + c.get("altro_conifere", 0)) > 0.5

    gc = fp.gdf[fp.gdf["tipo_fores"].map(is_conifer)].to_crs(ndvi.rio.crs)
    mask = rasterio.features.rasterize(
        ((g, 1) for g in gc.geometry), out_shape=ndvi.shape,
        transform=ndvi.rio.transform(), fill=0, dtype="uint8").astype(bool)

    valid = mask & np.isfinite(ca) & np.isfinite(ndvi_v) & np.isfinite(nbr_v)
    caC, ndC, nbC = ca[valid], ndvi_v[valid], nbr_v[valid]
    print(f"\ncelle CONIFERA (mappa forestale) sulla finestra: {caC.size} px @20 m")

    # (1) CROSS-TAB
    cl, inter, it = (caC < 0.2).mean(), ((caC >= 0.2) & (caC <= 0.8)).mean(), (caC > 0.8).mean()
    print(f"  canopy_alive:  cleared<0.2 {100*cl:.0f}%  |  intermedio {100*inter:.0f}%  |  intatto>0.8 {100*it:.0f}%")
    print("  → % di 'pecceta secondo la mappa' che oggi NON è chioma viva = staleness catturata")

    # (2) TARATURA soglia: sottopopolazioni cleared vs intatte (per canopy_alive provvisorio)
    lowca, highca = caC < 0.2, caC > 0.8
    print(f"\ntaratura — sottopopolazioni conifera:")
    print(f"  CLEARED (ca<0.2, n={lowca.sum()}):  NDVI {ndC[lowca].mean():.2f}±{ndC[lowca].std():.2f}  NBR {nbC[lowca].mean():.2f}±{nbC[lowca].std():.2f}")
    print(f"  INTACT  (ca>0.8, n={highca.sum()}): NDVI {ndC[highca].mean():.2f}±{ndC[highca].std():.2f}  NBR {nbC[highca].mean():.2f}±{nbC[highca].std():.2f}")
    print(f"\nistogramma NBR su tutte le celle conifera (cerca il minimo tra i due modi = soglia):")
    _hist(nbC)
    print(f"istogramma NDVI su tutte le celle conifera:")
    _hist(ndC)


if __name__ == "__main__":
    main()
