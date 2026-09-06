"""Bot Telegram di cattura (spec §6.1).

Registra ritrovamenti e uscite a vuoto — deve essere VELOCE quanto il ritrovamento.
Usa la LOCATION nativa di Telegram (l'EXIF delle foto è strippato → niente geotag).

Avvio:
    export MAPPA_FUNGHI_BOT_TOKEN=...        # token da @BotFather
    python -m bot.bot

Flussi:
    /trovato   ritrovamento (specie, posizione, giorno, fase, abbondanza, peso, foto)
    /vuoto     uscita a vuoto generica (posizione, giorno, effort)
    /mirato    uscita a vuoto mirata su una specie (zero forte sul timing)
    /annulla   annulla il flusso in corso

Due scelte che non sono di comodo:
- si chiede il GIORNO dell'uscita, non solo l'ora di invio. Il learner legge lo stato del
  meteo a quella data, e la finestra del lag è di dieci giorni: loggare la sera dopo
  sposterebbe `days_since_trigger` di un decimo della finestra.
- niente numeri da digitare: in montagna la digitazione è dove il flusso muore. Fasce a
  bottoni, con estremi dichiarati (16+ lo leggono tutti uguale, "molti" no).
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from engine.profiles import load_profiles, species_buttons
from . import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mappa-funghi.bot")

REGISTRY = load_profiles()

# stati conversazione
(F_SPECIES, F_LOCATION, F_DATE, F_DATETEXT, F_PHASE, F_OLDREASON, F_ABUNDANCE, F_WEIGHT,
 F_PHOTO, B_LOCATION, B_DATE, B_DATETEXT, B_EFFORT,
 T_SPECIES, T_LOCATION, T_DATE, T_DATETEXT, T_EFFORT) = range(18)

PHASES = [("primordi", "🌱 primordi (troppo presto)"), ("buono", "👌 giusti"),
          ("vecchio", "🍂 vecchi (troppo tardi)")]
# perché "vecchio": senescente = buttata semplicemente tardi (limite superiore del lag);
# abortito = condizioni girate (secco) → informa il moisture floor (spec §6.1).
OLD_REASONS = [("senescente", "🍂 senescente (tardi)"), ("abortito", "🌵 abortito (secco)")]
# Fasce con gli estremi scritti: "molti" varia fra persone e fra stagioni, "16+" no.
ABUNDANCE = [("pochi", "pochi (1–5)"), ("medi", "medi (6–15)"), ("tanti", "tanti (16+)")]
# effort in minuti: si salva il punto medio della fascia, la colonna resta numerica.
EFFORT = [(15, "meno di 30 min"), (60, "30–90 min"), (150, "1,5–3 h"), (240, "più di 3 h")]
# Un vuoto informa in proporzione a quanto hai cercato: dieci minuti e tre ore non sono
# lo stesso zero, e il learner deve poterli pesare diversamente.
_EFFORT_Q = "Quanto hai cercato?"
_CANCEL = filters.Regex(r"^/annulla")


def _species_keyboard(prefix: str) -> InlineKeyboardMarkup:
    rows, row = [], []
    for sid, name in species_buttons(REGISTRY):
        row.append(InlineKeyboardButton(name, callback_data=f"{prefix}:{sid}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def _choice_keyboard(prefix: str, options) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=f"{prefix}:{val}")] for val, label in options]
    )


_LOCATION_KB = ReplyKeyboardMarkup(
    [[KeyboardButton("📍 Invia posizione", request_location=True)]],
    resize_keyboard=True, one_time_keyboard=True,
)

# tastiera principale persistente (al posto degli slash)
BTN_TROVATO, BTN_VUOTO, BTN_MIRATO = "🍄 Trovato", "🚫 Vuoto", "🎯 Mirato"
MAIN_KB = ReplyKeyboardMarkup(
    [[KeyboardButton(BTN_TROVATO)], [KeyboardButton(BTN_VUOTO), KeyboardButton(BTN_MIRATO)]],
    resize_keyboard=True,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> date:
    """Giorno locale (Europe/Rome sul VPS via TZ), non UTC: alle 01:00 sono giorni diversi."""
    return datetime.now().date()


def _date_keyboard(prefix: str) -> InlineKeyboardMarkup:
    """Oggi / ieri / l'altro ieri, più una data libera. Il caso comune è un tap."""
    today = _today()
    opts = [(0, "oggi"), (1, "ieri"), (2, "l'altro ieri")]
    rows = [[InlineKeyboardButton(f"{label} ({(today - timedelta(days=d)).strftime('%d/%m')})",
                                  callback_data=f"{prefix}:{d}")] for d, label in opts]
    rows.append([InlineKeyboardButton("📅 altra data", callback_data=f"{prefix}:altra")])
    return InlineKeyboardMarkup(rows)


