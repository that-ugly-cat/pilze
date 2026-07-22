"""Bot Telegram di cattura (spec §6.1).

Registra ritrovamenti e uscite a vuoto — deve essere VELOCE quanto il ritrovamento.
Usa la LOCATION nativa di Telegram (l'EXIF delle foto è strippato → niente geotag).

Avvio:
    export MAPPA_FUNGHI_BOT_TOKEN=...        # token da @BotFather
    python -m bot.bot

Flussi:
    /trovato   ritrovamento (specie, posizione, fase, abbondanza, peso, foto)
    /vuoto     uscita a vuoto generica (posizione, effort)
    /mirato    uscita a vuoto mirata su una specie (zero forte sul timing)
    /annulla   annulla il flusso in corso
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

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
(F_SPECIES, F_LOCATION, F_PHASE, F_OLDREASON, F_ABUNDANCE, F_WEIGHT, F_PHOTO,
 B_LOCATION, B_EFFORT, T_SPECIES, T_LOCATION, T_EFFORT) = range(12)

PHASES = [("primordi", "🌱 primordi"), ("buono", "👌 buono"), ("vecchio", "🍂 vecchio")]
# perché "vecchio": senescente = buttata semplicemente tardi (limite superiore del lag);
# abortito = condizioni girate (secco) → informa il moisture floor (spec §6.1).
OLD_REASONS = [("senescente", "🍂 senescente (tardi)"), ("abortito", "🌵 abortito (secco)")]
ABUNDANCE = [("uno", "1"), ("pochi", "pochi"), ("molti", "molti")]


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
    ctx.user_data["obs"]["species"] = sid
    name = REGISTRY[sid].common_name if sid in REGISTRY else sid
    await q.edit_message_text(f"Specie: {name}")
    await q.message.reply_text("Posizione? (usa il bottone)", reply_markup=_LOCATION_KB)
    return F_LOCATION


async def f_location(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    loc = update.message.location
    ctx.user_data["obs"]["lat"] = loc.latitude
    ctx.user_data["obs"]["lon"] = loc.longitude
    await update.message.reply_text("📍 Posizione ok.", reply_markup=ReplyKeyboardRemove())
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
    await q.message.reply_text("Peso raccolto in grammi? (numero, o /skip)")
    return F_WEIGHT


async def f_weight(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.text and update.message.text != "/skip":
        try:
            ctx.user_data["obs"]["weight_g"] = float(update.message.text.replace(",", "."))
        except ValueError:
            await update.message.reply_text("Numero non valido — riprova o /skip")
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
    await update.message.reply_text("📍 Posizione ok. Da quanti minuti giravi? (numero)",
                                    reply_markup=ReplyKeyboardRemove())
    return B_EFFORT


async def b_effort(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        ctx.user_data["obs"]["effort_min"] = int(update.message.text)
    except (ValueError, TypeError):
        await update.message.reply_text("Numero di minuti — riprova")
        return B_EFFORT
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
    ctx.user_data["obs"]["target_species"] = q.data.split(":", 1)[1]
    await q.edit_message_text(f"Cercavi: {ctx.user_data['obs']['target_species']}")
    await q.message.reply_text("Posizione?", reply_markup=_LOCATION_KB)
    return T_LOCATION


async def t_location(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    loc = update.message.location
    ctx.user_data["obs"].update({"lat": loc.latitude, "lon": loc.longitude})
    await update.message.reply_text("📍 Posizione ok. Da quanti minuti giravi? (numero)",
                                    reply_markup=ReplyKeyboardRemove())
    return T_EFFORT


async def t_effort(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        ctx.user_data["obs"]["effort_min"] = int(update.message.text)
    except (ValueError, TypeError):
        await update.message.reply_text("Numero di minuti — riprova")
        return T_EFFORT
    return await _save_and_end(update, ctx)


# --------------------------------------------------------------------------- #
async def _save_and_end(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    obs = ctx.user_data.pop("obs")
    obs_id = db.insert_observation(obs)
    await update.message.reply_text(f"✅ Salvato (#{obs_id}). Grazie!", reply_markup=MAIN_KB)
    return ConversationHandler.END


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
                      MessageHandler(filters.Regex(f"^{BTN_TROVATO}$"), trovato)],
        states={
            F_SPECIES: [CallbackQueryHandler(f_species, pattern=r"^f:")],
            F_LOCATION: [MessageHandler(filters.LOCATION, f_location)],
            F_PHASE: [CallbackQueryHandler(f_phase, pattern=r"^ph:")],
            F_OLDREASON: [CallbackQueryHandler(f_oldreason, pattern=r"^or:")],
            F_ABUNDANCE: [CallbackQueryHandler(f_abundance, pattern=r"^ab:")],
            F_WEIGHT: [MessageHandler(filters.TEXT | filters.COMMAND, f_weight)],
            F_PHOTO: [MessageHandler(filters.PHOTO | filters.COMMAND, f_photo)],
        },
        fallbacks=fallbacks,
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("vuoto", vuoto),
                      MessageHandler(filters.Regex(f"^{BTN_VUOTO}$"), vuoto)],
        states={
            B_LOCATION: [MessageHandler(filters.LOCATION, b_location)],
            B_EFFORT: [MessageHandler(filters.TEXT & ~filters.COMMAND, b_effort)],
        },
        fallbacks=fallbacks,
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("mirato", mirato),
                      MessageHandler(filters.Regex(f"^{BTN_MIRATO}$"), mirato)],
        states={
            T_SPECIES: [CallbackQueryHandler(t_species, pattern=r"^t:")],
            T_LOCATION: [MessageHandler(filters.LOCATION, t_location)],
            T_EFFORT: [MessageHandler(filters.TEXT & ~filters.COMMAND, t_effort)],
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
