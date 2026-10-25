# -*- coding: utf-8 -*-
"""
@premiumuchun_bot — şaxsiy yordamçi (administrator) bot.

Nima qiladi:
  • Botga yozilgan har qanday xabarni (matn, rasm, video, ovoz, fayl, stiker...) egasiga yuboradi.
  • Birinçi marta yozganga salom beradi, keyingi xabarlarga «kuting» deb javob qaytaradi.
  • Egasi kelgan xabarga «Reply» qilib javob yozsa, javob öşa odamga boradi.
  • Telegram Business (Premium) orqali şaxsiy akkauntga ulansa, şaxsiy çatlarda ham avtojavob beradi.

Sozlamalar Railway → Variables bölimida beriladi:
  BOT_TOKEN  — @BotFather bergan token (kodga yozilmaydi!)
  ADMIN_ID   — sizning Telegram ID raqamingiz (botga /id yozib bilib olasiz)
  DB_PATH    — ma’lumotlar bazasi yöli, masalan /data/bot.db (Volume ulangan bölsa)
"""

import asyncio
import html
import logging
import os
import sqlite3
import time

from telegram import BotCommand, BotCommandScopeChat, BotCommandScopeDefault, Update
from telegram.constants import ParseMode
from telegram.error import Forbidden, TelegramError
from telegram.ext import (
    Application,
    BusinessConnectionHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ============================ SOZLAMALAR ============================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0").strip() or 0)
DB_PATH = os.getenv("DB_PATH", "bot.db").strip()

# Foydalanuvçiga boradigan matnlar (xohlasangiz şu yerda özgartiring)
SALOM_MATNI = (
    "Assalomu alaykum, {ism}! 👋\n\n"
    "Xabaringizni yozib qoldiring, u albatta xöjayinga yetkaziladi."
)
KUTING_MATNI = (
    "Xöjayinga xabaringizni yetkazaman, ular tez orada siz bilan boğlanadi. "
    "Iltimos, kuting. ⏳"
)

# Ketma-ket yozilgan xabarlarga «kuting» javobi necça daqiqada bir marta qaytsin.
# 0 qilib qöysangiz — har bir xabarga javob beradi.
AVTOJAVOB_ORALIQ_DAQIQA = int(os.getenv("AVTOJAVOB_ORALIQ_DAQIQA", "5"))

# Odam şunça soat yozmay turib, keyin yana yozsa — qaytadan salom beriladi.
YANGI_SUHBAT_SOAT = int(os.getenv("YANGI_SUHBAT_SOAT", "12"))

# Business rejimi: egasi öşa çatda öz qöli bilan yozgan bölsa, şunça soat bot jim turadi.
EGASI_FAOL_SOAT = int(os.getenv("EGASI_FAOL_SOAT", "2"))

# ====================================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

# --------------------------- Ma’lumotlar bazasi ---------------------------

if os.path.dirname(DB_PATH):
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row
db.executescript(
    """
    CREATE TABLE IF NOT EXISTS users (
        user_id    INTEGER PRIMARY KEY,
        full_name  TEXT,
        username   TEXT,
        first_seen REAL,
        last_seen  REAL,
        msg_count  INTEGER DEFAULT 0,
        banned     INTEGER DEFAULT 0,
        last_auto  REAL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS msg_map (
        admin_msg_id INTEGER PRIMARY KEY,
        user_id      INTEGER
    );
    CREATE TABLE IF NOT EXISTS biz_chats (
        chat_id      INTEGER PRIMARY KEY,
        last_seen    REAL DEFAULT 0,
        last_auto    REAL DEFAULT 0,
        owner_active REAL DEFAULT 0
    );
    """
)
db.commit()


def hozir() -> float:
    return time.time()


def user_olish(uid: int):
    return db.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()


def map_saqlash(admin_msg_id: int, uid: int) -> None:
    db.execute(
        "INSERT OR REPLACE INTO msg_map(admin_msg_id, user_id) VALUES(?, ?)",
        (admin_msg_id, uid),
    )
    db.commit()


def reply_egasini_topish(msg):
    """Admin reply qilgan xabar qaysi foydalanuvçidan kelganini topadi."""
    r = msg.reply_to_message
    if not r:
        return None
    row = db.execute(
        "SELECT user_id FROM msg_map WHERE admin_msg_id=?", (r.message_id,)
    ).fetchone()
    if row:
        return row["user_id"]
    origin = getattr(r, "forward_origin", None)
    if origin is not None and getattr(origin, "sender_user", None):
        return origin.sender_user.id
    return None


def is_admin(update: Update) -> bool:
    u = update.effective_user
    return bool(ADMIN_ID) and u is not None and u.id == ADMIN_ID


# --------------------------- Egasiga yetkaziş ---------------------------

async def egasiga_yetkaz(
    context: ContextTypes.DEFAULT_TYPE, msg, user, yangi: bool, forward: bool = True
) -> None:
    if not ADMIN_ID:
        log.warning("ADMIN_ID kiritilmagan — xabar egasiga yuborilmadi.")
        return

    bot = context.bot
    t = hozir()
    oxirgi = context.bot_data.get("last_sender")

    # Bir odam ketma-ket yozsa, sarlavha har safar takrorlanmaydi (10 daqiqa içida)
    sarlavha_kerak = yangi or not oxirgi or oxirgi[0] != user.id or t - oxirgi[1] > 600
    if sarlavha_kerak:
        ism = html.escape(user.full_name or "Nomsiz")
        matn = (
            ("🆕 <b>Yangi foydalanuvçi</b>\n" if yangi else "📩 <b>Xabar</b>\n")
            + f'👤 <a href="tg://user?id={user.id}">{ism}</a>'
            + (f" (@{user.username})" if user.username else "")
            + f"\n🆔 <code>{user.id}</code>\n"
            + (
                "↩️ <i>Javob beriş uçun pastdagi xabarga «Reply» qiling</i>"
                if forward
                else "🚪 <i>Botga kirdi (/start bosdi)</i>"
            )
        )
        try:
            s = await bot.send_message(ADMIN_ID, matn, parse_mode=ParseMode.HTML)
            map_saqlash(s.message_id, user.id)
        except TelegramError as e:
            log.error("Sarlavha yuborilmadi: %s", e)
    context.bot_data["last_sender"] = (user.id, t)
    if not forward:
        return

    try:
        f = await msg.forward(ADMIN_ID)
        map_saqlash(f.message_id, user.id)
    except TelegramError:
        try:
            c = await msg.copy(ADMIN_ID)
            map_saqlash(c.message_id, user.id)
        except TelegramError as e:
            log.error("Xabar yönaltirilmadi: %s", e)
            s = await bot.send_message(
                ADMIN_ID,
                f"⚠️ <code>{user.id}</code> yuborgan xabarni yönaltirib bölmadi.",
                parse_mode=ParseMode.HTML,
            )
            map_saqlash(s.message_id, user.id)


# --------------------------- Foydalanuvçi tomoni ---------------------------

def user_yangilash(user, t: float, count_msg: bool) -> tuple:
    """Foydalanuvçini bazaga yozadi. (yangi, yangi_suhbat, eski_qator) qaytaradi."""
    row = user_olish(user.id)
    yangi = row is None
    yangi_suhbat = yangi or (t - (row["last_seen"] or 0) > YANGI_SUHBAT_SOAT * 3600)
    if yangi:
        db.execute(
            "INSERT INTO users(user_id, full_name, username, first_seen, last_seen, msg_count) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (user.id, user.full_name, user.username, t, t, 1 if count_msg else 0),
        )
    else:
        db.execute(
            "UPDATE users SET full_name=?, username=?, last_seen=?, msg_count=msg_count+? "
            "WHERE user_id=?",
            (user.full_name, user.username, t, 1 if count_msg else 0, user.id),
        )
    db.commit()
    return yangi, yangi_suhbat, row


async def salom_yubor(msg, user) -> None:
    await msg.reply_text(SALOM_MATNI.format(ism=user.first_name or "hurmatli foydalanuvçi"))
    # Salomdan keyingi birinçi xabarga albatta «kuting» javobi borsin
    db.execute("UPDATE users SET last_auto=0 WHERE user_id=?", (user.id,))
    db.commit()


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if is_admin(update):
        await cmd_help(update, context)
        return

    user, msg = update.effective_user, update.message
    row = user_olish(user.id)
    if row and row["banned"]:
        return
    yangi, _, _ = user_yangilash(user, hozir(), count_msg=False)
    if yangi:
        await egasiga_yetkaz(context, msg, user, yangi=True, forward=False)
    await salom_yubor(msg, user)


async def user_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if is_admin(update):
        await admin_xabari(update, context)
        return

    msg, user = update.message, update.effective_user
    if not msg or not user:
        return
    row = user_olish(user.id)
    if row and row["banned"]:
        return  # bloklangan — jim

    t = hozir()
    yangi, yangi_suhbat, eski = user_yangilash(user, t, count_msg=True)

    # 1) Xabarni egasiga yetkazamiz
    await egasiga_yetkaz(context, msg, user, yangi)

    # 2) Avtojavob
    try:
        if yangi_suhbat:
            await salom_yubor(msg, user)
        else:
            oxirgi_avto = eski["last_auto"] or 0
            if t - oxirgi_avto >= AVTOJAVOB_ORALIQ_DAQIQA * 60:
                await msg.reply_text(KUTING_MATNI)
                db.execute("UPDATE users SET last_auto=? WHERE user_id=?", (t, user.id))
                db.commit()
    except TelegramError as e:
        log.error("Avtojavob yuborilmadi: %s", e)


# --------------------------- Admin tomoni ---------------------------

YORDAM_MATNI = (
    "🤖 <b>Yordamçi bot işlayapti.</b>\n\n"
    "Sizga kelgan xabar ostiga <b>«Reply»</b> qilib javob yozsangiz, javob öşa odamga boradi "
    "(matn, rasm, ovoz, video, fayl — hammasi).\n\n"
    "<b>Buyruqlar:</b>\n"
    "/stat — statistika\n"
    "/ban — bloklaş (xabarga reply qilib yoki <code>/ban ID</code>)\n"
    "/unban — blokdan çiqariş\n"
    "/javob — <code>/javob ID matn</code> şaklida javob yoziş\n"
    "/hammaga — reply qilingan xabarni barça foydalanuvçilarga yuboriş\n"
    "/id — ID raqamingiz"
)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(YORDAM_MATNI, parse_mode=ParseMode.HTML)


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        f"🆔 Sizning ID raqamingiz: <code>{update.effective_user.id}</code>",
        parse_mode=ParseMode.HTML,
    )


