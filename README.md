# Mappa Funghi 🍄

Sistema per **mappare le aree produttive** per specie di funghi, **prevedere le buttate**
(idoneità statica dell'habitat × condizioni meteo dinamiche) e **migliorare nel tempo**
tramite i ritrovamenti sul campo. Ambito iniziale: 6 specie micorriziche, Veneto + Trentino.

Spec completa: `../ono-wiki/raw/strumenti/mappa-funghi-spec.md` (pagina wiki: `mappa-funghi.md`).

```
predizione(cella, specie, giorno) = idoneità_statica(cella, specie) × readiness_dinamica(meteo, specie)
```

Tutto è **per singola specie**. Aggiungere una specie = aggiungere un profilo YAML in
`profiles/` — il motore, il bot e (in futuro) il learner si adeguano da soli.

## Struttura

```
profiles/     6 profili di specie (YAML) — il cuore dichiarativo (spec §7.1)
config/       grid.yaml (griglia comune) · crosswalk.yaml (forestale VE↔TN)
engine/       MOTORE generico, species-agnostic:
                membership.py     funzioni sfumate (§7.5)
                profiles.py       registry loader
                static_scorer.py  idoneità habitat (host-gate × geomean)  [traccia A]
                dynamic_scorer.py readiness meteo (gate fenologia+umidità) [traccia B/v3]
                combiner.py       statica × readiness
bot/          bot Telegram di cattura + SQLite (spec §6.1)               [traccia B]
gis/          ingestione layer statici → mappa idoneità (STUB, vedi README) [traccia A]
tests/        test del motore su feature sintetiche
docs/         ROADMAP.md (v1→v4)
```

## Stato (kickoff 18 lug 2026)

Costruite in parallelo le due metà del v1:
- **Motore + profili + scoring**: completi e **testati** (feature sintetiche).
- **Bot di cattura**: **runnable** con un token — inizia ad accumulare ground-truth *subito*
  (siamo in stagione; i ritrovamenti non sono backfillabili, a differenza del meteo).
- **Traccia A GIS** (acquisizione layer reali) e **pipeline meteo v2**: stub documentati.

## Setup

```bash
# motore + test (solo pyyaml + pytest)
pip install -e ".[dev]"
python -m engine.profiles        # elenca e valida i 6 profili
pytest                           # test del motore

# bot di cattura
pip install -e ".[bot]"
cp .env.example .env             # inserisci MAPPA_FUNGHI_BOT_TOKEN (da @BotFather)
export MAPPA_FUNGHI_BOT_TOKEN=...
python -m bot.bot
```

## Note di design (dalla spec, da tenere presenti)

- **Niente ML all'avvio**: la mappa statica è MCE a **pesi esperti**; i ritrovamenti
  aggiornano priori in bayesiano online, non addestrano da zero.
- **Assi di apprendimento SEPARATI** (§6.2): non mescolare le feature statiche di un
  ritrovamento col meteo di quel giorno.
- **Trigger sulla temperatura del SUOLO**, non dell'aria (§5).
- **Layer disturbo Vaia/bostrico** (`canopy_alive`) declassa gli ospiti conifera morti —
  critico per *edulis*/*pinophilus* (§3.1).
- I `dynamic_triggers` dei profili (oltre *aereus*) sono **priori di prima passata da rivedere**.
