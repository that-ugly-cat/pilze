"""La documentazione che mostra profili deve mostrare profili VALIDI.

Il validatore dei profili cresce (la coerenza finestra/lag è del 7 set 2026, `host_floor`
pure), e ogni regola nuova può rendere invalido un esempio scritto mesi prima. Un esempio
che l'editor rifiuta è peggio di nessun esempio: chi lo copia riceve un errore e non sa se
ha sbagliato lui. Questi test tengono allineati i due posti dove il codice mostra un
profilo — la pagina Doc e lo scheletro del bottone "nuovo profilo".
"""

import re
from pathlib import Path

import pytest

from engine.profiles import parse_profile_text

TEMPLATES = Path(__file__).resolve().parent.parent / "webapp" / "templates"


def _unescape(s: str) -> str:
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def _esempi_doc():
    html = (TEMPLATES / "docs.html").read_text(encoding="utf-8")
    return [_unescape(b) for b in re.findall(r"<pre>(species:.*?)</pre>", html, re.S)]


def test_la_pagina_doc_mostra_almeno_due_esempi():
    esempi = _esempi_doc()
    assert len(esempi) >= 2, "servono almeno una micorrizica di bosco e un saprotrofo"


@pytest.mark.parametrize("i", range(2))
def test_esempi_della_pagina_doc_sono_validi(i):
    profilo = parse_profile_text(_esempi_doc()[i])
    assert profilo.validate() == [], profilo.validate()


def test_scheletro_del_nuovo_profilo_e_valido():
    """Il template del bottone «nuovo» deve passare il POST che lo salva."""
    js = (TEMPLATES / "profiles.html").read_text(encoding="utf-8")
    m = re.search(r"const TEMPLATE = id => `(.*?)`;", js, re.S)
    assert m, "scheletro non trovato in profiles.html"
    testo = m.group(1).replace("${id}", "specie_di_prova")
    profilo = parse_profile_text(testo)
    assert profilo.id == "specie_di_prova"
    assert profilo.validate() == [], profilo.validate()
