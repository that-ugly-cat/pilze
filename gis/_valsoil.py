"""Test (scratch): il suolo (CARG) discrimina sui dati GBIF/iNat? baseline vs +suolo.
Con la CFI come forestale. Bounded per non stressare il server geologico PAT.
    python -m gis._valsoil
"""
import json
import sys
from pathlib import Path

from engine.profiles import load_profiles
from gis.suitability import validate_species
from gis.validate import build_provider

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

reg = load_profiles()
gj = json.loads((Path(__file__).resolve().parent.parent / "data" / "gbif_occurrences.geojson")
                .read_text(encoding="utf-8"))
pts = {}
for f in gj["features"]:
    lon, lat = f["geometry"]["coordinates"]
    pts.setdefault(f["properties"]["species"], []).append({"lat": lat, "lon": lon})

base, ab = build_provider(include_geology=False)
print("baseline:", " + ".join(ab))
geo, ag = build_provider(include_geology=True)
print("con suolo:", " + ".join(ag))
print()

NB = 1200
print(f"{'specie':22s} {'baseline':>9s} {'+suolo':>9s} {'Δ':>7s}")
for sid in ["boletus_edulis", "cantharellus_cibarius", "boletus_pinophilus", "amanita_caesarea"]:
    b = validate_species(reg[sid], base, pts.get(sid, []), n_background=NB)["boyce"]
    g = validate_species(reg[sid], geo, pts.get(sid, []), n_background=NB)["boyce"]
    print(f"{sid:22s} {b:+9.3f} {g:+9.3f} {g-b:+7.3f}")

# persisti la cache geologia REST
for p in geo.providers:
    if hasattr(p, "save_cache"):
        p.save_cache()
