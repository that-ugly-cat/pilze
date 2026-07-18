"""Continuous Boyce Index (spec §6.3) — metrica per SDM presence-only.

Verifica che le classi a idoneità più alta ricevano proporzionalmente più presenze.
Funziona con pochi punti e SENZA vere assenze (Hirzel et al. 2006): confronta la
frequenza delle presenze (F) con quella attesa dal background disponibile (E) lungo
la scala di idoneità, e correla (Spearman) il rapporto P/E con l'idoneità.

Boyce ∈ [-1, 1]: ~+1 modello buono (P/E cresce con l'idoneità), ~0 non meglio del
caso, <0 controverso. Solo numpy.
"""

from __future__ import annotations

import numpy as np


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 2:
        return float("nan")
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    ra -= ra.mean(); rb -= rb.mean()
    denom = np.sqrt((ra**2).sum() * (rb**2).sum())
    return float((ra * rb).sum() / denom) if denom else float("nan")


def continuous_boyce(pres: np.ndarray, background: np.ndarray,
                     n_windows: int = 100, window_frac: float = 0.1) -> dict:
    """
    pres       : idoneità predetta ai punti di PRESENZA
    background : idoneità predetta alle celle DISPONIBILI (tutta l'area o un campione)
    Ritorna {'boyce': float, 'centers': [...], 'pe': [...]} (pe = rapporto P/E per finestra).
    """
    pres = np.asarray(pres, dtype=float)
    background = np.asarray(background, dtype=float)
    lo = float(min(pres.min(), background.min()))
    hi = float(max(pres.max(), background.max()))
    if hi <= lo:
        return {"boyce": float("nan"), "centers": [], "pe": []}

    width = (hi - lo) * window_frac
    centers = np.linspace(lo + width / 2, hi - width / 2, n_windows)
    npres, nbg = len(pres), len(background)

    used_centers, pe = [], []
    for c in centers:
        a, b = c - width / 2, c + width / 2
        f = np.count_nonzero((pres >= a) & (pres <= b)) / npres      # osservata
        e = np.count_nonzero((background >= a) & (background <= b)) / nbg  # attesa
        if e > 0:
            used_centers.append(c)
            pe.append(f / e)

    boyce = _spearman(np.array(used_centers), np.array(pe)) if len(pe) >= 2 else float("nan")
    return {"boyce": boyce, "centers": used_centers, "pe": pe}
