"""Provider di feature reali per cella (traccia A). Innesto dei layer nel motore.

Ogni provider implementa engine-side `FeatureProvider.features(lat,lon) -> dict|None`
e riempie le SUE chiavi; il CompositeFeatureProvider le fonde. Qui c'è il DEMProvider
(quota/pendenza/esposizione da Copernicus GLO-30). Forestale/suolo/disturbo: TODO.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import rasterio
import yaml

from .suitability import FeatureProvider

DEM_DIR = Path(__file__).resolve().parent.parent / "data" / "dem"
FOREST_DIR = Path(__file__).resolve().parent.parent / "data" / "forest"
SOIL_PATH = Path(__file__).resolve().parent.parent / "data" / "soil" / "phh2o_0-5cm.tif"
CROSSWALK_PATH = Path(__file__).resolve().parent.parent / "config" / "crosswalk.yaml"
IGH = "+proj=igh +lat_0=0 +lon_0=0 +x_0=0 +y_0=0 +ellps=WGS84 +units=m +no_defs"


def _aspect_to_class(aspect_deg: float, slope_deg: float) -> str:
    """Esposizione (0=N,90=E,180=S,270=O) → classe di calore per soil/aspect del profilo."""
    if slope_deg < 3.0:
        return "neutral"                      # quasi piano: nessuna esposizione dominante
    if 112.5 <= aspect_deg <= 247.5:
        return "warm"                         # SE–S–SO
    if aspect_deg >= 292.5 or aspect_deg <= 67.5:
        return "cool"                         # NO–N–NE
    return "neutral"                          # E / O


class DEMProvider(FeatureProvider):
    """Quota + pendenza + esposizione da un mosaico di tile Copernicus GLO-30.

    Legge una finestra 3×3 attorno al punto e applica il metodo di Horn (pendenza
    ed esposizione). CRS dei tile: EPSG:4326 → converte i passi in metri alla latitudine.
    """

    def __init__(self, dem_dir: Path | str = DEM_DIR):
        self.datasets = [rasterio.open(p) for p in sorted(Path(dem_dir).glob("*.tif"))]
        if not self.datasets:
            raise FileNotFoundError(
                f"Nessun tile DEM in {dem_dir}. Esegui: python -m gis.fetch_dem")

    def _dataset_for(self, lon: float, lat: float):
        for ds in self.datasets:
            b = ds.bounds
            if b.left <= lon < b.right and b.bottom <= lat < b.top:
                return ds
        return None

    def features(self, lat: float, lon: float) -> dict | None:
        ds = self._dataset_for(lon, lat)
        if ds is None:
            return None
        row, col = ds.index(lon, lat)
        if not (1 <= row < ds.height - 1 and 1 <= col < ds.width - 1):
            return None                       # bordo tile: niente finestra 3×3
        win = rasterio.windows.Window(col - 1, row - 1, 3, 3)
        z = ds.read(1, window=win).astype("float64")
        if z.shape != (3, 3):
            return None
        nodata = ds.nodata
        if nodata is not None and (z == nodata).any():
            return None

        elev = float(z[1, 1])
        # passi in metri alla latitudine (tile in gradi)
        dx_deg, dy_deg = ds.transform.a, -ds.transform.e
        cx = dx_deg * 111_320.0 * math.cos(math.radians(lat))
        cy = dy_deg * 110_540.0
        # Horn: z indicizzata [riga][col], riga 0 = nord
        dzdx = ((z[0, 2] + 2*z[1, 2] + z[2, 2]) - (z[0, 0] + 2*z[1, 0] + z[2, 0])) / (8 * cx)
        dzdy = ((z[2, 0] + 2*z[2, 1] + z[2, 2]) - (z[0, 0] + 2*z[0, 1] + z[0, 2])) / (8 * cy)
        slope_deg = math.degrees(math.atan(math.hypot(dzdx, dzdy)))
        # direzione di affaccio (downhill) = -gradiente; bearing orario da nord
        aspect_deg = math.degrees(math.atan2(-dzdx, -dzdy)) % 360.0

        return {
            "elevation_m": round(elev, 1),
            "slope_deg": round(slope_deg, 1),
            "aspect": _aspect_to_class(aspect_deg, slope_deg),
        }

    def close(self):
        for ds in self.datasets:
            ds.close()


class ForestProvider(FeatureProvider):
    """Composizione di generi ospite dalla carta forestale, via crosswalk (spec §3.3).

    Legge gli shapefile della carta forestale, fa point-in-polygon e traduce la
    categoria → composizione {genere: peso} col crosswalk della regione. Fuori dai
    poligoni → None: host resta SCONOSCIUTO (non azzera, §7.5). Il campo e la sorgente
    cambiano per regione (Veneto: CATEGORIA; Trentino: tipo_fores) → costruttori dedicati.
    """

    def __init__(self, forest_dir: Path | str, shp_glob: str, category_field: str,
                 region: str, crosswalk_path: Path | str = CROSSWALK_PATH):
        import geopandas as gpd
        import pandas as pd

        shps = sorted(Path(forest_dir).glob(shp_glob))
        if not shps:
            raise FileNotFoundError(
                f"Nessuno shapefile ({shp_glob}) in {forest_dir}. Esegui: python -m gis.fetch_forest")
        self.field = category_field
        parts = [gpd.read_file(s, columns=[category_field]) for s in shps]
        self.gdf = pd.concat(parts, ignore_index=True).to_crs("EPSG:4326")
        self.gdf[category_field] = self.gdf[category_field].astype(str).str.strip()
        self.sindex = self.gdf.sindex
        with open(crosswalk_path, encoding="utf-8") as fh:
            self.crosswalk = (yaml.safe_load(fh) or {}).get(region, {}) or {}

    @classmethod
    def veneto(cls, crosswalk_path: Path | str = CROSSWALK_PATH) -> "ForestProvider":
        return cls(FOREST_DIR / "veneto", "*_cat.shp", "CATEGORIA", "veneto", crosswalk_path)

    @classmethod
    def trentino(cls, crosswalk_path: Path | str = CROSSWALK_PATH) -> "ForestProvider":
        return cls(FOREST_DIR / "trentino", "tipi_forestali_v.shp", "tipo_fores", "trentino",
                   crosswalk_path)

    @classmethod
    def cfi(cls, crosswalk_path: Path | str = CROSSWALK_PATH) -> "ForestProvider":
        """CFI2020 nazionale — legenda UNICA (campo Ct_CFI) per VE + Trento + Bolzano.
        Copertura completa del bosco → sostituisce il patchwork veneto()/trentino()."""
        return cls(FOREST_DIR / "cfi", "**/*.shp", "Ct_CFI", "cfi", crosswalk_path)

    def features(self, lat: float, lon: float) -> dict | None:
        from shapely.geometry import Point
        pt = Point(lon, lat)
        for i in self.sindex.query(pt, predicate="intersects"):
            geom = self.gdf.geometry.iloc[int(i)]
            if geom.contains(pt):
                cat = self.gdf[self.field].iloc[int(i)]
                comp = self.crosswalk.get(cat)
                if comp is None:
                    return {"forest_categoria": cat}   # categoria non mappata: nota, host ignoto
                return {"host": dict(comp), "forest_categoria": cat}
        return None


class SoilProvider(FeatureProvider):
    """Reazione del suolo (soil_ph) da SoilGrids phh2o (spec §3.1).

    Classifica il pH in acidic/neutral/calcareous per soglia. Il GeoTIFF WCS ha il
    geotransform ma non il CRS → si assegna Homolosine (IGH) e si trasformano i punti.
    NB (§2): discriminatore DEBOLE (quasi tutte le specie sono acidofile) — completa il
    fattore, non lo domina.
    """

    def __init__(self, soil_path: Path | str = SOIL_PATH,
                 acidic_below: float = 6.2, calcareous_above: float = 7.2):
        from pyproj import Transformer

        if not Path(soil_path).exists():
            raise FileNotFoundError(
                f"Raster suolo assente: {soil_path}. Esegui: python -m gis.fetch_soil")
        with rasterio.open(soil_path) as ds:
            self.data = ds.read(1)
            self.inv = ~ds.transform          # (x,y) IGH -> (col,row)
            self.height, self.width = ds.height, ds.width
        self.to_igh = Transformer.from_crs("EPSG:4326", IGH, always_xy=True)
        self.acidic_below = acidic_below
        self.calcareous_above = calcareous_above

    def features(self, lat: float, lon: float) -> dict | None:
        x, y = self.to_igh.transform(lon, lat)
        col, row = self.inv * (x, y)
        col, row = int(col), int(row)
        if not (0 <= row < self.height and 0 <= col < self.width):
            return None
        raw = float(self.data[row, col])
        if raw <= 0:                          # 0 = nodata (no soil)
            return None
        ph = raw / 10.0
        cat = ("acidic" if ph < self.acidic_below else
               "calcareous" if ph > self.calcareous_above else "neutral")
        return {"soil_ph": cat, "soil_ph_value": round(ph, 1)}


class GeologyProvider(FeatureProvider):
    """soil_ph dalla litologia del substrato (CARG, spec §3.1) — Trentino e Veneto.

    Point-in-polygon LOCALE sui poligoni geologici, descrittore litologico → acido/
    neutro/calcareo per keyword. A differenza di SoilGrids (pH globale levigato), il
    substrato dà il contrasto NETTO carbonato(calcareo) vs cristallino(acido). Fallback:
    dove il quaternario copre il substrato (gap), usa il poligono più vicino entro
    `max_fallback_m` (eredita la litologia di provenienza locale).

    Keyword-set PER REGIONE: i vulcanici TN (porfidi permiani riolitici) sono ACIDI; i
    vulcanici VE (Euganei/Lessini, basaltico-trachitici cenozoici) danno suoli NEUTRI.
    """

    GPKG_TN = Path(__file__).resolve().parent.parent / "data" / "geology" / "substrato_tn.gpkg"
    SHP_VE = (Path(__file__).resolve().parent.parent / "data" / "geology" / "veneto"
              / "c0501031_litologiareg_.shp")

    # Trentino — NOME formazione (audit 411 tipi). Vulcanici atesini = acidi.
    TN_CALC = ("CALCARE", "CALCARI", "CALCAREN", "DOLOMIA", "DOLOMIE", "CARNIOLA",
               "CALCISCIST", "OOLIT", "MAIOLICA", "BIANCONE", "SCAGLIA", "CORNA",
               "MARMO", "MARMI", "TRAVERTINO", "ENCRINITE", "ROSSO AMMONITICO",
               "SASS DE LA LUNA", "SELCIFERO")
    TN_ACID = ("GRANIT", "GRANODIOR", "MONZOGRAN", "APLIT", "PEGMATIT", "PORFI",
               "FILLAD", "GNEISS", "MICASCIST", "SCISTI", "QUARZIT", "QUARZO",
               "RIOLIT", "DACIT", "FELSIT", "IGNIMBRIT", "TUFO", "TUFF", "VULCAN",
               "ATESIN", "GARGAZZONE", "AUCCIA", "PIROCLAST", "LATIT", "TONALIT",
               "SIENIT", "LEUCOMONZONIT", "VERRUCANO", "MILONIT")
    # Veneto — campo materiali_ (54 descrizioni). Vulcanici cenozoici = NEUTRI (non acidi);
    # acido solo il basamento metamorfico pre-Permiano.
    VE_CALC = ("CALCAR", "DOLOMI", "CALCAREN", "ENCRINIT", "OOLIT", "MARMO",
               "CALCESCIST", "CALCISCIST")
    VE_ACID = ("METAMORF", "FILLAD", "MICASCIST", "GNEISS", "SCISTI", "QUARZIT", "MIGMATIT",
               "RIOLIT", "IGNIMBRIT")   # felsici; trachiti/basalti/latiti VE restano neutri

    # TN via REST-per-punto (il server geologico PAT blocca il bulk sostenuto; le query
    # puntuali sono leggere e tollerate). Cache su disco per non ripetere.
    TN_REST = ("https://geoservices.provincia.tn.it/agol/rest/services/geologico/"
               "BDG12_Geologia/MapServer/6/query")
    TN_CACHE = Path(__file__).resolve().parent.parent / "data" / "geology" / "tn_cache.json"

    def __init__(self, data_path: Path | str, field: str,
                 calc_kw: tuple, acid_kw: tuple, max_fallback_m: float = 2000.0):
        import geopandas as gpd
        from pyproj import Transformer

        if not Path(data_path).exists():
            raise FileNotFoundError(f"Litologia assente: {data_path}")
        self.field, self.calc_kw, self.acid_kw = field, calc_kw, acid_kw
        self.gdf = gpd.read_file(data_path).to_crs("EPSG:32632")   # metrico
        self.gdf[field] = self.gdf[field].astype(str).str.strip()
        self.sindex = self.gdf.sindex
        self.to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32632", always_xy=True)
        self.max_fallback_m = max_fallback_m

    @classmethod
    def trentino(cls, **kw) -> "FeatureProvider":
        """Locale se il gpkg c'è (veloce), altrimenti REST-per-punto con cache (gentile)."""
        if cls.GPKG_TN.exists():
            return cls(cls.GPKG_TN, "NOME", cls.TN_CALC, cls.TN_ACID, **kw)
        return _GeologyREST(cls.TN_REST, cls.TN_CACHE, cls.TN_CALC, cls.TN_ACID)

    @classmethod
    def veneto(cls, **kw) -> "GeologyProvider":
        return cls(cls.SHP_VE, "materiali_", cls.VE_CALC, cls.VE_ACID, **kw)

    @staticmethod
    def classify(descr: str, calc_kw: tuple, acid_kw: tuple) -> str:
        u = descr.upper()
        if any(k in u for k in calc_kw):
            return "calcareous"          # carbonato dominante (anche in descrizioni miste)
        if any(k in u for k in acid_kw):
            return "acidic"
        return "neutral"                 # siliciclastici/marne/vulcaniti mafiche/quaternario

    def features(self, lat: float, lon: float) -> dict | None:
        from shapely.geometry import Point
        x, y = self.to_utm.transform(lon, lat)
        pt = Point(x, y)
        # 1) substrato affiorante: point-in-polygon
        for i in self.sindex.query(pt, predicate="intersects"):
            if self.gdf.geometry.iloc[int(i)].contains(pt):
                d = self.gdf[self.field].iloc[int(i)]
                return {"soil_ph": self.classify(d, self.calc_kw, self.acid_kw),
                        "geology_descr": d}
        # 2) coperto da quaternario → poligono più vicino entro max_fallback_m (provenienza)
        cand = list(self.sindex.query(pt.buffer(self.max_fallback_m), predicate="intersects"))
        if cand:
            i = min(cand, key=lambda j: self.gdf.geometry.iloc[int(j)].distance(pt))
            if self.gdf.geometry.iloc[int(i)].distance(pt) <= self.max_fallback_m:
                d = self.gdf[self.field].iloc[int(i)]
                return {"soil_ph": self.classify(d, self.calc_kw, self.acid_kw),
                        "geology_descr": d, "geology_fallback": True}
        return None


