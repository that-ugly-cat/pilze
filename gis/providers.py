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
TPI_DIR = Path(__file__).resolve().parent.parent / "data" / "dem_tpi"
FOREST_DIR = Path(__file__).resolve().parent.parent / "data" / "forest"
SOIL_PATH = Path(__file__).resolve().parent.parent / "data" / "soil" / "phh2o_0-5cm.tif"
CROSSWALK_PATH = Path(__file__).resolve().parent.parent / "config" / "crosswalk.yaml"
AOI_PATH = Path(__file__).resolve().parent.parent / "data" / "aoi" / "aoi.gpkg"
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


class AOIProvider(FeatureProvider):
    """Maschera dell'area con dati tematici: Bolzano + Trento + Veneto. Fuori → None.

    Non porta feature (ritorna un dict vuoto): serve solo a dire DOVE ha senso scorare.
    Il motore tratta l'host sconosciuto come neutro (§7.5, "unknown != absent"), regola
    giusta dentro un'area rilevata e sbagliata fuori, dove promuoverebbe il
    fuori-copertura come se l'ospite fosse quello giusto. Questo provider è il confine
    di validità di quella regola: `is_required`, quindi un None taglia la cella.

    Va messo PRIMO nel composite — fa uscire prima delle query raster/vettoriali.
    """

    is_required = True

    def __init__(self, gpkg_path: Path | str = AOI_PATH):
        import geopandas as gpd
        import shapely
        from pyproj import Transformer

        path = Path(gpkg_path)
        if not path.exists():
            raise FileNotFoundError(
                f"AOI mancante ({path}). Esegui: python -m gis.fetch_boundaries")
        aoi = gpd.read_file(path).to_crs("EPSG:32632")
        self.units = list(aoi["unit"])
        self._geom = shapely.union_all(aoi.geometry.values)
        shapely.prepare(self._geom)                 # indice interno: contains_xy ~µs
        self._contains = shapely.contains_xy
        self.to_utm = Transformer.from_crs("EPSG:4326", "EPSG:32632", always_xy=True)

    def features(self, lat: float, lon: float) -> dict | None:
        x, y = self.to_utm.transform(lon, lat)
        return {} if self._contains(self._geom, x, y) else None