async def admin_xabari(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not msg.reply_to_message:
        await msg.reply_text(
            "ℹ️ Kimga javob yozayotganingizni bilişim uçun foydalanuvçining xabariga "
            "«Reply» qiling.\nBuyruqlar: /yordam"
        )
        return

    uid = reply_egasini_topish(msg)
    if not uid:
        await msg.reply_text(
            "⚠️ Bu xabar kimdan kelgani aniqlanmadi. Foydalanuvçining xabariga reply qiling "
            "yoki /javob ID matn buyruğidan foydalaning."
        )
        return

    try:
        await msg.copy(uid)
        await msg.reply_text("✅ Yetkazildi")
    except Forbidden:
        await msg.reply_text("❌ Yuborilmadi: foydalanuvçi botni bloklagan yoki öçirib yuborgan.")
    except TelegramError as e:
        await msg.reply_text(f"❌ Yuborilmadi: {e}")


async def cmd_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if len(context.args) < 2 or not context.args[0].lstrip("-").isdigit():
        await msg.reply_text("Foydalaniş: /javob ID matn")
        return
    uid = int(context.args[0])
    matn = msg.text.split(maxsplit=2)[2]
    try:
        await context.bot.send_message(uid, matn)
        await msg.reply_text("✅ Yetkazildi")
    except Forbidden:
        await msg.reply_text("❌ Yuborilmadi: foydalanuvçi botni bloklagan.")
    except TelegramError as e:
        await msg.reply_text(f"❌ Yuborilmadi: {e}")


def target_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args and context.args[0].lstrip("-").isdigit():
        return int(context.args[0])
    return reply_egasini_topish(update.message)


async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = target_id(update, context)
    if not uid:
        await update.message.reply_text("Kimni bloklaş kerak? Xabarga reply qiling yoki /ban ID yozing.")
        return
    db.execute(
        "INSERT INTO users(user_id, banned, first_seen, last_seen) VALUES(?, 1, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET banned=1",
        (uid, hozir(), hozir()),
    )
    db.commit()
    await update.message.reply_text(
        f"🚫 <code>{uid}</code> bloklandi. Uning xabarlari endi sizga kelmaydi.",
        parse_mode=ParseMode.HTML,
    )


async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = target_id(update, context)
    if not uid:
        await update.message.reply_text("Kimni blokdan çiqariş kerak? Xabarga reply qiling yoki /unban ID yozing.")
        return
    db.execute("UPDATE users SET banned=0 WHERE user_id=?", (uid,))
    db.commit()
    await update.message.reply_text(
        f"✅ <code>{uid}</code> blokdan çiqarildi.", parse_mode=ParseMode.HTML
    )


async def cmd_stat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    jami = db.execute("SELECT COUNT(*) FROM users WHERE first_seen IS NOT NULL").fetchone()[0]
    xabar = db.execute("SELECT COALESCE(SUM(msg_count), 0) FROM users").fetchone()[0]
    blok = db.execute("SELECT COUNT(*) FROM users WHERE banned=1").fetchone()[0]
    bugun = db.execute(
        "SELECT COUNT(*) FROM users WHERE last_seen >= ?", (hozir() - 86400,)
    ).fetchone()[0]
    biz = db.execute("SELECT COUNT(*) FROM biz_chats").fetchone()[0]
    await update.message.reply_text(
        "📊 <b>Statistika</b>\n"
        f"👥 Foydalanuvçilar: {jami}\n"
        f"✉️ Kelgan xabarlar: {xabar}\n"
        f"🕐 Oxirgi 24 soatda yozganlar: {bugun}\n"
        f"🚫 Bloklanganlar: {blok}\n"
        f"💼 Business çatlar: {biz}",
        parse_mode=ParseMode.HTML,
    )


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    asl = msg.reply_to_message
    if not asl:
        await msg.reply_text(
            "Barça foydalanuvçilarga yuboriladigan xabarga reply qilib /hammaga yozing."
        )
        return
    qatorlar = db.execute("SELECT user_id FROM users WHERE banned=0").fetchall()
    await msg.reply_text(f"📣 Tarqatiş boşlandi: {len(qatorlar)} kişi...")
    ok = xato = 0
    for q in qatorlar:
        try:
            await asl.copy(q["user_id"])
            ok += 1
        except TelegramError:
            xato += 1
        await asyncio.sleep(0.05)  # Telegram limitlariga tuşmaslik uçun
    await msg.reply_text(f"✅ Tugadi. Yuborildi: {ok}, yetib bormadi: {xato}")


# --------------------------- Telegram Business (Premium) ---------------------------

async def biz_owner(context: ContextTypes.DEFAULT_TYPE, conn_id: str):
    kesh = context.bot_data.setdefault("biz_egalari", {})
    if conn_id not in kesh:
        try:
            conn = await context.bot.get_business_connection(conn_id)
            kesh[conn_id] = conn.user.id
        except TelegramError:
            return ADMIN_ID
    return kesh[conn_id]


async def biz_connection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    conn = update.business_connection
    context.bot_data.setdefault("biz_egalari", {})[conn.id] = conn.user.id
    matn = (
        "✅ Bot şaxsiy akkauntingizga ulandi. Endi şaxsiy çatlaringizda ham avtojavob beradi."
        if conn.is_enabled
        else "⛔️ Bot şaxsiy akkauntingizdan uzildi."
    )
    try:
        await context.bot.send_message(conn.user_chat_id, matn)
    except TelegramError:
        pass


async def biz_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.business_message
    if not msg or not msg.from_user or msg.sender_business_bot:
        return
    conn_id = msg.business_connection_id
    egasi = await biz_owner(context, conn_id)
    t, chat_id = hozir(), msg.chat_id

    row = db.execute("SELECT * FROM biz_chats WHERE chat_id=?", (chat_id,)).fetchone()
    if row is None:
        db.execute("INSERT INTO biz_chats(chat_id) VALUES(?)", (chat_id,))
        db.commit()

    # Egasi özi yozdi — bot aralaşmaydi
    if msg.from_user.id == egasi:
        db.execute("UPDATE biz_chats SET owner_active=? WHERE chat_id=?", (t, chat_id))
        db.commit()
        return

    yangi_suhbat = row is None or t - (row["last_seen"] or 0) > YANGI_SUHBAT_SOAT * 3600
    db.execute("UPDATE biz_chats SET last_seen=? WHERE chat_id=?", (t, chat_id))
    db.commit()

    if row is not None and t - (row["owner_active"] or 0) < EGASI_FAOL_SOAT * 3600:
        return  # egasi yaqinda şu çatda yozişgan

    try:
        if yangi_suhbat:
            await context.bot.send_message(
                chat_id,
                SALOM_MATNI.format(ism=msg.from_user.first_name or "hurmatli foydalanuvçi"),
                business_connection_id=conn_id,
            )
            db.execute("UPDATE biz_chats SET last_auto=0 WHERE chat_id=?", (chat_id,))
        elif t - (row["last_auto"] or 0) >= AVTOJAVOB_ORALIQ_DAQIQA * 60:
            await context.bot.send_message(chat_id, KUTING_MATNI, business_connection_id=conn_id)
            db.execute("UPDATE biz_chats SET last_auto=? WHERE chat_id=?", (t, chat_id))
        db.commit()
    except TelegramError as e:
        log.error("Business avtojavob yuborilmadi: %s", e)


# --------------------------- Xatolar va işga tuşiriş ---------------------------

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Kutilmagan xato", exc_info=context.error)


async def post_init(app: Application) -> None:
    await app.bot.set_my_commands(
        [BotCommand("start", "Boşlaş")], scope=BotCommandScopeDefault()
    )
    if ADMIN_ID:
        try:
            await app.bot.set_my_commands(
                [
                    BotCommand("yordam", "Qöllanma"),
                    BotCommand("stat", "Statistika"),
                    BotCommand("ban", "Bloklaş"),
                    BotCommand("unban", "Blokdan çiqariş"),
                    BotCommand("javob", "ID orqali javob yoziş"),
                    BotCommand("hammaga", "Hammaga xabar yuboriş"),
                    BotCommand("id", "ID raqamim"),
                ],
                scope=BotCommandScopeChat(ADMIN_ID),
            )
            await app.bot.send_message(ADMIN_ID, "🟢 Yordamçi bot işga tuşdi. /yordam")
        except TelegramError as e:
            log.warning("Adminga xabar yuborilmadi (avval botga /start yozing): %s", e)
    else:
        log.warning(
            "ADMIN_ID kiritilmagan! Botga /id yozing, çiqqan raqamni Railway → Variables "
            "bölimiga ADMIN_ID sifatida qöşing."
        )
    me = await app.bot.get_me()
    log.info("Bot işga tuşdi: @%s", me.username)


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN topilmadi. Railway → Variables bölimiga BOT_TOKEN qöşing.")

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    admin = filters.User(user_id=ADMIN_ID) & filters.ChatType.PRIVATE
    private = filters.ChatType.PRIVATE & filters.UpdateType.MESSAGE

    app.add_handler(CommandHandler("start", cmd_start, filters=private))
    app.add_handler(CommandHandler("id", cmd_id, filters=private))
    app.add_handler(CommandHandler("yordam", cmd_help, filters=admin))
    app.add_handler(CommandHandler("stat", cmd_stat, filters=admin))
    app.add_handler(CommandHandler("ban", cmd_ban, filters=admin))
    app.add_handler(CommandHandler("unban", cmd_unban, filters=admin))
    app.add_handler(CommandHandler("javob", cmd_reply, filters=admin))
    app.add_handler(CommandHandler("hammaga", cmd_broadcast, filters=admin))

    app.add_handler(BusinessConnectionHandler(biz_connection))
    app.add_handler(MessageHandler(filters.UpdateType.BUSINESS_MESSAGE, biz_message))
    app.add_handler(MessageHandler(private, user_message))

    app.add_error_handler(error_handler)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
