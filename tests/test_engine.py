"""Test del motore — girano senza dati GIS né Telegram (feature sintetiche)."""

from engine import load_profiles, predict, readiness, species_buttons, static_suitability

REG = load_profiles()


def test_carica_sei_profili_validi():
    assert len(REG) == 6
    for p in REG.values():
        assert p.validate() == [], p.validate()


def test_species_buttons_ordinati_per_nome():
    names = [name for _, name in species_buttons(REG)]
    assert names == sorted(names)
    assert len(names) == 6


def test_host_gate_azzera_senza_ospite():
    aereus = REG["boletus_aereus"]
    buona = {"host_class": "querceto", "elevation_m": 400, "aspect": "warm",
             "soil_ph": "acidic", "drainage": "well_drained", "slope_deg": 15}
    senza = {**buona, "host_class": "faggeta"}      # aereus non ha faggio → host 0
    assert static_suitability(aereus, buona) > 0.6
    assert static_suitability(aereus, senza) == 0.0


def test_host_sconosciuto_e_neutro_non_gate():
    # senza alcuna info ospite (layer forestale non ancora presente) l'host NON azzera:
    # una mappa solo-DEM deve produrre idoneità parziale, non zero ovunque.
    aereus = REG["boletus_aereus"]
    solo_dem = {"elevation_m": 400, "slope_deg": 15, "aspect": "warm"}
    assert static_suitability(aereus, solo_dem) > 0.5
    # ma host noto-e-incompatibile resta gate a 0
    assert static_suitability(aereus, {**solo_dem, "host_class": "faggeta"}) == 0.0


def test_elevation_fuori_range_abbassa():
    aereus = REG["boletus_aereus"]                 # opt 200–600, max 800
    base = {"host_class": "querceto", "aspect": "warm", "soil_ph": "acidic",
            "drainage": "well_drained", "slope_deg": 15}
    in_opt = static_suitability(aereus, {**base, "elevation_m": 400})
    troppo_alta = static_suitability(aereus, {**base, "elevation_m": 900})
    assert troppo_alta < in_opt


def test_canopy_morta_declassa_conifera():
    edulis = REG["boletus_edulis"]                 # host abete 1.0
    cell = {"host_class": "pecceta", "elevation_m": 1200, "aspect": "cool",
            "soil_ph": "acidic", "drainage": "well_drained", "slope_deg": 15}
    viva = static_suitability(edulis, {**cell, "canopy_alive": 1.0})
    morta = static_suitability(edulis, {**cell, "canopy_alive": 0.1})
    assert morta < viva


def test_readiness_gate_fenologia():
    aereus = REG["boletus_aereus"]                 # phenology 6..10
    feat = {"cumulative_rain_mm": 60, "soil_moisture": 0.30, "soil_temp_c": 17,
            "thermal_shock_c": 6, "days_since_trigger": 14}
    in_stagione = readiness(aereus, {**feat, "month": 8})
    fuori = readiness(aereus, {**feat, "month": 1})
    assert in_stagione > 0.5
    assert fuori == 0.0


def test_readiness_gate_moisture_floor():
    aereus = REG["boletus_aereus"]                 # moisture_floor 0.20
    feat = {"month": 8, "cumulative_rain_mm": 60, "soil_temp_c": 17,
            "thermal_shock_c": 6, "days_since_trigger": 14}
    ok = readiness(aereus, {**feat, "soil_moisture": 0.30})
    secco = readiness(aereus, {**feat, "soil_moisture": 0.05})
    assert ok > 0.5
    assert secco == 0.0


def test_forest_fraction_gate():
    # gate "è bosco?" (WorldCover): fuori-bosco azzera, frazione scala linearmente
    edulis = REG["boletus_edulis"]
    cell = {"host_class": "pecceta", "elevation_m": 1200, "aspect": "cool",
            "soil_ph": "acidic", "drainage": "well_drained", "slope_deg": 15}
    full = static_suitability(edulis, cell)                      # forest_fraction assente → 1.0
    assert full > 0.5
    assert static_suitability(edulis, {**cell, "forest_fraction": 0.0}) == 0.0
    assert abs(static_suitability(edulis, {**cell, "forest_fraction": 0.5}) - full * 0.5) < 1e-6


def test_habitat_gate_prato_vs_bosco():
    from engine.profiles import _from_dict
    # saprotrofo di prato: gate = grassland_fraction (non forest_fraction)
    prato = _from_dict({"species": {"id": "test_prato", "common_name": "prataiolo",
                                    "trophic_mode": "saprotrophic", "habitat": "grassland",
                                    "static_envelope": {"elevation_m": {"opt": [100, 1000]}}}})
    assert prato.validate() == []
    grass = {"elevation_m": 400, "forest_fraction": 0.0, "grassland_fraction": 0.9}
    wood = {"elevation_m": 400, "forest_fraction": 0.9, "grassland_fraction": 0.0}
    assert static_suitability(prato, grass) > 0.5      # sul prato: alto
    assert static_suitability(prato, wood) == 0.0      # in bosco: 0 (grassland_fraction 0)
    # micorrizica di bosco: comportamento opposto (gate forest_fraction)
    edulis = REG["boletus_edulis"]                     # opt 800-1600
    hb = {"host_class": "pecceta", "elevation_m": 1000, "aspect": "cool", "soil_ph": "acidic",
          "drainage": "well_drained", "slope_deg": 15}
    assert static_suitability(edulis, {**hb, "forest_fraction": 0.9}) > 0.0
    assert static_suitability(edulis, {**hb, "forest_fraction": 0.0}) == 0.0


def test_combiner_e_prodotto():
    aereus = REG["boletus_aereus"]
    cell = {"host_class": "querceto", "elevation_m": 400, "aspect": "warm",
            "soil_ph": "acidic", "drainage": "well_drained", "slope_deg": 15}
    feat_secco = {"month": 8, "soil_moisture": 0.05, "cumulative_rain_mm": 60,
                  "soil_temp_c": 17, "thermal_shock_c": 6, "days_since_trigger": 14}
    # habitat perfetto ma readiness 0 (secco) → predizione 0 (spec §1)
    assert predict(aereus, cell, feat_secco) == 0.0
