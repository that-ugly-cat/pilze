# Deploy Pilze su borant

Stack: Docker Compose (web + bot + poller) dietro Caddy (HTTPS), pattern borant.

## Prerequisiti
- Docker + Docker Compose sul VPS.
- Una porta host libera dal **registro porte borant**: qui **8790** (cambiala in `docker-compose.yml` se occupata).
- Caddy come reverse proxy.

## Cosa viaggia e cosa no
- **Nel repo (git):** codice + `profiles/` + `config/` + **l'AOI** `data/aoi/` (confini BZ+TN+VE, ~300 KB: serve sia alla maschera dei provider sia al layer confini della web app).
- **NON nel repo:** i layer grezzi (DEM, forestale, geologia, WorldCover, canopy), che servono solo a *generare* le mappe, e **le mappe statiche stesse** `data/maps/idoneita_*.tif`. Erano versionate finché pesavano 600 KB l'una; a passo 200 m sono 5 MB × 9 specie, cioè ~45 MB di binari a ogni rigenerazione. Ora si generano **sul VPS** col bottone «Rigenera» dell'admin, e vivono nel volume.
- **Volumi persistenti sul VPS** (`./data`): DB osservazioni/meteo/utenti + foto + il `pronte_oggi_*.geojson` ricalcolato dal poller.

## Passi
```bash
# 1. clona in /opt/apps/pilze
cd /opt/apps && git clone https://github.com/that-ugly-cat/pilze.git pilze && cd pilze

# 2. segreti
cat > .env <<'EOF'
MAPPA_FUNGHI_BOT_TOKEN=<token @BotFather>
PILZE_ADMIN_USER=spit
PILZE_ADMIN_PASS=<password admin iniziale>
EOF

# 3. su
docker compose up -d --build
```
- `web` → `127.0.0.1:8790` (Caddy proxy; la cattura è il form `/log`). `poller` → fetch meteo + "pronte oggi" ogni 24 h (l'archivio ICON-D2 cresce in avanti, §9).
- L'admin iniziale è creato da `PILZE_ADMIN_USER/PASS` al primo avvio; poi crea gli altri account (fidati) da `/admin`.

## Caddy
```
pilze.borant.eu {
    reverse_proxy 127.0.0.1:8790
}
```

## Aggiornare le mappe statiche
Dall'admin della web app, bottone **Rigenera** (gira `gis.make_map` nel container, un core,
GDAL/BLAS a 1 thread). Dura un paio d'ore sull'intera griglia: le mappe vecchie restano
servite finché non finisce. In locale `python -m gis.make_map` fa lo stesso, ma il
risultato non si spinge più via git — vedi «Cosa viaggia e cosa no».

Serve rigenerare dopo: un cambio di profilo che tocca `static_envelope` o `host_genera`,
un layer nuovo, o un cambio dell'AOI.

**Un passo in più dopo `fetch_dem`.** Il drenaggio viene da un raster precalcolato dal DEM:

```bash
docker compose exec web python -m gis.make_tpi     # 12 tile, qualche minuto, una volta sola
```

Scrive `data/dem_tpi/` (~155 MB, nel volume come gli altri layer) e va rilanciato solo se
cambia il DEM. Se la cartella non c'è il sistema funziona lo stesso: il `drainage` torna
«non misurato» e il fattore resta neutro, esattamente come prima del 7 set 2026.

## Note
- Cold-start meteo: le somme di pioggia mobili si riempiono dopo ~2–4 settimane di poller; l'umidità del suolo dà segnale dal giorno 1.
- La cattura è il form `/log` della web app: i ritrovamenti sono **condivisi** fra gli account.
  `MAPPA_FUNGHI_BOT_TOKEN` resta in `.env` perché serve a servire le foto dei vecchi
  record catturati via Telegram (e servirà alle notifiche).
- **Aggiornamento su un VPS che ha già le mappe rigenerate in loco.** Da quando le mappe
  non sono più tracciate, un `git pull` non le tocca. La prima volta però il repo locale le
  ha ancora come file tracciati e modificati, quindi il pull si rifiuta: mettere le mappe
  vive al sicuro fuori dal repo, riportare `data/maps` allo stato del git, `git pull`,
  rimettere le mappe al loro posto, `docker compose up -d --build`.
- I profili **vivi** stanno nel volume (`data/profiles/`) e si modificano dall'editor
  online: un `git pull` che cambia `profiles/` NON li aggiorna (il seed avviene solo se la
  cartella è vuota). Le tarature vanno incollate nell'editor.