class _GeologyREST(FeatureProvider):
    """GeologyProvider Trentino via ArcGIS REST per-punto + cache su disco.

    Fallback quando il bulk locale non c'è (il server PAT blocca il bulk sostenuto ma
    tollera le query puntuali). Stessa semantica/classificazione del provider locale.
    """

    def __init__(self, url: str, cache_path: Path, calc_kw: tuple, acid_kw: tuple):
        import json
        self.url, self.cache_path = url, Path(cache_path)
        self.calc_kw, self.acid_kw = calc_kw, acid_kw
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache = json.loads(self.cache_path.read_text(encoding="utf-8")) \
            if self.cache_path.exists() else {}
        self._dirty = 0

    def _nome(self, lat: float, lon: float) -> str | None:
        key = f"{lat:.4f},{lon:.4f}"
        if key in self._cache:
            return self._cache[key]
        import json
        import urllib.parse
        import urllib.request
        p = urllib.parse.urlencode({"geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint",
                                    "inSR": "4326", "spatialRel": "esriSpatialRelIntersects",
                                    "outFields": "NOME", "returnGeometry": "false", "f": "json"})
        try:
            d = json.loads(urllib.request.urlopen(f"{self.url}?{p}", timeout=30).read())
            feats = d.get("features", [])
            nome = feats[0]["attributes"].get("NOME") if feats else None
        except Exception:
            nome = None
        self._cache[key] = nome
        self._dirty += 1
        if self._dirty >= 100:
            self.save_cache()
        return nome

    def save_cache(self) -> None:
        import json
        self.cache_path.write_text(json.dumps(self._cache), encoding="utf-8")
        self._dirty = 0

    def features(self, lat: float, lon: float) -> dict | None:
        nome = self._nome(lat, lon)
        if not nome:
            return None
        return {"soil_ph": GeologyProvider.classify(nome, self.calc_kw, self.acid_kw),
                "geology_descr": nome}


