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
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
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
AOI_GEOJSON = Path(__file__).resolve().parent.parent / "data" / "aoi" / "aoi.geojson"

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
    auth.migrate()           # colonne aggiunte dopo la prima release (casa)
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
    prof = auth.get_user(u["username"]) or {}
    return templates.TemplateResponse(request, "map.html",
                                      {"user": u, "species": _species_list(),
                                       "home_set": prof.get("home_lat") is not None})


# --- log delle uscite ------------------------------------------------------ #
# La cattura era un bot Telegram. Il pin trascinabile su mappa batte la posizione nativa
# nel caso che conta davvero — la sera, a casa, quando il posto lo ricordi ma non ci sei
# più — e chi logga non ha bisogno di Telegram. Resta un vincolo: senza campo non si
# salva, quindi si scrive dopo, e per questo il GIORNO è un campo e non l'ora di invio.
KINDS = {"found", "blank", "target"}
PHASES = {"primordi", "buono", "vecchio"}
OLD_REASONS = {"senescente", "abortito"}
ABUNDANCE = {"pochi", "medi", "tanti"}
EFFORT_MIN = {15, 60, 150, 240}


def _species_list():
    return [{"id": p.id, "common": p.common_name, "scientific": p.scientific_name}
            for p in sorted(REG.values(), key=lambda p: p.common_name)]


def _log_page(request: Request, u: dict, prefill: dict, saved=None, error=None, edit_id=None):
    return templates.TemplateResponse(request, "log.html", {
        "user": u, "species": _species_list(), "prefill": prefill, "saved": saved,
        "error": error, "edit_id": edit_id, "today": date.today().isoformat()})


def _obs_from_form(kind, obs_date, lat, lon, species, phase, old_reason,
                   abundance, weight_g, effort_min) -> tuple[dict | None, str | None]:
    """Valida i campi del form → (osservazione, errore). Condivisa da /log e dalla modifica.

    I campi non pertinenti al tipo di uscita vengono messi a None esplicitamente, non
    lasciati stare: cambiando un ritrovamento in un vuoto, la fase di prima resterebbe
    attaccata e il learner leggerebbe un vuoto "in fase buona".
    """
    if kind not in KINDS:
        return None, "Tipo di uscita non valido."
    try:
        d = date.fromisoformat(obs_date)
    except ValueError:
        return None, "Data non valida."
    if d > date.today():
        return None, "La data è nel futuro."
    if kind != "blank" and species not in REG:
        return None, "Specie non riconosciuta."

    obs = {"obs_date": d.isoformat(), "lat": lat, "lon": lon,
           "is_blank": 0 if kind == "found" else 1,
           "species": None, "target_species": None, "phase": None, "old_reason": None,
           "abundance": None, "weight_g": None, "effort_min": None}
    if kind == "found":
        obs["species"] = species
        obs["phase"] = phase if phase in PHASES else None
        if obs["phase"] == "vecchio" and old_reason in OLD_REASONS:
            obs["old_reason"] = old_reason
        obs["abundance"] = abundance if abundance in ABUNDANCE else None
        if weight_g.strip():
            try:
                obs["weight_g"] = float(weight_g.replace(",", "."))
            except ValueError:
                return None, "Peso non valido."
    else:
        if kind == "target":
            obs["target_species"] = species
        try:
            e = int(effort_min)
        except ValueError:
            e = 0
        obs["effort_min"] = e if e in EFFORT_MIN else None
    return obs, None


@app.get("/log", response_class=HTMLResponse)
def log_form(request: Request, again: int | None = None):
    u = _user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    prefill = {}
    if again:      # "altra specie, stesso punto": si riparte da posizione e giorno
        rows = [o for o in obsdb.all_observations() if o["id"] == again]
        if rows:
            prefill = {k: rows[0].get(k) for k in ("lat", "lon", "obs_date")}
    return _log_page(request, u, prefill)


