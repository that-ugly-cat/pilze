"""Scarica le carte forestali di Veneto e Trentino (spec §3.1).

- Veneto: Carta Regionale dei Tipi Forestali — share ownCloud Regione Veneto (no auth),
  5 ZIP per provincia (~52 MB), CRS EPSG:3003, campo CATEGORIA (18 categorie).
- Trentino: "Tipi forestali - SIGFAT" — geocatalogo SIAT/PAT (no auth), 1 ZIP (~189 MB),
  CRS EPSG:25832, campo tipo_fores (54 tipi). Copre le unità dei piani di gestione forestale.

    python -m gis.fetch_forest            # entrambe le regioni

Opzione futura: CFI2020 nazionale (MASAF) → legenda unica VE+TN.
"""

from __future__ import annotations

import sys
import urllib.request
import zipfile
from pathlib import Path

FOREST_BASE = Path(__file__).resolve().parent.parent / "data" / "forest"
FOREST_DIR = FOREST_BASE / "veneto"
TN_DIR = FOREST_BASE / "trentino"
SHARE = "https://sharing.regione.veneto.it/public.php/webdav"
SHARE_TOKEN = "xJTnxeP5q4d4A8f"
PROVINCES = ["CRCF_BL", "CRCF_PD_RO", "CRCF_TV_VE", "CRCF_VI", "CRCF_VR"]
TN_URL = "https://siatservices.provincia.tn.it/idt/vector/p_TN_a0f9772a-da15-4cf0-a661-fa03f3d890d1.zip"


def _opener() -> urllib.request.OpenerDirector:
    mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    mgr.add_password(None, SHARE, SHARE_TOKEN, "")     # share token = user, password vuota
    return urllib.request.build_opener(urllib.request.HTTPBasicAuthHandler(mgr))


def fetch(forest_dir: Path = FOREST_DIR) -> int:
    forest_dir.mkdir(parents=True, exist_ok=True)
    opener = _opener()
    n = 0
    for prov in PROVINCES:
        shp = forest_dir / f"{prov.lower().replace('crcf_', '')}_cat.shp"
        if list(forest_dir.glob(f"{prov.lower().replace('crcf_','')}_cat.shp")):
            print(f"  già presente: {shp.name}")
            n += 1
            continue
        zpath = forest_dir / f"{prov}.zip"
        print(f"  scarico {prov}.zip …", flush=True)
        with opener.open(f"{SHARE}/{prov}.zip", timeout=180) as resp, open(zpath, "wb") as out:
            out.write(resp.read())
        zipfile.ZipFile(zpath).extractall(forest_dir)
        zpath.unlink()
        n += 1
        print(f"    estratto ({prov})")
    return n


def fetch_trentino(tn_dir: Path = TN_DIR) -> bool:
    tn_dir.mkdir(parents=True, exist_ok=True)
    if list(tn_dir.glob("tipi_forestali_v.shp")):
        print("  già presente: tipi_forestali_v.shp")
        return True
    zpath = tn_dir / "tipi_forestali_TN.zip"
    print("  scarico Tipi forestali - SIGFAT (~189 MB) …", flush=True)
    urllib.request.urlretrieve(TN_URL, zpath)
    zipfile.ZipFile(zpath).extractall(tn_dir)
    zpath.unlink()
    print("    estratto (Trentino)")
    return True


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(f"Veneto — Carta Regionale Tipi Forestali → {FOREST_DIR}")
    got = fetch()
    print(f"  province: {got}/{len(PROVINCES)}")
    print(f"Trentino — Tipi forestali SIGFAT → {TN_DIR}")
    fetch_trentino()
