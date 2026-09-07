"""Test del motore — girano senza dati GIS né Telegram (feature sintetiche)."""

from engine import load_profiles, predict, readiness, species_buttons, static_suitability

REG = load_profiles()


def test_carica_i_profili_versionati_e_sono_validi():
    sei_di_bosco = {"boletus_edulis", "boletus_aereus", "boletus_aestivalis",
                    "boletus_pinophilus", "cantharellus_cibarius", "amanita_caesarea"}
    assert sei_di_bosco <= set(REG)
    for p in REG.values():
        assert p.validate() == [], p.validate()


def test_species_buttons_ordinati_per_nome():
    names = [name for _, name in species_buttons(REG)]
    assert names == sorted(names)
    assert len(names) == len(REG)


def test_host_gate_azzera_senza_ospite():
    aereus = REG["boletus_aereus"]
    buona = {"host_class": "querceto", "elevation_m": 400, "aspect": "warm",
             "soil_ph": "acidic", "drainage": "well_drained", "slope_deg": 15}
    senza = {**buona, "host_class": "faggeta"}      # aereus non ha faggio → host 0
    assert static_suitability(aereus, buona) > 0.6
    assert static_suitability(aereus, senza) == 0.0     # host_floor di default = 0


def test_host_sconosciuto_e_neutro_non_gate():
    # senza alcuna info ospite (layer forestale non ancora presente) l'host NON azzera:
    # una mappa solo-DEM deve produrre idoneità parziale, non zero ovunque.
    aereus = REG["boletus_aereus"]
    solo_dem = {"elevation_m": 400, "slope_deg": 15, "aspect": "warm"}
    assert static_suitability(aereus, solo_dem) > 0.5
    # ma host noto-e-incompatibile resta gate a 0
    assert static_suitability(aereus, {**solo_dem, "host_class": "faggeta"}) == 0.0


def test_host_floor_per_specie():
    """`host_floor` declassa invece di vietare — ma è una scelta PER SPECIE, non del motore.

    Misurato col Boyce: all'ovolo un pavimento a 0.12 vale +0.25, al porcino ne costa 0.44.
    Un valore unico avrebbe pagato l'uno col doppio dell'altro, quindi sta nel profilo.
    """
    from copy import deepcopy

    from engine.static_scorer import host_membership
    aereus = REG["boletus_aereus"]
    cella = {"host_class": "faggeta", "elevation_m": 400, "aspect": "warm",
             "soil_ph": "acidic", "drainage": "well_drained", "slope_deg": 15}
    assert host_membership(aereus, cella) == 0.0            # default: veto secco
    con_pavimento = deepcopy(aereus)
    con_pavimento.host_floor = 0.12
    assert host_membership(con_pavimento, cella) == 0.12
    buona = {**cella, "host_class": "querceto"}
    assert static_suitability(con_pavimento, cella) < static_suitability(con_pavimento, buona) / 5
    # un pavimento che arriva al peso host più basso è un errore: l'ospite sbagliato
    # varrebbe quanto uno buono
    con_pavimento.host_floor = 0.9
    assert any("host_floor" in e for e in con_pavimento.validate())


def test_pavimento_host_non_resuscita_la_chioma_morta():
    # il pavimento dice "la categoria è una generalizzazione", non "c'è comunque un albero":
    # dove la chioma è morta (Vaia/bostrico) la conifera resta un non-ospite.
    from copy import deepcopy

    from engine.static_scorer import host_membership
    edulis = deepcopy(REG["boletus_edulis"])
    edulis.host_floor = 0.12
    assert host_membership(edulis, {"host_class": "pecceta", "canopy_alive": 1.0}) == 1.0
    assert host_membership(edulis, {"host_class": "pecceta", "canopy_alive": 0.0}) == 0.0


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


def test_habitat_a_pesi_per_le_specie_di_ecotono():
    from engine.profiles import _from_dict
    from engine.static_scorer import habitat_gate
    # una specie di margine: né bosco né prato, entrambi con un peso
    ecotono = _from_dict({"species": {
        "id": "test_margine", "common_name": "margine", "trophic_mode": "saprotrophic",
        "habitat": {"grassland": 1.0, "forest": 0.7, "cropland": 0.2},
        "static_envelope": {"elevation_m": {"opt": [100, 1000]}}}})
    assert ecotono.validate() == []
    bosco = {"forest_fraction": 1.0, "grassland_fraction": 0.0, "cropland_fraction": 0.0}
    prato = {"forest_fraction": 0.0, "grassland_fraction": 1.0, "cropland_fraction": 0.0}
    misto = {"forest_fraction": 0.5, "grassland_fraction": 0.5, "cropland_fraction": 0.0}
    assert abs(habitat_gate(ecotono, bosco) - 0.7) < 1e-9
    assert abs(habitat_gate(ecotono, prato) - 1.0) < 1e-9
    assert abs(habitat_gate(ecotono, misto) - 0.85) < 1e-9
    # la somma pesata non può sfondare 1, e senza frazioni non c'è gate (unknown ≠ absent)
    tutto = {"forest_fraction": 1.0, "grassland_fraction": 1.0, "cropland_fraction": 1.0}
    assert habitat_gate(ecotono, tutto) == 1.0
    assert habitat_gate(ecotono, {"elevation_m": 500}) == 1.0
    # la forma a stringa resta identica a un dizionario con un peso solo
    prato_puro = _from_dict({"species": {"id": "t", "common_name": "t",
                                         "trophic_mode": "saprotrophic", "habitat": "grassland"}})
    assert habitat_gate(prato_puro, misto) == 0.5


