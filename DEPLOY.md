# Deploy Pilze su borant

Stack: Docker Compose (web + bot + poller) dietro Caddy (HTTPS), pattern borant.

## Prerequisiti
- Docker + Docker Compose sul VPS.
- Una porta host libera dal **registro porte borant**: qui **8790** (cambiala in `docker-compose.yml` se occupata).
- Caddy come reverse proxy.

## Cosa viaggia e cosa no
- **Nel repo (git):** codice + `profiles/` + `config/` + **le mappe statiche** `data/maps/idoneita_*.tif` (piccole).
- **NON nel repo (restano locali/da rigenerare):** layer grezzi (DEM, forestale, geologia, WorldCover, canopy) — servono solo a *generare* le mappe, non a runtime.
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
- `web` → `127.0.0.1:8790` (Caddy proxy). `bot` → cattura Telegram (live). `poller` → fetch meteo + "pronte oggi" ogni 24 h (l'archivio ICON-D2 cresce in avanti, §9).
- L'admin iniziale è creato da `PILZE_ADMIN_USER/PASS` al primo avvio; poi crea gli altri account (fidati) da `/admin`.

## Caddy
```
pilze.borant.eu {
    reverse_proxy 127.0.0.1:8790
}
```

## Aggiornare le mappe statiche
Si rigenerano **in locale** (dove ci sono i layer grezzi):
```bash
python -m gis.make_map            # tutte le specie
git add data/maps/idoneita_*.tif && git commit -m "maps: rigenerate" && git push
```
poi sul VPS: `git pull && docker compose restart web poller`.

## Note
- Cold-start meteo: le somme di pioggia mobili si riempiono dopo ~2–4 settimane di poller; l'umidità del suolo dà segnale dal giorno 1.
- Bot **live subito** = inizia a raccogliere ground-truth (stagione). I ritrovamenti sono **condivisi** tra gli account.
