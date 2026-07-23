"""Registro dei profili di specie (spec §7).

Il MOTORE è species-agnostic: legge i profili dichiarativi da profiles/*.yaml.
Aggiungere una specie = aggiungere un file YAML. Nessun codice del motore cambia
(salvo l'aggiunta di un layer statico nuovo via extra_static_layers, spec §7.3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

PROFILES_DIR = Path(__file__).resolve().parent.parent / "profiles"

VALID_TROPHIC = {"mycorrhizal", "saprotrophic", "facultative"}
CROSSWALK_CLASSES = {
    "querce", "castagno", "faggio", "abete", "pino",
    "altro_latifoglie", "altro_conifere", "non_forestale",
}


@dataclass
class SpeciesProfile:
    id: str
    common_name: str
    trophic_mode: str
    similar_to: list[str] = field(default_factory=list)
    host_genera: dict[str, float] = field(default_factory=dict)
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
        for g in self.host_genera:
            if g not in CROSSWALK_CLASSES:
                errs.append(f"{self.id}: host '{g}' non è una classe del crosswalk (§3.3)")
        for m in self.phenology_months:
            if not 1 <= m <= 12:
                errs.append(f"{self.id}: mese fenologia {m} fuori range")
        if self.is_mycorrhizal and not self.host_genera:
            errs.append(f"{self.id}: micorrizico senza host_genera")
        return errs


def _from_dict(d: dict) -> SpeciesProfile:
    s = d["species"] if "species" in d else d
    return SpeciesProfile(
        id=s["id"],
        common_name=s.get("common_name", s["id"]),
        trophic_mode=s.get("trophic_mode", "mycorrhizal"),
        similar_to=s.get("similar_to", []) or [],
        host_genera=s.get("host_genera", {}) or {},
        static_envelope=s.get("static_envelope", {}) or {},
        extra_static_layers=s.get("extra_static_layers", []) or [],
        phenology_months=s.get("phenology_months", []) or [],
        dynamic_triggers=s.get("dynamic_triggers", {}) or {},
    )


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
