"""Posizione topografica relativa dal DEM → il layer di drenaggio (spec §3.1).

Il `drainage` dei profili è sempre stato un fattore COSTANTE: nessun provider produceva
la chiave, quindi ogni cella prendeva il 0.5 del "non misurato" e si portava via il ~9%
del punteggio senza distinguere niente. Le due uscite scritte in COME-FUNZIONA erano
«o si trova una fonte di drenaggio, o il fattore va tolto dai pesi»: questa è la prima.

La fonte è il DEM che è già in casa. Per ogni pixel si guarda l'intorno di `RADIUS_M` e
si calcola **dove sta fra il fondo e la cresta di quell'intorno**:

    r = (z - z_min) / (z_max - z_min)     ∈ [0, 1]

r ≈ 1 è un crinale (l'acqua se ne va), r ≈ 0 un fondo di conca (l'acqua ci arriva e ci
resta). È il TPI di Weiss normalizzato sul rilievo locale invece che su una soglia in
metri: così la stessa regola vale in Lessinia e in Val di Fiemme, dove venti metri di
dislivello significano cose opposte.

**Dove il terreno è piatto la domanda non ha risposta** e la si dichiara: sotto
`MIN_RELIEF_M` di dislivello nell'intorno il pixel esce come "non misurato" (sentinella),
il provider non emette la chiave e il fattore torna al 0.5 neutro di oggi. In pianura un
DEM a 30 m non sa se un campo è drenato, e fingere il contrario sposterebbe in silenzio
mezza Veneto.

    python -m gis.make_tpi          # 12 tile, qualche minuto, una volta sola

Va rilanciato solo se cambia il DEM. L'output sta in data/dem_tpi/, con gli stessi nomi
dei tile del DEM: int16 = r × 1000, sentinella -32768.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import rasterio
from numpy.lib.stride_tricks import sliding_window_view

DEM_DIR = Path(__file__).resolve().parent.parent / "data" / "dem"
TPI_DIR = Path(__file__).resolve().parent.parent / "data" / "dem_tpi"

RADIUS_M = 500.0          # raggio dell'intorno: il versante, non la microtopografia
MIN_RELIEF_M = 20.0       # sotto questo dislivello nell'intorno: "non misurato"
NODATA = -32768           # sentinella int16 (anche il nodata del DEM finisce qui)


def _slide(a: np.ndarray, w: int, axis: int, want_max: bool) -> np.ndarray:
    """min/max scorrevole di ampiezza `w` lungo `axis`, finestra troncata ai bordi.

    Il bordo si gestisce riempiendo con ±inf, che nel min/max non vince mai: il risultato
    è l'estremo della sola parte valida della finestra.
    """
    pad = w // 2
    widths = [(pad, pad) if i == axis else (0, 0) for i in range(a.ndim)]
    padded = np.pad(a, widths, constant_values=(-np.inf if want_max else np.inf))
    view = sliding_window_view(padded, w, axis=axis)
    return view.max(axis=-1) if want_max else view.min(axis=-1)


def relative_position(z: np.ndarray, half_rows: int, half_cols: int,
                      min_relief: float = MIN_RELIEF_M):
    """(r, valid): posizione relativa nell'intorno e maschera dei pixel con rilievo.

    Il nodata (NaN) diventa ±inf prima degli scorrimenti: nel min/max non vince mai, così
    un buco di mare non azzera l'intorno di mezzo chilometro attorno a sé.
    """
    hole = np.isnan(z)
    wr, wc = 2 * half_rows + 1, 2 * half_cols + 1
    lo = _slide(_slide(np.where(hole, np.inf, z), wr, 0, False), wc, 1, False)
    hi = _slide(_slide(np.where(hole, -np.inf, z), wr, 0, True), wc, 1, True)
    relief = hi - lo
    valid = np.isfinite(z) & np.isfinite(relief) & (relief >= min_relief)
    r = np.zeros(z.shape, dtype="float32")
    np.divide(z - lo, relief, out=r, where=valid)
    return r, valid


def process(path: Path, out_dir: Path = TPI_DIR) -> Path:
    with rasterio.open(path) as ds:
        z = ds.read(1).astype("float32")
        if ds.nodata is not None:
            z[z == ds.nodata] = np.nan
        # passi in metri al centro del tile (i tile sono in gradi)
        lat_c = (ds.bounds.bottom + ds.bounds.top) / 2.0
        cy = -ds.transform.e * 110_540.0
        cx = ds.transform.a * 111_320.0 * math.cos(math.radians(lat_c))
        half_rows = max(1, int(round(RADIUS_M / cy)))
        half_cols = max(1, int(round(RADIUS_M / cx)))
        r, valid = relative_position(z, half_rows, half_cols)
        out = np.full(z.shape, NODATA, dtype="int16")
        out[valid] = np.rint(r[valid] * 1000.0).astype("int16")
        profile = ds.profile | {"dtype": "int16", "count": 1, "nodata": NODATA,
                                "compress": "deflate"}
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / path.name
    with rasterio.open(dest, "w", **profile) as dst:
        dst.write(out, 1)
    pct = 100.0 * float(valid.sum()) / valid.size
    print(f"  {path.name}: intorno {2*half_rows+1}×{2*half_cols+1} px, "
          f"{pct:.1f}% con rilievo ≥ {MIN_RELIEF_M:.0f} m")
    return dest


def main(dem_dir: Path = DEM_DIR, out_dir: Path = TPI_DIR) -> int:
    tifs = sorted(Path(dem_dir).glob("*.tif"))
    if not tifs:
        raise FileNotFoundError(f"Nessun tile DEM in {dem_dir}. Esegui: python -m gis.fetch_dem")
    print(f"posizione topografica relativa (raggio {RADIUS_M:.0f} m) → {out_dir}")
    for p in tifs:
        process(p, out_dir)
    return len(tifs)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    n = main()
    print(f"{n} tile scritti.")