def parse_date_text(text: str, today: date | None = None) -> date | None:
    """'12/9', '12/09/2026' o '2026-09-12' → data. None se non si legge o è nel futuro."""
    today = today or _today()
    text = (text or "").strip()
    for fmt, needs_year in (("%d/%m/%Y", True), ("%d-%m-%Y", True), ("%Y-%m-%d", True),
                            ("%d/%m", False), ("%d-%m", False)):
        try:
            d = datetime.strptime(text, fmt).date()
        except ValueError:
            continue
        if not needs_year:
            d = d.replace(year=today.year)
            # "28/12" scritto a gennaio è dicembre scorso; "7/9" scritto il 6/9 è invece
            # un errore di battitura, e va rifiutato. Mezz'anno separa i due casi.
            if d > today and (d - today).days > 180:
                d = d.replace(year=today.year - 1)
        return None if d > today else d
    return None


# --------- data dell'osservazione (condivisa dai tre flussi) --------------- #
async def _handle_date_choice(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                              text_state: int) -> int | None:
    """Applica la scelta del bottone data. Ritorna lo stato per la data libera, o None
    se la data è fissata e il chiamante può proseguire."""
    q = update.callback_query
    await q.answer()
    choice = q.data.split(":", 1)[1]
    if choice == "altra":
        await q.edit_message_text("Che giorno? (es. 3/9 oppure 3/9/2026)")
        return text_state
    d = _today() - timedelta(days=int(choice))
    ctx.user_data["obs"]["obs_date"] = d.isoformat()
    await q.edit_message_text(f"Giorno: {d.strftime('%d/%m/%Y')}")
    return None


async def _handle_date_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    """True se la data scritta è valida (e salvata); False se va richiesta di nuovo."""
    d = parse_date_text(update.message.text)
    if d is None:
        await update.message.reply_text("Non ho capito la data (o è nel futuro). "
                                        "Scrivila come 3/9 oppure 3/9/2026.")
        return False
    ctx.user_data["obs"]["obs_date"] = d.isoformat()
    await update.message.reply_text(f"Giorno: {d.strftime('%d/%m/%Y')}")
    return True


# --------------------------------------------------------------------------- #
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🍄 *Pilze*\n\nUsa i bottoni qui sotto:\n"
        f"{BTN_TROVATO} — ho trovato qualcosa\n"
        f"{BTN_VUOTO} — uscita a vuoto\n"
        f"{BTN_MIRATO} — cercavo una specie e non c'era\n"
        "(/annulla per interrompere)",
        parse_mode="Markdown", reply_markup=MAIN_KB,
    )