class DEMProvider(FeatureProvider):
    """Quota + pendenza + esposizione + drenaggio da un mosaico di tile Copernicus GLO-30.

    Legge una finestra 3×3 attorno al punto e applica il metodo di Horn (pendenza
    ed esposizione). CRS dei tile: EPSG:4326 → converte i passi in metri alla latitudine.
    Senza quota/pendenza i fattori ambientali resterebbero neutri e la cella verrebbe
    sovrastimata → `is_required`, come l'AOI.

    Il **drenaggio** viene dalla posizione topografica relativa precalcolata da
    `gis.make_tpi` (r = dove sta il pixel fra il fondo e la cresta del suo intorno di
    500 m): crinale → l'acqua se ne va, conca piatta → ci resta. Se `data/dem_tpi/` non
    c'è, la chiave non viene emessa e il fattore resta il 0.5 neutro di sempre: il layer
    è additivo, non obbligatorio.
    """

    is_required = True

    # Soglie su r ∈ [0,1]. Il versante regolare sta attorno a 0.5 e prende `well_drained`;
    # gli estremi sono il crinale e il fondo. `waterlogged` chiede anche il piano, perché
    # un fondo di forra ripida drena comunque.
    DRAINAGE_DRY = 0.75
    DRAINAGE_WELL = 0.30
    DRAINAGE_MOIST = 0.12
    DRAINAGE_FLAT_DEG = 3.0

    def __init__(self, dem_dir: Path | str = DEM_DIR, tpi_dir: Path | str = TPI_DIR):
        self.datasets = [rasterio.open(p) for p in sorted(Path(dem_dir).glob("*.tif"))]
        if not self.datasets:
            raise FileNotFoundError(
                f"Nessun tile DEM in {dem_dir}. Esegui: python -m gis.fetch_dem")
        # stessi nomi, stessa griglia: l'indice (row, col) del DEM vale anche qui
        self.tpi = {p.name: rasterio.open(p) for p in sorted(Path(tpi_dir).glob("*.tif"))}

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

        out = {
            "elevation_m": round(elev, 1),
            "slope_deg": round(slope_deg, 1),
            "aspect": _aspect_to_class(aspect_deg, slope_deg),
        }
        drainage = self._drainage(ds, row, col, slope_deg)
        if drainage is not None:
            out["drainage"] = drainage
        return out

    def _drainage(self, ds, row: int, col: int, slope_deg: float) -> str | None:
        """Classe di drenaggio dalla posizione topografica relativa. None = non misurata."""
        tpi = self.tpi.get(Path(ds.name).name)
        if tpi is None:
            return None
        win = rasterio.windows.Window(col, row, 1, 1)
        raw = int(tpi.read(1, window=win)[0, 0])
        if raw == tpi.nodata:                 # piatto: il DEM non sa rispondere
            return None
        r = raw / 1000.0
        if r >= self.DRAINAGE_DRY:
            return "dry"
        if r >= self.DRAINAGE_WELL:
            return "well_drained"
        if r >= self.DRAINAGE_MOIST or slope_deg >= self.DRAINAGE_FLAT_DEG:
            return "moist"
        return "waterlogged"

    def close(self):
        for ds in list(self.datasets) + list(self.tpi.values()):
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
                f"Nessuno shapefile ({shp_glob}) in {forest_dir} (CFI2020: metti i .shp in data/forest/cfi/)")
        self.field = category_field
        parts = [gpd.read_file(s, columns=[category_field]) for s in shps]
        self.gdf = pd.concat(parts, ignore_index=True).to_crs("EPSG:4326")
        self.gdf[category_field] = self.gdf[category_field].astype(str).str.strip()
        self.sindex = self.gdf.sindex
        with open(crosswalk_path, encoding="utf-8") as fh:
            self.crosswalk = (yaml.safe_load(fh) or {}).get(region, {}) or {}

    @classmethod
    def cfi(cls, crosswalk_path: Path | str = CROSSWALK_PATH) -> "ForestProvider":
        """CFI2020 nazionale — legenda UNICA (campo Ct_CFI) per VE + Trento + Bolzano.
        Copertura completa del bosco. È l'unica fonte forestale (patchwork VE/TN eliminato)."""
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
    DIR_TN = Path(__file__).resolve().parent.parent / "data" / "geology" / "trentino"
    GPKG_BZ = Path(__file__).resolve().parent.parent / "data" / "geology" / "bolzano" / "geologia_bz.gpkg"
    SHP_VE = (Path(__file__).resolve().parent.parent / "data" / "geology" / "veneto"
              / "c0501031_litologiareg_.shp")

    # Trentino — NOME formazione (audit 411 tipi). Vulcanici atesini = acidi.
    TN_CALC = ("CALCARE", "CALCARI", "CALCAREN", "DOLOMIA", "DOLOMIE", "CARNIOLA",
               "CALCISCIST", "OOLIT", "MAIOLICA", "BIANCONE", "SCAGLIA", "CORNA",
               "MARMO", "MARMI", "TRAVERTINO", "ENCRINITE", "ROSSO AMMONITICO",
               "SASS DE LA LUNA", "SELCIFERO",
               # piattaforme carbonatiche dolomitiche a nome di località (VE/TN/BZ)
               "SCILIAR", "SCHLERN", "CONTRIN", "LATEMAR", "MENDOLA", "SERLA", "CASSIANA")
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
        """Carta Geologica Substrato PAT, shapefile LOCALE (campo nome, scaricato dal
        geocatalogo SIAT → point-in-polygon veloce, usabile nella mappa). Fallback: REST."""
        shps = sorted(cls.DIR_TN.glob("*substrato*.shp"))
        if shps:
            return cls(shps[0], "nome", cls.TN_CALC, cls.TN_ACID, **kw)
        if cls.GPKG_TN.exists():
            return cls(cls.GPKG_TN, "NOME", cls.TN_CALC, cls.TN_ACID, **kw)
        return _GeologyREST(cls.TN_REST, cls.TN_CACHE, cls.TN_CALC, cls.TN_ACID)

    @classmethod
    def veneto(cls, **kw) -> "GeologyProvider":
        return cls(cls.SHP_VE, "materiali_", cls.VE_CALC, cls.VE_ACID, **kw)

    @classmethod
    def bolzano(cls, **kw) -> "GeologyProvider":
        """Geologia Alto Adige (WFS PAB → gpkg locale), campo litho = nome formazione.
        Stesse litologie alpine del TN → riusa TN_CALC/TN_ACID (Dolomia→calc, gneiss/
        filladi/vulcaniti atesine→acid). Sintemi quaternari → fallback substrato vicino."""
        return cls(cls.GPKG_BZ, "litho", cls.TN_CALC, cls.TN_ACID, **kw)

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
    """Frazioni di copertura da ESA WorldCover 10 m (spec §3.1) + densità di bordo.

    Gate "è l'habitat giusto?" a copertura COMPLETA: legge una finestra attorno al punto e
    calcola la frazione di pixel di OGNI classe. In static_suitability la combinazione
    scelta da `profile.habitat` moltiplica il punteggio → fuori dall'habitat 0, dentro
    pieno, con gradazione. Risolve l'over-predict dove i layer genere lasciano
    host-sconosciuto. Windowed read (i tile sono ~1 Gpx, non si caricano interi).

    Due cose cambiate il 7 set 2026:

    - **La finestra è in METRI.** Era 0.0025° per lato, che a 46°N vale 555 m in latitudine
      e 385 in longitudine: un rettangolo, per un fatto di gradi e non di ecologia.
    - **`edge_density`**: la quota di pixel della finestra che stanno sul confine
      bosco/prato. Serve alle specie di ecotono, il cui habitat è il margine e non nessuna
      delle due coperture, e che oggi nessun altro layer descrive. Misurata sui punti GBIF
      dentro l'AOI, la mazza di tamburo sta a 61 m mediani dal confine contro i 102 del
      fungo medio, con densità di bordo 3.8×.

    Nota sulla grana: la finestra resta ~25 ha, contro i 4 della cella da 200 m, quindi il
    gate ha una risoluzione effettiva di mezzo chilometro. Costa poco al bosco (il 3% della
    sua area sta in chiazze sotto i 21 ha) e molto al prato (il 28%). `HALF_M` è il posto
    dove si prova a stringerla, quando si vorrà misurarne l'effetto.
    """

    WC_DIR = Path(__file__).resolve().parent.parent / "data" / "worldcover"
    HALF_M = 250.0                # semi-lato della finestra, in metri → cella 500 × 500 m
    # codice WorldCover → nome della classe nei profili (`forest` = nome storico della 10)
    CLASSES = {10: "forest", 20: "shrubland", 30: "grassland", 40: "cropland",
               50: "built_up", 60: "bare", 70: "snow_ice", 80: "water",
               90: "wetland", 100: "moss_lichen"}

    def __init__(self, wc_dir: Path | str = WC_DIR, half_m: float | None = None):
        tifs = sorted(Path(wc_dir).glob("*.tif"))
        if not tifs:
            raise FileNotFoundError(
                f"Nessun tile WorldCover in {wc_dir}. Esegui: python -m gis.fetch_worldcover")
        self.datasets = [rasterio.open(p) for p in tifs]
        self.half_m = float(half_m if half_m is not None else self.HALF_M)

    @staticmethod
    def _edge_pixels(arr: "np.ndarray") -> int:
        """Pixel sul confine bosco/prato (adiacenza a 4, contati da entrambi i lati)."""
        t, g = (arr == 10), (arr == 30)
        if not t.any() or not g.any():
            return 0
        edge = np.zeros(arr.shape, dtype=bool)
        for a, b in ((t, g), (g, t)):
            edge[:-1, :] |= a[:-1, :] & b[1:, :]
            edge[1:, :] |= a[1:, :] & b[:-1, :]
            edge[:, :-1] |= a[:, :-1] & b[:, 1:]
            edge[:, 1:] |= a[:, 1:] & b[:, :-1]
        return int(edge.sum())

    def features(self, lat: float, lon: float) -> dict | None:
        from rasterio.windows import from_bounds
        dlat = self.half_m / 110_540.0
        dlon = self.half_m / (111_320.0 * math.cos(math.radians(lat)))
        for ds in self.datasets:
            b = ds.bounds
            if not (b.left <= lon < b.right and b.bottom <= lat < b.top):
                continue
            win = from_bounds(lon - dlon, lat - dlat, lon + dlon, lat + dlat, ds.transform)
            arr = ds.read(1, window=win, boundless=True, fill_value=0)
            nvalid = int((arr != 0).sum())      # 0 = nodata
            if nvalid == 0:
                return None
            out = {f"{name}_fraction": float((arr == code).sum()) / nvalid
                   for code, name in self.CLASSES.items()}
            out["edge_density"] = self._edge_pixels(arr) / nvalid
            return out
        return None


class CompositeFeatureProvider(FeatureProvider):
    """Fonde le feature di più provider (AOI + DEM + forestale + suolo + disturbo).

    Ordine = priorità crescente: i provider successivi sovrascrivono le chiavi.
    Un provider con `is_required` che ritorna None azzera la cella (fuori area);
    gli altri, tacendo, lasciano semplicemente il fattore neutro. Mettere per primo
    il provider più selettivo (l'AOI) fa uscire subito e risparmia le query pesanti.
    """

    def __init__(self, providers: list[FeatureProvider], require_first: bool = True):
        self.providers = providers
        self.require_first = require_first

    def features(self, lat: float, lon: float) -> dict | None:
        merged: dict = {}
        for i, p in enumerate(self.providers):
            f = p.features(lat, lon)
            if f is None and ((i == 0 and self.require_first) or p.is_required):
                return None
            if f:
                merged.update(f)
        return merged or None