class CanopyProvider(FeatureProvider):
    """canopy_alive da Sentinel-2 (spec §3.1) — reality-check "chioma viva oggi".

    Legge i raster canopy_alive generati da fetch_canopy (composite estivo NDVI×NBR,
    CRS UTM 32N). Point-in-raster come DEMProvider. Fuori copertura → None: nessun
    declassamento (host_membership tratta canopy_alive assente come "chioma intatta").
    Bersaglio: stand conifera dove la carta forestale è stale (Vaia/bostrico/tagli).
    """

    CANOPY_DIR = Path(__file__).resolve().parent.parent / "data" / "canopy"

    def __init__(self, canopy_dir: Path | str = CANOPY_DIR):
        from pyproj import Transformer
        tifs = sorted(p for p in Path(canopy_dir).glob("canopy_*.tif"))
        if not tifs:
            raise FileNotFoundError(
                f"Nessun raster canopy in {canopy_dir}. Esegui: python -m gis.fetch_canopy")
        self.tiles = []                          # array in memoria (i tile sono piccoli)
        for p in tifs:
            with rasterio.open(p) as ds:
                self.tiles.append((ds.read(1), ds.bounds, ~ds.transform, ds.height, ds.width))
        crs0 = rasterio.open(tifs[0]).crs        # i tile Sentinel sono UTM 32N (condiviso)
        self.to_crs = Transformer.from_crs("EPSG:4326", crs0, always_xy=True)

    def features(self, lat: float, lon: float) -> dict | None:
        x, y = self.to_crs.transform(lon, lat)
        for arr, b, inv, h, w in self.tiles:
            if not (b.left <= x < b.right and b.bottom <= y < b.top):
                continue
            col, row = inv * (x, y)
            col, row = int(col), int(row)
            if not (0 <= row < h and 0 <= col < w):
                continue
            val = arr[row, col]
            return None if not np.isfinite(val) else {"canopy_alive": float(np.clip(val, 0, 1))}
        return None


