"""Registro dei profili di specie (spec §7).

Il MOTORE è species-agnostic: legge i profili dichiarativi da profiles/*.yaml.
Aggiungere una specie = aggiungere un file YAML. Nessun codice del motore cambia
(salvo l'aggiunta di un layer statico nuovo via extra_static_layers, spec §7.3).
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Default versionati in git (baked nell'immagine Docker) = seed di fabbrica.
DEFAULT_PROFILES_DIR = Path(__file__).resolve().parent.parent / "profiles"
# Directory dei profili VIVI. Sul VPS è un volume persistente (PILZE_PROFILES_DIR):
# gli edit online sono la fonte di verità e non tornano in git. Altrove = i default.
PROFILES_DIR = Path(os.environ.get("PILZE_PROFILES_DIR") or DEFAULT_PROFILES_DIR)


def seed_profiles(src: Path | str = DEFAULT_PROFILES_DIR,
                  dst: Path | str = PROFILES_DIR) -> int:
    """Popola la dir viva coi default se è distinta da src ed è priva di profili.

    Evita il footgun "volume vuoto → nessun profilo" su un deploy pulito. Ritorna
    quanti file ha copiato (0 se dst == src o se già popolata).
    """
    src, dst = Path(src), Path(dst)
    if dst.resolve() == src.resolve():
        return 0
    dst.mkdir(parents=True, exist_ok=True)
    if any(dst.glob("*.yaml")):
        return 0
    n = 0
    for f in sorted(src.glob("*.yaml")):
        shutil.copy2(f, dst / f.name)
        n += 1
    return n

VALID_TROPHIC = {"mycorrhizal", "saprotrophic", "facultative"}
# Classi di copertura WorldCover usabili come gate. `habitat` è una di queste (peso 1)
# oppure un dizionario {classe: peso} per le specie di ecotono, che stanno sul confine
# fra due coperture e con una classe sola perderebbero metà dell'habitat.
VALID_HABITAT = {"forest", "grassland", "cropland", "shrubland", "built_up", "bare",
                 "moss_lichen", "wetland", "water", "snow_ice"}
# Classi host = le 20 categorie forestali CFI2020 (nomi leggibili). Devono restare
# allineate a target_classes in config/crosswalk.yaml.
CROSSWALK_CLASSES = {
    "pecceta", "pecceta_secondaria", "abetina", "larici_cembreto", "faggeta",
    "mugheta", "pineta_silvestre", "pineta_nera", "castagneto", "querceto",
    "querceto_rovere", "querco_carpineto", "orno_ostrieto", "lecceta",
    "acero_frassineto", "frassineto", "alneto", "altre_latifoglie",
    "latifoglie_sempreverdi", "formazione_marginale",
}


@dataclass
class SpeciesProfile:
    id: str
    common_name: str
    trophic_mode: str
    habitat: str | dict = "forest"   # classe di copertura, o {classe: peso} (WorldCover)
    similar_to: list[str] = field(default_factory=list)
    host_genera: dict[str, float] = field(default_factory=dict)
    host_floor: float = 0.0          # quanto vale l'ospite SBAGLIATO (0 = veto secco)
    static_envelope: dict = field(default_factory=dict)
    extra_static_layers: list[str] = field(default_factory=list)
    phenology_months: list[int] = field(default_factory=list)
    dynamic_triggers: dict = field(default_factory=dict)

    @property
    def is_mycorrhizal(self) -> bool:
        return self.trophic_mode == "mycorrhizal"

    @property
    def scientific_name(self) -> str:
        """Binomio scientifico dall'id (es. boletus_edulis → Boletus edulis)."""
        return self.id.replace("_", " ").capitalize()

    def validate(self) -> list[str]:
        """Ritorna la lista di problemi (vuota = ok). Non solleva: fail-soft."""
        errs: list[str] = []
        if self.trophic_mode not in VALID_TROPHIC:
            errs.append(f"{self.id}: trophic_mode '{self.trophic_mode}' non valido")
        if not isinstance(self.habitat, (str, dict)):
            errs.append(f"{self.id}: habitat dev'essere una classe o un dizionario {{classe: peso}}")
        else:
            weights = self.habitat if isinstance(self.habitat, dict) else {self.habitat: 1.0}
            for cls, w in weights.items():
                if cls not in VALID_HABITAT:
                    errs.append(f"{self.id}: habitat '{cls}' non è una classe di copertura "
                                f"({' | '.join(sorted(VALID_HABITAT))})")
                elif not isinstance(w, (int, float)) or not 0.0 <= float(w) <= 1.0:
                    errs.append(f"{self.id}: peso habitat '{cls}' = {w!r} non è un numero in [0,1]")
        for g in self.host_genera:
            if g not in CROSSWALK_CLASSES:
                errs.append(f"{self.id}: host '{g}' non è una classe del crosswalk (§3.3)")
        for m in self.phenology_months:
            if not 1 <= m <= 12:
                errs.append(f"{self.id}: mese fenologia {m} fuori range")
        if self.is_mycorrhizal and not self.host_genera:
            errs.append(f"{self.id}: micorrizico senza host_genera")
        if not 0.0 <= self.host_floor < 1.0:
            errs.append(f"{self.id}: host_floor {self.host_floor} fuori da [0,1)")
        if self.host_floor and self.host_floor >= min(self.host_genera.values(), default=1.0):
            errs.append(f"{self.id}: host_floor {self.host_floor} ≥ del peso host più basso: "
                        f"l'ospite sbagliato varrebbe quanto uno buono")

        # Coerenza fra finestra di pioggia e lag: la finestra deve contenere la pioggia
        # che ha innescato la buttata che si sta valutando. Se `rain_window_days` non
        # supera il lag massimo, una cella valutata a fine finestra vede una pioggia
        # cumulata da cui l'innesco è già uscito, e la specie risulta secca proprio nei
        # giorni in cui dovrebbe essere pronta. Serve stretto: la somma prende gli ultimi
        # `win` giorni, cioè da d-(win-1) a d, quindi un trigger a dst = win resta fuori.
        win = self.dynamic_triggers.get("rain_window_days")
        opt = (self.dynamic_triggers.get("lag_days") or {}).get("opt")
        if win and opt and len(opt) == 2 and win <= float(opt[1]):
            errs.append(f"{self.id}: rain_window_days ({win}) non supera lag_days.opt max "
                        f"({opt[1]}) — l'innesco esce dalla finestra proprio quando serve")
        return errs


