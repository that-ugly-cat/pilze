"""Rigenerazione delle mappe statiche come job in background (admin).

Lancia `python -m gis.make_map` in un sottoprocesso a bassa priorità e a 1 thread
(GDAL/BLAS), con un lock che impedisce run sovrapposti. Lo stato è un file JSON +
un log su disco: l'endpoint li legge senza interagire col processo. Assunto un
singolo worker uvicorn (default del deploy) → il lock in-process basta.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAPS_DIR = ROOT / "data" / "maps"
STATE = MAPS_DIR / "_regen.json"
LOG = MAPS_DIR / "_regen.log"

_proc: subprocess.Popen | None = None
_lock = threading.Lock()


def running() -> bool:
    return _proc is not None and _proc.poll() is None


def _write_state(d: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(d), encoding="utf-8")


def tail(n: int = 30) -> str:
    if not LOG.exists():
        return ""
    lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-n:])


def status() -> dict:
    st = {"state": "idle"}
    if STATE.exists():
        try:
            st = json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            pass
    # Riconcilia uno stato "running" rimasto orfano dopo un riavvio del processo.
    if st.get("state") == "running" and not running():
        st = {**st, "state": "unknown"}
    st["running"] = running()
    st["tail"] = tail()
    return st


def start(species: list[str] | None = None, started_by: str = "") -> bool:
    """Avvia make_map in background. Ritorna False se un job è già in corso."""
    global _proc
    with _lock:
        if running():
            return False
        MAPS_DIR.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, PYTHONUNBUFFERED="1", GDAL_NUM_THREADS="1",
                   OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
        cmd = [sys.executable, "-m", "gis.make_map", *(species or [])]
        kw: dict = {}
        if hasattr(os, "nice"):                       # bassa priorità su POSIX (VPS Linux)
            kw["preexec_fn"] = lambda: os.nice(15)
        logf = open(LOG, "w", encoding="utf-8")
        _proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=logf,
                                 stderr=subprocess.STDOUT, **kw)
        _write_state({"state": "running", "pid": _proc.pid, "by": started_by,
                      "species": species or "tutte",
                      "started": time.strftime("%Y-%m-%d %H:%M:%S")})
        threading.Thread(target=_watch, args=(_proc, logf), daemon=True).start()
        return True


def _watch(proc: subprocess.Popen, logf) -> None:
    rc = proc.wait()
    logf.close()
    st = status()
    st.pop("tail", None); st.pop("running", None)
    st.update(state=("done" if rc == 0 else "error"), returncode=rc,
              ended=time.strftime("%Y-%m-%d %H:%M:%S"))
    _write_state(st)