def test_habitat_non_valido_segnalato():
    from engine.profiles import _from_dict
    male = _from_dict({"species": {"id": "t", "common_name": "t", "trophic_mode": "saprotrophic",
                                   "habitat": {"prateria": 1.0, "forest": 3.0}}})
    errs = male.validate()
    assert any("prateria" in e for e in errs)
    assert any("3.0" in e for e in errs)


def test_edge_density_e_opt_in():
    """Il fattore di bordo entra solo se il profilo lo dichiara: chi tace non cambia voto."""
    from engine.profiles import _from_dict
    base = {"id": "t", "common_name": "t", "trophic_mode": "saprotrophic",
            "habitat": "grassland", "static_envelope": {"elevation_m": {"opt": [100, 1000]}}}
    muto = _from_dict({"species": base})
    parla = _from_dict({"species": {**base, "static_envelope": {
        "elevation_m": {"opt": [100, 1000]}, "edge_density": {"opt": [0.05, 0.30]}}}})
    cella = {"elevation_m": 500, "grassland_fraction": 1.0}
    # senza la chiave nella cella il fattore è neutro (feature non misurata)
    assert static_suitability(parla, cella) == static_suitability(muto, cella)
    molto = static_suitability(parla, {**cella, "edge_density": 0.15})
    poco = static_suitability(parla, {**cella, "edge_density": 0.0})
    assert molto > poco
    # e il profilo muto resta indifferente al bordo: nessuno spostamento silenzioso
    assert static_suitability(muto, {**cella, "edge_density": 0.0}) == \
        static_suitability(muto, {**cella, "edge_density": 0.15})


def test_combiner_e_prodotto():
    aereus = REG["boletus_aereus"]
    cell = {"host_class": "querceto", "elevation_m": 400, "aspect": "warm",
            "soil_ph": "acidic", "drainage": "well_drained", "slope_deg": 15}
    feat_secco = {"month": 8, "soil_moisture": 0.05, "cumulative_rain_mm": 60,
                  "soil_temp_c": 17, "thermal_shock_c": 6, "days_since_trigger": 14}
    # habitat perfetto ma readiness 0 (secco) → predizione 0 (spec §1)
    assert predict(aereus, cell, feat_secco) == 0.0


def test_host_floor_su_specie_senza_gate_host_e_segnalato():
    """Un campo che non fa niente deve dirlo: il gate host esiste solo per le micorriziche."""
    from engine.profiles import _from_dict
    sap = _from_dict({"species": {"id": "t", "common_name": "t", "habitat": "grassland",
                                  "trophic_mode": "saprotrophic", "host_floor": 0.5}})
    assert any("host_floor" in e and "non farebbe niente" in e for e in sap.validate())
    # senza il campo, nessun rumore
    muto = _from_dict({"species": {"id": "t", "common_name": "t", "habitat": "grassland",
                                   "trophic_mode": "saprotrophic"}})
    assert muto.validate() == []


def test_numero_scritto_male_non_viene_ingoiato():
    """`host_floor: 0,12` da tastiera italiana diventava 0.0 in silenzio."""
    from engine.profiles import _from_dict
    p = _from_dict({"species": {"id": "t", "common_name": "t", "trophic_mode": "mycorrhizal",
                                "host_genera": {"faggeta": 1.0}, "host_floor": "0,12"}})
    assert p.host_floor == 0.0                      # fail-soft: l'app si avvia lo stesso
    errs = p.validate()
    assert any("non e' un numero" in e for e in errs)
    assert any("separatore decimale" in e for e in errs)
    # la stringa scritta bene invece passa (YAML puo' quotare un numero)
    ok = _from_dict({"species": {"id": "t", "common_name": "t", "trophic_mode": "mycorrhizal",
                                 "host_genera": {"faggeta": 1.0}, "host_floor": "0.12"}})
    assert ok.host_floor == 0.12 and ok.validate() == []