def _as_float(v, default: float = 0.0) -> float:
    """Numero o default: il parsing di un profilo non deve morire su un campo scritto male."""
    try:
        return float(v or default)
    except (TypeError, ValueError):
        return default


def _from_dict(d: dict) -> SpeciesProfile:
    s = d["species"] if "species" in d else d
    return SpeciesProfile(
        id=s["id"],
        common_name=s.get("common_name", s["id"]),
        trophic_mode=s.get("trophic_mode", "mycorrhizal"),
        habitat=s.get("habitat", "forest"),
        similar_to=s.get("similar_to", []) or [],
        host_genera=s.get("host_genera", {}) or {},
        host_floor=_as_float(s.get("host_floor")),
        static_envelope=s.get("static_envelope", {}) or {},
        extra_static_layers=s.get("extra_static_layers", []) or [],
        phenology_months=s.get("phenology_months", []) or [],
        dynamic_triggers=s.get("dynamic_triggers", {}) or {},
    )


def parse_profile_text(text: str) -> SpeciesProfile:
    """Parsa il testo YAML di un profilo in SpeciesProfile. Solleva su YAML invalido."""
    data = yaml.safe_load(text)
    if not data:
        raise ValueError("YAML vuoto")
    return _from_dict(data)


def load_profiles(directory: Path | str = PROFILES_DIR) -> dict[str, SpeciesProfile]:
    """Carica tutti i profili YAML in un registro {id: SpeciesProfile}."""
    directory = Path(directory)
    registry: dict[str, SpeciesProfile] = {}
    for path in sorted(directory.glob("*.yaml")):
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if not data:
            continue
        profile = _from_dict(data)
        registry[profile.id] = profile
    return registry


def species_buttons(registry: dict[str, SpeciesProfile]) -> list[tuple[str, str]]:
    """(id, common_name) ordinati per nome — per i bottoni inline del bot (§7.2)."""
    return sorted(((p.id, p.common_name) for p in registry.values()), key=lambda t: t[1])


if __name__ == "__main__":  # smoke: elenca e valida i profili
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    reg = load_profiles()
    print(f"{len(reg)} profili caricati:")
    for pid, p in sorted(reg.items()):
        problems = p.validate()
        flag = "  OK" if not problems else "  ⚠ " + "; ".join(problems)
        print(f"  - {pid:24s} {p.common_name:14s} {p.trophic_mode}{flag}")
