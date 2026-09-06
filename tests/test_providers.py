"""Test dei provider raster/vector — SKIP se mancano dipendenze o dati locali.

I dati (DEM ~360 MB, forestale ~52 MB) sono gitignored: questi test girano solo
sulla macchina di sviluppo dopo `python -m gis.fetch_dem` / `gis.fetch_forest`.
"""

from pathlib import Path

import pytest

DATA = Path(__file__).resolve().parent.parent / "data"


@pytest.mark.skipif(not list((DATA / "dem").glob("*.tif")) if (DATA / "dem").exists() else True,
                    reason="tile DEM assenti (python -m gis.fetch_dem)")
def test_dem_provider_quota_pendenza():
    pytest.importorskip("rasterio")
    from gis.providers import DEMProvider
    dem = DEMProvider()
    f = dem.features(46.30, 11.60)          # Val di Fiemme
    dem.close()
    assert f is not None
    assert 200 < f["elevation_m"] < 3000
    assert 0 <= f["slope_deg"] <= 90
    assert f["aspect"] in {"warm", "cool", "neutral"}


@pytest.mark.skipif(not list((DATA / "forest" / "cfi").glob("**/*.shp"))
                    if (DATA / "forest" / "cfi").exists() else True,
                    reason="shapefile CFI2020 assenti (data/forest/cfi/)")
def test_forest_provider_cfi():
    pytest.importorskip("geopandas")
    from gis.providers import ForestProvider
    fp = ForestProvider.cfi()                # VE+Trento+Bolzano, campo Ct_CFI
    # punto rappresentativo di un poligono reale → dentro copertura
    rp = fp.gdf.geometry.iloc[0].representative_point()
    f = fp.features(rp.y, rp.x)
    assert f is not None
    if "host" in f:
        assert isinstance(f["host"], dict) and f["host"]


def test_geology_classify_litologia():
    # classificatore litologico → soil_ph (puro, senza rete)
    from gis.providers import GeologyProvider as G
    tn = lambda s: G.classify(s, G.TN_CALC, G.TN_ACID)
    assert tn("DOLOMIA PRINCIPALE") == "calcareous"
    assert tn("CALCARE DI ESINO") == "calcareous"
    assert tn("GRANITO DI BRESSANONE") == "acidic"
    assert tn("PORFIDI QUARZIFERI INFERIORI") == "acidic"   # atesino → acido
    assert tn("TONALITE DI CIMA D'ASTA") == "acidic"        # Adamello/Presanella → acido
    assert tn("GABBRO (Plutone di Predazzo)") == "neutral"  # mafico → neutro
    assert tn("ARENARIA DI VAL GARDENA") == "neutral"       # siliciclastico → intermedio
    ve = lambda s: G.classify(s, G.VE_CALC, G.VE_ACID)
    assert ve("calcari e dolomie di piattaforma") == "calcareous"
    assert ve("Sequenze metamorfitiche pre-Permiane") == "acidic"
    assert ve("ialoclastiti, tufi e brecce") == "neutral"   # vulcanico VE → NEUTRO (non acido)


def test_canopy_alive_mapping():
    # verde E struttura richiesti: chioma viva→1, nudo→0, prato (verde ma no struttura)→basso
    import numpy as np
    from gis import canopy
    ca = canopy.canopy_alive
    assert ca(np.array([0.85]), np.array([0.60]))[0] > 0.9      # conifera intatta
    assert ca(np.array([0.45]), np.array([0.18]))[0] < 0.1      # cleared
    assert ca(np.array([0.80]), np.array([0.20]))[0] < 0.4      # prato/ricrescita: verde ma no struttura


@pytest.mark.skipif(not list((DATA / "canopy").glob("canopy_*.tif"))
                    if (DATA / "canopy").exists() else True,
                    reason="raster canopy assenti (python -m gis.fetch_canopy)")
def test_canopy_provider():
    pytest.importorskip("rasterio")
    from gis.providers import CanopyProvider
    cp = CanopyProvider()
    f = cp.features(46.30, 11.76)               # Paneveggio
    assert f is None or (0.0 <= f["canopy_alive"] <= 1.0)


@pytest.mark.skipif(not (DATA / "soil" / "phh2o_0-5cm.tif").exists(),
                    reason="raster suolo assente (python -m gis.fetch_soil)")
def test_soil_provider_ph():
    pytest.importorskip("rasterio")
    from gis.providers import SoilProvider
    sp = SoilProvider()
    f = sp.features(46.30, 11.60)           # Val di Fiemme
    assert f is not None
    assert f["soil_ph"] in {"acidic", "neutral", "calcareous"}
    assert 3.0 <= f["soil_ph_value"] <= 9.0


@pytest.mark.skipif(not (DATA / "aoi" / "aoi.gpkg").exists(),
                    reason="AOI assente (python -m gis.fetch_boundaries)")
def test_aoi_provider_dentro_e_fuori():
    pytest.importorskip("geopandas")
    from gis.providers import AOIProvider
    aoi = AOIProvider()
    assert aoi.units == ["Bolzano", "Trento", "Veneto"]
    for lat, lon in [(46.30, 11.45), (46.75, 12.05), (45.87, 11.51)]:   # TN, BZ, VE
        assert aoi.features(lat, lon) == {}, (lat, lon)
    for lat, lon in [(46.25, 10.45), (46.40, 12.95), (47.05, 11.40)]:   # LO, FVG, AT
        assert aoi.features(lat, lon) is None, (lat, lon)


def test_composite_taglia_sul_provider_required():
    """Un provider `is_required` che tace azzera la cella; gli altri restano neutri."""
    from gis.providers import CompositeFeatureProvider
    from gis.suitability import FeatureProvider

    class Silent(FeatureProvider):
        def features(self, lat, lon):
            return None

    class Required(Silent):
        is_required = True

    class Base(FeatureProvider):
        def features(self, lat, lon):
            return {"elevation_m": 900}

    assert CompositeFeatureProvider([Base(), Silent()]).features(46.0, 11.0) == {"elevation_m": 900}
    assert CompositeFeatureProvider([Base(), Required()]).features(46.0, 11.0) is None