@app.post("/log", response_class=HTMLResponse)
async def log_save(request: Request,
                   kind: str = Form(...), obs_date: str = Form(...),
                   lat: float = Form(...), lon: float = Form(...),
                   species: str = Form(""), phase: str = Form(""),
                   old_reason: str = Form(""), abundance: str = Form(""),
                   weight_g: str = Form(""), effort_min: str = Form(""),
                   photo: UploadFile | None = File(None)):
    u = _user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    obs, err = _obs_from_form(kind, obs_date, lat, lon, species, phase, old_reason,
                              abundance, weight_g, effort_min)
    if err:
        return _log_page(request, u, {"lat": lat, "lon": lon, "obs_date": obs_date}, error=err)
    obs.update({"ts_submit": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "logged_by": u["username"], "id_verified": 1})
    obs_id = obsdb.insert_observation(obs)
    await _save_photo(obs_id, photo)
    return _log_page(request, u, {}, saved=obs_id)


async def _save_photo(obs_id: int, photo) -> None:
    """Le foto vanno in data/photos/<id>.jpg, che è dove /photo/<id> già cercava la cache
    dei file scaricati da Telegram: nessun secondo percorso da mantenere."""
    if photo is None or not photo.filename:
        return
    PHOTO_CACHE.mkdir(parents=True, exist_ok=True)
    (PHOTO_CACHE / f"{obs_id}.jpg").write_bytes(await photo.read())


# --- scheda utente --------------------------------------------------------- #
# Storico modificabile perché un'osservazione sbagliata è peggio di una mancante: il
# learner la prende per buona. I ritrovamenti sono condivisi (decisione A, gruppo di
# fidati), quindi la lista li mostra tutti con l'autore, e chiunque può correggerli.
@app.get("/me", response_class=HTMLResponse)
def me_page(request: Request, msg: str | None = None, err: str | None = None):
    u = _user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    prof = auth.get_user(u["username"]) or {}
    obs = obsdb.all_observations()
    for o in obs:
        o["label"] = _obs_label(o)
    return templates.TemplateResponse(request, "me.html", {
        "user": u, "profile": prof, "observations": obs, "msg": msg, "err": err})


def _obs_label(o: dict) -> str:
    sid = o.get("species") or o.get("target_species")
    name = REG[sid].common_name if sid in REG else (sid or "—")
    if not o.get("is_blank"):
        return f"🍄 {name}"
    return f"🎯 vuoto mirato ({name})" if o.get("target_species") else "🚫 vuoto"


@app.post("/me/password")
def me_password(request: Request, current: str = Form(...), new1: str = Form(...),
                new2: str = Form(...)):
    u = _user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    if not auth.verify(u["username"], current):
        return RedirectResponse("/me?err=Password+attuale+errata", status_code=303)
    if len(new1) < 8:
        return RedirectResponse("/me?err=La+nuova+password+e+troppo+corta+(min+8)", status_code=303)
    if new1 != new2:
        return RedirectResponse("/me?err=Le+due+password+non+coincidono", status_code=303)
    auth.set_password(u["username"], new1)
    auth.close_other_sessions(u["username"], request.cookies.get("pilze_session"))
    return RedirectResponse("/me?msg=Password+aggiornata+(le+altre+sessioni+sono+state+chiuse)",
                            status_code=303)


@app.post("/me/home")
def me_home(request: Request, home_lat: str = Form(""), home_lon: str = Form("")):
    u = _user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    if not home_lat.strip() or not home_lon.strip():
        auth.set_home(u["username"], None, None)
        return RedirectResponse("/me?msg=Casa+rimossa", status_code=303)
    try:
        lat, lon = float(home_lat), float(home_lon)
    except ValueError:
        return RedirectResponse("/me?err=Coordinate+di+casa+non+valide", status_code=303)
    auth.set_home(u["username"], lat, lon)
    return RedirectResponse("/me?msg=Casa+salvata", status_code=303)


@app.get("/me/obs/{obs_id}", response_class=HTMLResponse)
def obs_edit_form(request: Request, obs_id: int):
    u = _user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    o = obsdb.get_observation(obs_id)
    if not o:
        return RedirectResponse("/me?err=Osservazione+non+trovata", status_code=303)
    return _log_page(request, u, o, edit_id=obs_id)


