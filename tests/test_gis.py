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


def test_intorno_produce_nove_punti_e_include_il_centro():
    """La validazione scora il massimo di un intorno: il centro deve starci dentro."""
    from gis.suitability import neighbourhood
    pts = neighbourhood(46.3, 11.5, 250.0)
    assert len(pts) == 9
    assert (46.3, 11.5) in [(round(a, 6), round(b, 6)) for a, b in pts]
    lats = [a for a, _ in pts]
    # 250 m in latitudine ≈ 0.00226°: l'intorno copre ±250 m, non ±25 né ±2500
    assert 0.002 < max(lats) - 46.3 < 0.003


def test_score_cells_prende_il_massimo():
    """Il punteggio di un punto è il migliore del suo intorno, non quello del centro."""
    from engine.profiles import SpeciesProfile
    from gis.suitability import score_cells
    prof = SpeciesProfile(id="quercino", common_name="quercino", trophic_mode="mycorrhizal",
                          host_genera={"querceto": 1.0},
                          static_envelope={"elevation_m": {"min": 0, "opt": [400, 500], "max": 900}})
    dentro = {"host_class": "querceto", "elevation_m": 450}
    fuori = {"host_class": "querceto", "elevation_m": 880}
    assert score_cells(prof, [[fuori, dentro, fuori]])[0] == score_cells(prof, [[dentro]])[0]
    assert score_cells(prof, [[fuori]])[0] < score_cells(prof, [[dentro]])[0]
    assert score_cells(prof, [[]]) == []          # punto fuori copertura: saltato


def test_posizione_relativa_cresta_versante_conca():
    """make_tpi: r ≈ 1 sul crinale, ≈ 0 nel fondo, in mezzo sul versante."""
    from gis.make_tpi import relative_position
    # profilo a V: quota che scende da 200 a 0 e risale, replicato su 40 righe
    prof = np.concatenate([np.arange(200, -1, -10), np.arange(10, 201, 10)]).astype("float32")
    z = np.tile(prof, (40, 1))
    r, valid = relative_position(z, half_rows=5, half_cols=10, min_relief=20.0)
    fondo = int(np.argmin(prof))
    assert valid.all()
    assert r[20, 0] > 0.9                     # spalla alta = cresta dell'intorno
    assert r[20, fondo] < 0.1                 # fondo della V
    assert 0.2 < r[20, fondo // 2] < 0.9      # mezza costa


def test_posizione_relativa_dichiara_il_piatto():
    """Sotto MIN_RELIEF_M il DEM non sa rispondere e la cella resta non misurata."""
    from gis.make_tpi import relative_position
    piano = np.full((30, 30), 12.0, dtype="float32")
    piano += np.random.default_rng(0).normal(0, 0.5, piano.shape).astype("float32")
    _, valid = relative_position(piano, half_rows=5, half_cols=5, min_relief=20.0)
    assert not valid.any()


def test_edge_density_conta_solo_i_confini_bosco_prato():
    from gis.providers import WorldCoverProvider as W
    tutto_bosco = np.full((10, 10), 10, dtype="uint8")
    assert W._edge_pixels(tutto_bosco) == 0
    meta = tutto_bosco.copy()
    meta[:, 5:] = 30                          # un confine verticale netto
    assert W._edge_pixels(meta) == 20         # 10 pixel per lato
    # bosco e seminativo non fanno bordo: il margine che conta è bosco/prato
    con_seminativo = tutto_bosco.copy()
    con_seminativo[:, 5:] = 40
    assert W._edge_pixels(con_seminativo) == 0


# --- inneschi multipli: episodi di pioggia e carica ancorata ------------------------ #

def _serie(rain_by_day: dict, n: int = 40, temp=14.0, moist=0.30):
    """n giorni consecutivi; rain_by_day mappa «giorni fa» → mm caduti quel giorno."""
    from datetime import date, timedelta
    oggi = date(2026, 9, 15)
    return [(oggi - timedelta(days=n - 1 - i),
             float(rain_by_day.get(n - 1 - i, 0.0)), temp, moist) for i in range(n)]


def test_episodio_di_pioggia_e_un_innesco_solo_ancorato_alla_fine():
    from gis import meteo
    # tre giorni consecutivi sopra soglia = una bagnatura, non tre buttate
    anc = meteo.trigger_anchors(_serie({14: 15.0, 13: 20.0, 12: 12.0}))
    assert len(anc) == 1
    assert anc[0] == 40 - 1 - 12                   # l'ultimo giorno del gruppo
    # due episodi separati da una settimana restano due
    assert len(meteo.trigger_anchors(_serie({20: 15.0, 5: 18.0}))) == 2


def test_pioggia_sotto_soglia_non_innesca():
    from gis import meteo
    assert meteo.trigger_anchors(_serie({10: 9.9})) == []


def test_features_per_flush_vede_la_buttata_che_l_ultima_pioggia_nascondeva():
    from engine import load_profiles
    from gis import meteo
    edulis = load_profiles()["boletus_edulis"]     # lag_days.opt = [10, 20]
    daily = _serie({13: 30.0, 1: 25.0})            # innesco vecchio + pioggia di ieri
    assert meteo.features_from_daily(edulis, daily)["days_since_trigger"] == 1
    dsts = [f["days_since_trigger"] for f in meteo.features_per_flush(edulis, daily)]
    assert dsts == [1, 13]                         # la piu' recente per prima, ma ci sono entrambe


def test_la_carica_e_del_suo_innesco_i_gate_sono_di_oggi():
    from engine import load_profiles
    from gis import meteo
    edulis = load_profiles()["boletus_edulis"]     # rain_window_days = 25
    daily = _serie({30: 80.0, 13: 30.0, 1: 12.0})  # un diluvio fuori dalla finestra di oggi
    per_flush = meteo.features_per_flush(edulis, daily)
    vecchia = next(f for f in per_flush if f["days_since_trigger"] == 13)
    oggi = meteo.features_from_daily(edulis, daily)
    # al 13esimo giorno fa la finestra conteneva ancora il diluvio: cumulata piu' alta di oggi
    assert vecchia["cumulative_rain_mm"] > oggi["cumulative_rain_mm"]
    # il gate dell'umidita' invece guarda adesso, perche' un suolo asciutto aborta comunque
    assert vecchia["soil_moisture"] == oggi["soil_moisture"]


def test_gli_inneschi_oltre_l_orizzonte_non_sono_piu_vivi():
    from engine import load_profiles
    from gis import meteo
    edulis = load_profiles()["boletus_edulis"]     # orizzonte = 2 x 20 = 40 giorni
    daily = _serie({50: 40.0, 5: 20.0}, n=60)
    dsts = [f["days_since_trigger"] for f in meteo.features_per_flush(edulis, daily)]
    assert dsts == [5]
