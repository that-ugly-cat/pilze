"""Punti di presenza da GBIF (spec §6.3) — set di validazione dell'habitat.

Prima di avere ritrovamenti propri, la mappa statica si valida coi punti di presenza
GBIF/iNaturalist: testano l'idoneità dell'HABITAT, non il timing (§6.3).
Solo stdlib (urllib). iNaturalist arriva a GBIF (datasetKey), quindi qui basta GBIF.

API: https://api.gbif.org/v1/ — nessuna chiave.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

GBIF = "https://api.gbif.org/v1"
UA = {"User-Agent": "mappa-funghi/0.1 (personal research)"}


def scientific_name(species_id: str) -> str:
    """boletus_aereus -> 'Boletus aereus'."""
    return species_id.replace("_", " ").capitalize()


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def taxon_key(name: str) -> int | None:
    """Chiave GBIF del taxon. Se il nome è un SINONIMO (es. Boletus aestivalis →
    accepted Boletus reticulatus) usa la chiave ACCETTATA, che ha le occorrenze."""
    data = _get(f"{GBIF}/species/match?{urllib.parse.urlencode({'name': name})}")
    return data.get("acceptedUsageKey") or data.get("usageKey")


def _bbox_wkt(bb: dict) -> str:
    # POLYGON counter-clockwise, anello chiuso (richiesto da GBIF)
    lo, la, Lo, La = bb["lon_min"], bb["lat_min"], bb["lon_max"], bb["lat_max"]
    return (f"POLYGON(({lo} {la}, {Lo} {la}, {Lo} {La}, {lo} {La}, {lo} {la}))")


def fetch_occurrences(name: str, bbox: dict, max_records: int = 1000) -> list[dict]:
    """Occorrenze georiferite di `name` dentro il bbox. Ritorna [{lat,lon,year,key}]."""
    key = taxon_key(name)
    if key is None:
        return []
    wkt = _bbox_wkt(bbox)
    out: list[dict] = []
    offset, page = 0, 300
    while offset < max_records:
        params = urllib.parse.urlencode({
            "taxonKey": key, "geometry": wkt, "hasCoordinate": "true",
            "hasGeospatialIssue": "false", "limit": page, "offset": offset,
        })
        data = _get(f"{GBIF}/occurrence/search?{params}")
        for r in data.get("results", []):
            if r.get("decimalLatitude") is None:
                continue
            out.append({"lat": r["decimalLatitude"], "lon": r["decimalLongitude"],
                        "year": r.get("year"), "key": r.get("key")})
        if data.get("endOfRecords") or not data.get("results"):
            break
        offset += page
    return out[:max_records]


def to_geojson(species_points: dict[str, list[dict]]) -> dict:
    feats = []
    for sid, pts in species_points.items():
        for p in pts:
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
                "properties": {"species": sid, "year": p.get("year"), "gbif": p.get("key")},
            })
    return {"type": "FeatureCollection", "features": feats}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    import yaml
    from engine.profiles import load_profiles

    cfg = yaml.safe_load(open(Path(__file__).resolve().parent.parent / "config" / "grid.yaml",
                              encoding="utf-8"))
    bbox = cfg["bbox_wgs84"]
    reg = load_profiles()
    all_points: dict[str, list[dict]] = {}
    print(f"GBIF — presenze in bbox VE+TN {bbox}:")
    for sid in reg:
        pts = fetch_occurrences(scientific_name(sid), bbox, max_records=1000)
        all_points[sid] = pts
        yrs = [p["year"] for p in pts if p.get("year")]
        span = f"{min(yrs)}–{max(yrs)}" if yrs else "n/d"
        print(f"  {sid:24s} {len(pts):5d} punti   anni {span}")
    out = Path(__file__).resolve().parent.parent / "data" / "gbif_occurrences.geojson"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(to_geojson(all_points)), encoding="utf-8")
    print(f"\nGeoJSON scritto: {out} ({sum(len(v) for v in all_points.values())} punti totali)")
