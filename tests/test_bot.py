"""Test della cattura: parsing della data, migrazione dello schema, giro completo sul DB."""

from datetime import date

import pytest


def test_parse_date_text_formati():
    pytest.importorskip("telegram")
    from bot.bot import parse_date_text
    oggi = date(2026, 9, 6)
    assert parse_date_text("3/9", oggi) == date(2026, 9, 3)
    assert parse_date_text("03/09/2026", oggi) == date(2026, 9, 3)
    assert parse_date_text("2026-09-03", oggi) == date(2026, 9, 3)
    assert parse_date_text("3-9", oggi) == date(2026, 9, 3)


def test_parse_date_text_senza_anno_non_salta_nel_futuro():
    """'28/12' scritto a gennaio è dicembre scorso, non fra undici mesi."""
    pytest.importorskip("telegram")
    from bot.bot import parse_date_text
    assert parse_date_text("28/12", date(2027, 1, 5)) == date(2026, 12, 28)


def test_parse_date_text_rifiuta_futuro_e_spazzatura():
    pytest.importorskip("telegram")
    from bot.bot import parse_date_text
    oggi = date(2026, 9, 6)
    assert parse_date_text("7/9", oggi) is None            # domani
    assert parse_date_text("ieri", oggi) is None
    assert parse_date_text("", oggi) is None
    assert parse_date_text("32/13", oggi) is None


def test_migrate_aggiunge_obs_date_a_un_db_vecchio(tmp_path):
    """Il DB di produzione esiste già: CREATE TABLE IF NOT EXISTS non porta le colonne nuove."""
    import sqlite3

    from bot import db
    p = tmp_path / "old.db"
    sqlite3.connect(p).executescript(
        "CREATE TABLE observations (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "ts_submit TEXT NOT NULL, lat REAL, lon REAL, is_blank INTEGER NOT NULL DEFAULT 0)")
    assert db.migrate(p) == ["obs_date"]
    assert db.migrate(p) == []                              # idempotente
    cols = {r["name"] for r in db.connect(p).execute("PRAGMA table_info(observations)")}
    assert "obs_date" in cols


def test_insert_e_rilettura_con_obs_date(tmp_path):
    from bot import db
    p = tmp_path / "obs.db"
    db.init_db(p)
    oid = db.insert_observation({"ts_submit": "2026-09-07T21:00:00+00:00",
                                 "obs_date": "2026-09-06", "lat": 46.13515, "lon": 11.68636,
                                 "species": "boletus_edulis", "is_blank": 0,
                                 "phase": "buono", "abundance": "tanti"}, p)
    rows = db.all_observations(p)
    assert len(rows) == 1 and rows[0]["id"] == oid
    # il giorno dell'uscita è distinto dall'invio: è quello che legge il learner
    assert rows[0]["obs_date"] == "2026-09-06"
    assert rows[0]["ts_submit"].startswith("2026-09-07")


# --------------------------------------------------------------------------- #
# Giro completo del flusso, senza Telegram: oggetti finti al posto di Update/Context.
# Serve perché il bot è fatto di 18 stati cablati a mano, e un errore di cablaggio
# altrimenti si scopre solo in chat.

class _Msg:
    def __init__(self, text=None, location=None, photo=(), replies=None):
        self.text, self.location, self.photo = text, location, photo
        self.replies = replies if replies is not None else []

    async def reply_text(self, text, **kw):
        self.replies.append(text)
        return _Msg(replies=self.replies)


class _Query:
    def __init__(self, data, replies):
        self.data, self.message = data, _Msg(replies=replies)
        self.edits = []

    async def answer(self, *a, **k):
        pass

    async def edit_message_text(self, text, **kw):
        self.edits.append(text)


class _Update:
    def __init__(self, message=None, query=None, chat_type="private"):
        self.message, self.callback_query = message, query
        self.effective_user = type("U", (), {"id": 42})()
        self.effective_chat = type("C", (), {"type": chat_type})()


class _Ctx:
    def __init__(self):
        self.user_data = {}