class WorldCoverProvider(FeatureProvider):
    """Frazione di copertura arborea da ESA WorldCover 10 m (spec §3.1) → forest_fraction.

    Gate "è bosco?" a copertura COMPLETA: legge una finestra ~500 m attorno al punto e
    calcola la frazione di pixel classe 10 (tree cover). In static_suitability moltiplica
    il punteggio → fuori-bosco 0, dentro-bosco pieno, con gradazione. Risolve l'over-
    predict dove i layer genere (TN parziale) lasciano host-sconosciuto, senza bucare il TN.
    Windowed read (memory-safe: i tile sono ~1 Gpx, non si caricano interi).
    """

    WC_DIR = Path(__file__).resolve().parent.parent / "data" / "worldcover"
    HALF_DEG = 0.0025             # semi-lato finestra ~250–280 m → cella ~500 m

    def __init__(self, wc_dir: Path | str = WC_DIR):
        tifs = sorted(Path(wc_dir).glob("*.tif"))
        if not tifs:
            raise FileNotFoundError(
                f"Nessun tile WorldCover in {wc_dir}. Esegui: python -m gis.fetch_worldcover")
        self.datasets = [rasterio.open(p) for p in tifs]

    def features(self, lat: float, lon: float) -> dict | None:
        from rasterio.windows import from_bounds
        h = self.HALF_DEG
        for ds in self.datasets:
            b = ds.bounds
            if not (b.left <= lon < b.right and b.bottom <= lat < b.top):
                continue
            win = from_bounds(lon - h, lat - h, lon + h, lat + h, ds.transform)
            arr = ds.read(1, window=win, boundless=True, fill_value=0)
            valid = arr != 0                     # 0 = nodata
            nvalid = int(valid.sum())
            if nvalid == 0:
                return None
            return {"forest_fraction": float((arr == 10).sum()) / nvalid}
        return None


class CompositeFeatureProvider(FeatureProvider):
    """Fonde le feature di più provider (DEM + forestale + suolo + disturbo).

    Ordine = priorità crescente: i provider successivi sovrascrivono le chiavi.
    Se il DEM (base) non copre il punto → None (fuori area).
    """

    def __init__(self, providers: list[FeatureProvider], require_first: bool = True):
        self.providers = providers
        self.require_first = require_first

    def features(self, lat: float, lon: float) -> dict | None:
        merged: dict = {}
        for i, p in enumerate(self.providers):
            f = p.features(lat, lon)
            if f is None and i == 0 and self.require_first:
                return None
            if f:
                merged.update(f)
        return merged or None
