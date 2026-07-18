"""Test traccia A: griglia + Boyce. (occurrences: rete, non nei test.)"""

import numpy as np

from gis import boyce, grid


def test_assign_deterministico_e_doppia_cella():
    a = grid.assign(45.86, 11.77)
    b = grid.assign(45.86, 11.77)
    assert a == b                          # deterministico
    s, m = a
    assert s.startswith("s100_") and m.startswith("m2200_")   # passi da config


def test_punti_vicini_stessa_cella_lontani_diversa():
    base = grid.assign(45.8600, 11.7700)[0]
    vicino = grid.assign(45.8603, 11.7700)[0]      # ~33 m a nord → stessa cella 100 m
    lontano = grid.assign(46.30, 11.60)[0]
    assert base == vicino
    assert base != lontano


def test_cell_center_dentro_la_cella():
    lat, lon = 45.86, 11.77
    s, _ = grid.assign(lat, lon)
    clat, clon = grid.cell_center(s)
    # il centro cade entro ~1 passo (100 m ≈ 0.001° lat) dal punto
    assert abs(clat - lat) < 0.01 and abs(clon - lon) < 0.01


def test_grid_dimensions_coerenti():
    ncol, nrow, tot = grid.grid_dimensions()
    assert ncol > 0 and nrow > 0 and tot == ncol * nrow


def test_boyce_alto_per_modello_buono():
    # presenze concentrate su idoneità alta, background uniforme → Boyce > 0
    rng = np.random.default_rng(0)
    background = rng.uniform(0, 1, 5000)
    pres = rng.beta(5, 1.5, 500)           # sbilanciate verso l'alto
    res = boyce.continuous_boyce(pres, background)
    assert res["boyce"] > 0.5


def test_boyce_nullo_per_modello_casuale():
    rng = np.random.default_rng(1)
    background = rng.uniform(0, 1, 5000)
    pres = rng.uniform(0, 1, 500)          # nessuna preferenza
    res = boyce.continuous_boyce(pres, background)
    assert abs(res["boyce"]) < 0.5