@app.post("/me/obs/{obs_id}", response_class=HTMLResponse)
async def obs_edit_save(request: Request, obs_id: int,
                        kind: str = Form(...), obs_date: str = Form(...),
                        lat: float = Form(...), lon: float = Form(...),
                        species: str = Form(""), phase: str = Form(""),
                        old_reason: str = Form(""), abundance: str = Form(""),
                        weight_g: str = Form(""), effort_min: str = Form(""),
                        photo: UploadFile | None = File(None)):
    u = _user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    old = obsdb.get_observation(obs_id)
    if not old:
        return RedirectResponse("/me?err=Osservazione+non+trovata", status_code=303)
    obs, err = _obs_from_form(kind, obs_date, lat, lon, species, phase, old_reason,
                              abundance, weight_g, effort_min)
    if err:
        return _log_page(request, u, {**old, "lat": lat, "lon": lon, "obs_date": obs_date},
                         error=err, edit_id=obs_id)
    if (lat, lon) != (old["lat"], old["lon"]):
        # i cell_id li riassegna il poller: lasciarli vecchi legherebbe l'osservazione
        # alla cella sbagliata, che è il modo peggiore di sbagliare
        obs["static_cell_id"] = obs["meteo_cell_id"] = None
    obsdb.update_observation(obs_id, obs)
    await _save_photo(obs_id, photo)
    return RedirectResponse(f"/me?msg=Osservazione+%23{obs_id}+aggiornata", status_code=303)


@app.post("/me/obs/{obs_id}/delete")
def obs_delete(request: Request, obs_id: int):
    u = _user(request)
    if not u:
        return RedirectResponse("/login", status_code=303)
    obsdb.delete_observation(obs_id)
    (PHOTO_CACHE / f"{obs_id}.jpg").unlink(missing_ok=True)
    return RedirectResponse(f"/me?msg=Osservazione+%23{obs_id}+eliminata", status_code=303)


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


@app.get("/api/aoi")
def aoi(request: Request):
    """Confini dell'area con dati tematici (BZ + TN + VE) — il limite delle mappe."""
    if not _guard(request):
        return Response(status_code=401)
    if not AOI_GEOJSON.exists():
        return JSONResponse({"type": "FeatureCollection", "features": []})
    return JSONResponse(json.loads(AOI_GEOJSON.read_text(encoding="utf-8")))


@app.get("/api/pronte/{species}")
def pronte(request: Request, species: str):
    if not _guard(request):
        return Response(status_code=401)
    f = MAPS_DIR / f"pronte_oggi_{species}.geojson"
    if not f.exists():
        return JSONResponse({"type": "FeatureCollection", "features": []})
    return JSONResponse(json.loads(f.read_text(encoding="utf-8")))


@app.get("/api/top/{species}")
def top_spots_api(request: Request, species: str, mode: str = "both",
                  near: int = 0, k: int = 50, km: float = 50.0):
    """Migliori spot per una specie. Con `near` la ricerca parte da casa dell'utente e si
    ferma al raggio: il punto di casa sta sull'account, quindi non lo manda il client."""
    if not _guard(request):
        return Response(status_code=401)
    if mode not in ("static", "dynamic", "both"):
        mode = "both"
    from gis.predict_today import top_spots

    home = max_km = None
    if near:
        u = auth.get_user(_user(request)["username"]) or {}
        if u.get("home_lat") is None or u.get("home_lon") is None:
            return JSONResponse({"type": "FeatureCollection", "features": [],
                                 "mode": mode, "error": "home_missing"})
        home = (u["home_lat"], u["home_lon"])
        max_km = min(max(float(km), 0.0), 100.0)
        k = min(max(int(k), 0), 20)
    feats = [{"type": "Feature",
              "properties": {kk: s[kk] for kk in ("score", "idoneita", "readiness", "dist_km")
                             if kk in s},
              "geometry": {"type": "Point", "coordinates": [s["lon"], s["lat"]]}}
             for s in top_spots(species, mode, k=k, home=home, max_km=max_km)]
    return JSONResponse({"type": "FeatureCollection", "features": feats, "mode": mode,
                         "near": bool(near)})


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
                                     # il giorno dell'uscita, non quello dell'invio: il pin
                                     # deve dire quando eri lì (fallback sui vecchi record)
                                     "ts": o.get("obs_date") or (o.get("ts_submit") or "")[:10],
                                     "target": o.get("target_species"),
                                     "effort_min": o.get("effort_min"),
                                     "photo": bool(o.get("photo_file_id"))},
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
