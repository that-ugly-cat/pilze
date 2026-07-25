"""Pilze — web app (spec UI). FastAPI + SQLite + Leaflet.

Login obbligatorio; mappa topografica; selezione specie; tre layer (idoneità statica /
pronte oggi / ritrovamenti da Telegram); pagina admin per creare/revocare account.
Ritrovamenti CONDIVISI tra gli account (decisione A: solo persone fidate).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.request
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import (HTMLResponse, JSONResponse, RedirectResponse, Response)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from bot import db as obsdb
from engine.profiles import (PROFILES_DIR, load_profiles, parse_profile_text,
                             seed_profiles)

from . import auth, regen, render

BASE = Path(__file__).resolve().parent
MAPS_DIR = Path(__file__).resolve().parent.parent / "data" / "maps"
PHOTO_CACHE = Path(__file__).resolve().parent.parent / "data" / "photos"

app = FastAPI(title="Pilze")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")

seed_profiles()          # popola il volume dai default se vuoto (primo avvio sul VPS)
REG = load_profiles()


def _reload_profiles():
    global REG
    REG = load_profiles()

_ASSET_HASH: dict[str, str] = {}


def asset(name: str) -> str:
    """URL di uno static con cache-buster = hash del contenuto (una volta per processo).

    L'immagine Docker si ricostruisce a ogni deploy → nuovo processo → l'hash cambia
    solo per i file effettivamente modificati; il browser ri-scarica solo quelli.
    """
    if name not in _ASSET_HASH:
        try:
            _ASSET_HASH[name] = hashlib.md5((BASE / "static" / name).read_bytes()).hexdigest()[:8]
        except FileNotFoundError:
            _ASSET_HASH[name] = "0"
    return f"/static/{name}?v={_ASSET_HASH[name]}"


templates.env.globals["asset"] = asset


@app.on_event("startup")
def _startup():
    auth.ensure_bootstrap_admin()
    obsdb.init_db()          # crea la tabella observations se il bot non ha ancora girato


def _user(request: Request):
    return auth.session_user(request.cookies.get("pilze_session"))


# --- auth ---------------------------------------------------------------- #
@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"err": None})


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if auth.verify(username, password):
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie("pilze_session", auth.open_session(username), httponly=True, samesite="lax")
        return resp
    return templates.TemplateResponse(request, "login.html", {"err": "Credenziali errate"})


@app.get("/logout")
def logout(request: Request):
    auth.close_session(request.cookies.get("pilze_session"))
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("pilze_session")
    return resp


# --- pagine -------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    u = _user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    species = [{"id": p.id, "common": p.common_name, "scientific": p.scientific_name}
               for p in sorted(REG.values(), key=lambda p: p.common_name)]
    return templates.TemplateResponse(request, "map.html", {"user": u, "species": species})


@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request):
    u = _user(request)
    if not u or not u["is_admin"]:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "admin.html",
                                      {"user": u, "users": auth.list_users()})


@app.post("/admin/create")
def admin_create(request: Request, username: str = Form(...), password: str = Form(...),
                 is_admin: bool = Form(False)):
    u = _user(request)
    if u and u["is_admin"]:
        auth.create_user(username.strip(), password, is_admin=is_admin)
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/revoke")
def admin_revoke(request: Request, username: str = Form(...)):
    u = _user(request)
    if u and u["is_admin"] and username != u["username"]:
        auth.revoke_user(username)
    return RedirectResponse("/admin", status_code=303)


# --- editor profili + rigenerazione mappe (solo admin) ------------------- #
_PROFILE_RE = re.compile(r"^[a-z0-9_]+\.yaml$")


def _is_admin(request: Request):
    u = _user(request)
    return u if (u and u["is_admin"]) else None


def _profiles_list():
    return [{"name": f.name, "text": f.read_text(encoding="utf-8")}
            for f in sorted(PROFILES_DIR.glob("*.yaml"))]


@app.get("/admin/profiles", response_class=HTMLResponse)
def profiles_page(request: Request):
    u = _is_admin(request)
    if not u:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "profiles.html",
                                      {"user": u, "profiles": _profiles_list()})


@app.post("/admin/profiles/save")
def profiles_save(request: Request, name: str = Form(...), content: str = Form(...)):
    if not _is_admin(request):
        return JSONResponse({"ok": False, "errors": ["non autorizzato"]}, status_code=403)
    # regex = anche anti-traversal (niente '/' né '..'); il file può non esistere ancora
    # → stesso endpoint per creare una specie nuova o aggiornarne una esistente.
    if not _PROFILE_RE.match(name):
        return JSONResponse({"ok": False, "errors": [f"nome non valido: {name}"]}, status_code=400)
    try:
        prof = parse_profile_text(content)
    except Exception as e:
        return JSONResponse({"ok": False, "errors": [f"YAML non valido: {e}"]})
    errs = prof.validate()
    if prof.id != name[:-5]:
        errs.append(f"id '{prof.id}' ≠ nome file '{name[:-5]}'")
    if errs:
        return JSONResponse({"ok": False, "errors": errs})
    (PROFILES_DIR / name).write_text(content, encoding="utf-8")
    _reload_profiles()
    return JSONResponse({"ok": True})


@app.post("/admin/regen")
def admin_regen(request: Request, species: str = Form("")):
    if not _is_admin(request):
        return JSONResponse({"ok": False}, status_code=403)
    only = [s.strip() for s in species.split(",") if s.strip()] or None
    ok = regen.start(species=only, started_by=_user(request)["username"])
    return JSONResponse({"ok": ok, "status": regen.status()})


@app.get("/admin/regen/status")
def admin_regen_status(request: Request):
    if not _is_admin(request):
        return JSONResponse({}, status_code=403)
    return JSONResponse(regen.status())


@app.get("/admin/docs", response_class=HTMLResponse)
def docs_page(request: Request):
    u = _is_admin(request)
    if not u:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "docs.html", {"user": u})


# --- API (tutte richiedono login) ---------------------------------------- #
def _guard(request: Request):
    return _user(request) is not None


@app.get("/api/suitability/{species}.png")
def suitability_png(request: Request, species: str):
    if not _guard(request):
        return Response(status_code=401)
    png, bounds = render.suitability_png(species)
    if png is None:
        return Response(status_code=404)
    return Response(png, media_type="image/png", headers={"X-Bounds": json.dumps(bounds)})


@app.get("/api/suitability/{species}/grid")
def suitability_grid(request: Request, species: str):
    if not _guard(request):
        return Response(status_code=401)
    grid = render.suitability_grid(species)
    if grid is None:
        return Response(status_code=404)
    return JSONResponse(grid)


@app.get("/api/suitability/{species}/bounds")
def suitability_bounds(request: Request, species: str):
    if not _guard(request):
        return Response(status_code=401)
    _, bounds = render.suitability_png(species)
    return JSONResponse(bounds) if bounds else Response(status_code=404)


@app.get("/api/pronte/{species}")
def pronte(request: Request, species: str):
    if not _guard(request):
        return Response(status_code=401)
    f = MAPS_DIR / f"pronte_oggi_{species}.geojson"
    if not f.exists():
        return JSONResponse({"type": "FeatureCollection", "features": []})
    return JSONResponse(json.loads(f.read_text(encoding="utf-8")))


@app.get("/api/top/{species}")
def top_spots_api(request: Request, species: str, mode: str = "both"):
    if not _guard(request):
        return Response(status_code=401)
    if mode not in ("static", "dynamic", "both"):
        mode = "both"
    from gis.predict_today import top_spots
    feats = [{"type": "Feature",
              "properties": {"score": s["score"], "idoneita": s["idoneita"], "readiness": s["readiness"]},
              "geometry": {"type": "Point", "coordinates": [s["lon"], s["lat"]]}}
             for s in top_spots(species, mode)]
    return JSONResponse({"type": "FeatureCollection", "features": feats, "mode": mode})


@app.get("/api/pins")
def pins(request: Request):
    if not _guard(request):
        return Response(status_code=401)
    feats = []
    for o in obsdb.all_observations():
        feats.append({"type": "Feature",
                      "properties": {"id": o["id"], "species": o.get("species"),
                                     "phase": o.get("phase"), "weight_g": o.get("weight_g"),
                                     "abundance": o.get("abundance"), "is_blank": o.get("is_blank"),
                                     "ts": o.get("ts_submit"), "photo": bool(o.get("photo_file_id"))},
                      "geometry": {"type": "Point", "coordinates": [o["lon"], o["lat"]]}})
    return JSONResponse({"type": "FeatureCollection", "features": feats})


@app.get("/photo/{obs_id}")
def photo(request: Request, obs_id: int):
    if not _guard(request):
        return Response(status_code=401)
    PHOTO_CACHE.mkdir(parents=True, exist_ok=True)
    cached = PHOTO_CACHE / f"{obs_id}.jpg"
    if not cached.exists():
        token = os.environ.get("MAPPA_FUNGHI_BOT_TOKEN")
        rows = [o for o in obsdb.all_observations() if o["id"] == obs_id]
        if not token or not rows or not rows[0].get("photo_file_id"):
            return Response(status_code=404)
        fid = rows[0]["photo_file_id"]
        meta = json.loads(urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/getFile?file_id={fid}", timeout=20).read())
        path = meta["result"]["file_path"]
        urllib.request.urlretrieve(f"https://api.telegram.org/file/bot{token}/{path}", cached)
    return Response(cached.read_bytes(), media_type="image/jpeg")