class _Loc:
    latitude, longitude = 46.13515, 11.68636


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def test_flusso_trovato_completo(monkeypatch):
    pytest.importorskip("telegram")
    import bot.bot as b
    saved = {}
    monkeypatch.setattr(b.db, "insert_observation", lambda obs: saved.update(obs) or 1)
    ctx, replies = _Ctx(), []

    assert _run(b.trovato(_Update(_Msg(replies=replies)), ctx)) == b.F_SPECIES
    assert _run(b.f_species(_Update(query=_Query("f:boletus_edulis", replies)), ctx)) == b.F_LOCATION
    assert _run(b.f_location(_Update(_Msg(location=_Loc(), replies=replies)), ctx)) == b.F_DATE
    assert _run(b.f_date(_Update(query=_Query("fd:1", replies)), ctx)) == b.F_PHASE      # ieri
    assert _run(b.f_phase(_Update(query=_Query("ph:buono", replies)), ctx)) == b.F_ABUNDANCE
    assert _run(b.f_abundance(_Update(query=_Query("ab:tanti", replies)), ctx)) == b.F_WEIGHT
    assert _run(b.f_weight(_Update(query=_Query("wt:skip", replies)), ctx)) == b.F_PHOTO
    _run(b.f_photo(_Update(_Msg(text="/fine", replies=replies)), ctx))

    from datetime import timedelta
    assert saved["species"] == "boletus_edulis"
    assert saved["phase"] == "buono" and saved["abundance"] == "tanti"
    assert saved["lat"] == 46.13515
    assert saved["obs_date"] == (b._today() - timedelta(days=1)).isoformat()
    assert ctx.user_data["last"]["lat"] == 46.13515        # pronto per "altra specie"


def test_vecchio_chiede_il_perche(monkeypatch):
    """La fase 'vecchio' apre la domanda su senescente/abortito: informa lag e moisture."""
    pytest.importorskip("telegram")
    import bot.bot as b
    ctx, replies = _Ctx(), []
    _run(b.trovato(_Update(_Msg(replies=replies)), ctx))
    _run(b.f_species(_Update(query=_Query("f:boletus_edulis", replies)), ctx))
    _run(b.f_location(_Update(_Msg(location=_Loc(), replies=replies)), ctx))
    _run(b.f_date(_Update(query=_Query("fd:0", replies)), ctx))
    assert _run(b.f_phase(_Update(query=_Query("ph:vecchio", replies)), ctx)) == b.F_OLDREASON
    assert _run(b.f_oldreason(_Update(query=_Query("or:abortito", replies)), ctx)) == b.F_ABUNDANCE
    assert ctx.user_data["obs"]["old_reason"] == "abortito"


def test_data_libera_rifiutata_e_poi_accettata():
    pytest.importorskip("telegram")
    import bot.bot as b
    ctx, replies = _Ctx(), []
    _run(b.trovato(_Update(_Msg(replies=replies)), ctx))
    _run(b.f_species(_Update(query=_Query("f:boletus_edulis", replies)), ctx))
    _run(b.f_location(_Update(_Msg(location=_Loc(), replies=replies)), ctx))
    assert _run(b.f_date(_Update(query=_Query("fd:altra", replies)), ctx)) == b.F_DATETEXT
    assert _run(b.f_datetext(_Update(_Msg(text="banana", replies=replies)), ctx)) == b.F_DATETEXT
    assert _run(b.f_datetext(_Update(_Msg(text="1/9", replies=replies)), ctx)) == b.F_PHASE
    assert ctx.user_data["obs"]["obs_date"].endswith("-09-01")


def test_in_gruppo_si_ferma_subito_e_lo_dice():
    """Il bottone posizione non esiste nei gruppi: meglio dirlo al passo 1 che schiantarsi al 2."""
    pytest.importorskip("telegram")
    import bot.bot as b
    from telegram.ext import ConversationHandler
    ctx, replies = _Ctx(), []
    upd = _Update(_Msg(replies=replies), chat_type="group")
    assert _run(b.trovato(upd, ctx)) == ConversationHandler.END
    assert "chat privata" in replies[0]
    assert "obs" not in ctx.user_data


def test_bottone_di_un_flusso_altrui_non_contamina():
    """Trovato lasciato a metà + Vuoto avviato: il vecchio bottone specie non scrive nel vuoto."""
    pytest.importorskip("telegram")
    import bot.bot as b
    ctx, replies = _Ctx(), []
    _run(b.trovato(_Update(_Msg(replies=replies)), ctx))
    _run(b.vuoto(_Update(_Msg(replies=replies)), ctx))            # cambio idea
    with pytest.raises(b.FlowLost):
        _run(b.f_species(_Update(query=_Query("f:boletus_edulis", replies)), ctx))
    assert ctx.user_data["obs"]["is_blank"] == 1
    assert "species" not in ctx.user_data["obs"]