# --------- /trovato -------------------------------------------------------- #
async def trovato(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["obs"] = {"ts_submit": _now_iso(), "user_id": update.effective_user.id,
                            "is_blank": 0, "id_verified": 1}
    await update.message.reply_text("Specie?", reply_markup=_species_keyboard("f"))
    return F_SPECIES


async def f_species(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    sid = q.data.split(":", 1)[1]
    obs = ctx.user_data["obs"]
    obs["species"] = sid
    name = REGISTRY[sid].common_name if sid in REGISTRY else sid
    await q.edit_message_text(f"Specie: {name}")
    if obs.get("lat") is not None:            # "altra specie, stesso posto": pin e data già presi
        await q.message.reply_text("Maturità:", reply_markup=_choice_keyboard("ph", PHASES))
        return F_PHASE
    await q.message.reply_text("Posizione? (usa il bottone)", reply_markup=_LOCATION_KB)
    return F_LOCATION


async def f_location(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    loc = update.message.location
    ctx.user_data["obs"]["lat"] = loc.latitude
    ctx.user_data["obs"]["lon"] = loc.longitude
    await update.message.reply_text("📍 Posizione ok.", reply_markup=ReplyKeyboardRemove())
    await update.message.reply_text("Che giorno eri lì?", reply_markup=_date_keyboard("fd"))
    return F_DATE


async def f_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    nxt = await _handle_date_choice(update, ctx, F_DATETEXT)
    if nxt is not None:
        return nxt
    await update.callback_query.message.reply_text(
        "Maturità:", reply_markup=_choice_keyboard("ph", PHASES))
    return F_PHASE


async def f_datetext(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not await _handle_date_text(update, ctx):
        return F_DATETEXT
    await update.message.reply_text("Maturità:", reply_markup=_choice_keyboard("ph", PHASES))
    return F_PHASE


async def f_phase(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    phase = q.data.split(":", 1)[1]
    ctx.user_data["obs"]["phase"] = phase
    await q.edit_message_text(f"Fase: {phase}")
    if phase == "vecchio":            # perché è vecchio → informa lag e moisture floor (§6.1)
        await q.message.reply_text("Perché vecchio?", reply_markup=_choice_keyboard("or", OLD_REASONS))
        return F_OLDREASON
    await q.message.reply_text("Abbondanza:", reply_markup=_choice_keyboard("ab", ABUNDANCE))
    return F_ABUNDANCE


async def f_oldreason(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    ctx.user_data["obs"]["old_reason"] = q.data.split(":", 1)[1]
    await q.edit_message_text(f"Vecchio: {ctx.user_data['obs']['old_reason']}")
    await q.message.reply_text("Abbondanza:", reply_markup=_choice_keyboard("ab", ABUNDANCE))
    return F_ABUNDANCE


async def f_abundance(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    ctx.user_data["obs"]["abundance"] = q.data.split(":", 1)[1]
    await q.edit_message_text(f"Abbondanza: {ctx.user_data['obs']['abundance']}")
    await q.message.reply_text(
        "Peso in grammi? Scrivilo se ti va, oppure salta — l'abbondanza basta.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏭ salta", callback_data="wt:skip")]]))
    return F_WEIGHT


async def f_weight(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if update.callback_query:                       # bottone "salta"
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("Peso: saltato")
        await update.callback_query.message.reply_text("Foto? (invia una foto, o /fine)")
        return F_PHOTO
    if update.message.text and update.message.text not in ("/skip", "/fine"):
        try:
            ctx.user_data["obs"]["weight_g"] = float(update.message.text.replace(",", "."))
        except ValueError:
            await update.message.reply_text("Numero non valido — riprova o tocca «salta»")
            return F_WEIGHT
    await update.message.reply_text("Foto? (invia una foto, o /fine)")
    return F_PHOTO


async def f_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.photo:
        ctx.user_data["obs"]["photo_file_id"] = update.message.photo[-1].file_id
    return await _save_and_end(update, ctx)


# --------- /vuoto ---------------------------------------------------------- #
async def vuoto(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["obs"] = {"ts_submit": _now_iso(), "user_id": update.effective_user.id,
                            "is_blank": 1, "id_verified": 1}
    await update.message.reply_text("Uscita a vuoto. Posizione?", reply_markup=_LOCATION_KB)
    return B_LOCATION


async def b_location(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    loc = update.message.location
    ctx.user_data["obs"].update({"lat": loc.latitude, "lon": loc.longitude})
    await update.message.reply_text("📍 Posizione ok.", reply_markup=ReplyKeyboardRemove())
    await update.message.reply_text("Che giorno eri lì?", reply_markup=_date_keyboard("bd"))
    return B_DATE


async def b_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    nxt = await _handle_date_choice(update, ctx, B_DATETEXT)
    if nxt is not None:
        return nxt
    await update.callback_query.message.reply_text(_EFFORT_Q, reply_markup=_choice_keyboard("ef", EFFORT))
    return B_EFFORT


async def b_datetext(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not await _handle_date_text(update, ctx):
        return B_DATETEXT
    await update.message.reply_text(_EFFORT_Q, reply_markup=_choice_keyboard("ef", EFFORT))
    return B_EFFORT


async def b_effort(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    ctx.user_data["obs"]["effort_min"] = int(q.data.split(":", 1)[1])
    await q.edit_message_text(f"Ricerca: {ctx.user_data['obs']['effort_min']} min circa")
    return await _save_and_end(update, ctx)


# --------- /mirato --------------------------------------------------------- #
async def mirato(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["obs"] = {"ts_submit": _now_iso(), "user_id": update.effective_user.id,
                            "is_blank": 1, "id_verified": 1}
    await update.message.reply_text("Che specie cercavi?", reply_markup=_species_keyboard("t"))
    return T_SPECIES


async def t_species(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    sid = q.data.split(":", 1)[1]
    ctx.user_data["obs"]["target_species"] = sid
    name = REGISTRY[sid].common_name if sid in REGISTRY else sid
    await q.edit_message_text(f"Cercavi: {name}")
    await q.message.reply_text("Posizione?", reply_markup=_LOCATION_KB)
    return T_LOCATION


async def t_location(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    loc = update.message.location
    ctx.user_data["obs"].update({"lat": loc.latitude, "lon": loc.longitude})
    await update.message.reply_text("📍 Posizione ok.", reply_markup=ReplyKeyboardRemove())
    await update.message.reply_text("Che giorno eri lì?", reply_markup=_date_keyboard("td"))
    return T_DATE


async def t_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    nxt = await _handle_date_choice(update, ctx, T_DATETEXT)
    if nxt is not None:
        return nxt
    await update.callback_query.message.reply_text(_EFFORT_Q, reply_markup=_choice_keyboard("ef", EFFORT))
    return T_EFFORT


async def t_datetext(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if not await _handle_date_text(update, ctx):
        return T_DATETEXT
    await update.message.reply_text(_EFFORT_Q, reply_markup=_choice_keyboard("ef", EFFORT))
    return T_EFFORT


async def t_effort(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    ctx.user_data["obs"]["effort_min"] = int(q.data.split(":", 1)[1])
    await q.edit_message_text(f"Ricerca: {ctx.user_data['obs']['effort_min']} min circa")
    return await _save_and_end(update, ctx)


# --------------------------------------------------------------------------- #
async def _save_and_end(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    obs = ctx.user_data.pop("obs")
    obs.setdefault("obs_date", _today().isoformat())      # rete: mai un'osservazione senza giorno
    obs_id = db.insert_observation(obs)
    # posizione e giorno restano a portata per "altra specie, stesso posto"
    ctx.user_data["last"] = {k: obs[k] for k in ("lat", "lon", "obs_date") if k in obs}
    msg = update.message or update.callback_query.message
    kb = None
    if not obs.get("is_blank") and obs.get("lat") is not None:
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("➕ altra specie, stesso posto", callback_data="again:1")]])
    await msg.reply_text(f"✅ Salvato (#{obs_id}). Grazie!", reply_markup=MAIN_KB)
    if kb:
        await msg.reply_text("Hai trovato altro nella stessa uscita?", reply_markup=kb)
    return ConversationHandler.END


async def ancora(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Nuovo ritrovamento riusando pin e giorno dell'ultimo salvato (stessa uscita)."""
    q = update.callback_query
    await q.answer()
    last = ctx.user_data.get("last")
    if not last:
        await q.edit_message_text("Non ho più la posizione precedente — usa 🍄 Trovato.")
        return ConversationHandler.END
    ctx.user_data["obs"] = {"ts_submit": _now_iso(), "user_id": update.effective_user.id,
                            "is_blank": 0, "id_verified": 1, **last}
    await q.edit_message_text("Stessa posizione e stesso giorno.")
    await q.message.reply_text("Specie?", reply_markup=_species_keyboard("f"))
    return F_SPECIES


async def annulla(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.pop("obs", None)
    await update.message.reply_text("Annullato.", reply_markup=MAIN_KB)
    return ConversationHandler.END


async def unknown(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Messaggio non gestito → feedback, mai silenzio."""
    if ctx.user_data.get("obs") is not None:      # raccolta in corso, input non atteso per lo step
        await update.message.reply_text("Segui i bottoni qui sopra per continuare (o /annulla).")
        return
    hint = "📍 posizione ricevuta, ma " if (update.message and update.message.location) else ""
    await update.message.reply_text(
        f"{hint}non ho una raccolta in corso. Tocca 🍄 Trovato / 🚫 Vuoto / 🎯 Mirato (o /start).",
        reply_markup=MAIN_KB)


def build_application(token: str) -> Application:
    db.init_db()
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler(["start", "help"], start))

    fallbacks = [CommandHandler("annulla", annulla)]

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("trovato", trovato),
                      MessageHandler(filters.Regex(f"^{BTN_TROVATO}$"), trovato),
                      CallbackQueryHandler(ancora, pattern=r"^again:")],
        states={
            F_SPECIES: [CallbackQueryHandler(f_species, pattern=r"^f:")],
            F_LOCATION: [MessageHandler(filters.LOCATION, f_location)],
            F_DATE: [CallbackQueryHandler(f_date, pattern=r"^fd:")],
            F_DATETEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, f_datetext)],
            F_PHASE: [CallbackQueryHandler(f_phase, pattern=r"^ph:")],
            F_OLDREASON: [CallbackQueryHandler(f_oldreason, pattern=r"^or:")],
            F_ABUNDANCE: [CallbackQueryHandler(f_abundance, pattern=r"^ab:")],
            # `| COMMAND` serve per /skip e /fine, ma senza escludere /annulla lo stato
            # se lo mangerebbe e l'annullamento salverebbe invece di annullare.
            F_WEIGHT: [CallbackQueryHandler(f_weight, pattern=r"^wt:"),
                       MessageHandler((filters.TEXT | filters.COMMAND) & ~_CANCEL, f_weight)],
            F_PHOTO: [MessageHandler((filters.PHOTO | filters.COMMAND) & ~_CANCEL, f_photo)],
        },
        fallbacks=fallbacks,
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("vuoto", vuoto),
                      MessageHandler(filters.Regex(f"^{BTN_VUOTO}$"), vuoto)],
        states={
            B_LOCATION: [MessageHandler(filters.LOCATION, b_location)],
            B_DATE: [CallbackQueryHandler(b_date, pattern=r"^bd:")],
            B_DATETEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, b_datetext)],
            B_EFFORT: [CallbackQueryHandler(b_effort, pattern=r"^ef:")],
        },
        fallbacks=fallbacks,
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("mirato", mirato),
                      MessageHandler(filters.Regex(f"^{BTN_MIRATO}$"), mirato)],
        states={
            T_SPECIES: [CallbackQueryHandler(t_species, pattern=r"^t:")],
            T_LOCATION: [MessageHandler(filters.LOCATION, t_location)],
            T_DATE: [CallbackQueryHandler(t_date, pattern=r"^td:")],
            T_DATETEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, t_datetext)],
            T_EFFORT: [CallbackQueryHandler(t_effort, pattern=r"^ef:")],
        },
        fallbacks=fallbacks,
    ))
    app.add_handler(MessageHandler(~filters.COMMAND, unknown))   # ultimo: mai silenzio
    return app


def _load_dotenv() -> None:
    """Carica .env se presente (senza dipendenze): KEY=VALUE, righe # ignorate."""
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def main() -> None:
    _load_dotenv()
    token = os.environ.get("MAPPA_FUNGHI_BOT_TOKEN")
    if not token:
        raise SystemExit("Manca MAPPA_FUNGHI_BOT_TOKEN (token @BotFather).")
    log.info("Bot avviato con %d specie: %s", len(REGISTRY),
             ", ".join(p.common_name for p in REGISTRY.values()))
    build_application(token).run_polling()


if __name__ == "__main__":
    main()
