"""
FILE STORE BOT — AnimeFlix ke saath
====================================
Video wale jaisa file store bot: files ko PERMANENT shareable link me badalta hai.

ENV VARS (Render pe daalna):
  FILESTORE_BOT_TOKEN  — BotFather se mila token (is se bot ON hota hai)
  OWNER_ID             — tumhara Telegram user ID (mods ke commands ke liye)
  FORCE_SUB            — optional, comma-separated @channelusernames
  AUTO_DELETE          — optional seconds (file itne sec baad auto-delete, 0=off)
  FS_CUSTOM_CAPTION    — optional, delivered file ka caption ({filename} use karo)
  FS_CUSTOM_BTN        — optional "Button Text|https://url"
  FS_STORAGE_CHANNEL   — optional -100xxx channel id (permanent storage ke liye, bot admin ho)

Commands:
  /start — menu | /start <link> — file lo
  /id — apna ID
  /genlink — (reply ya seedha file bhejo) link banao
  /batch <link1> <link2> — range ka ek link
  /broadcast — (reply) sab users ko bhejo
  /ban /unban /stats — owner
"""
import os
import re
import json
import secrets
import asyncio
import asyncpg
from pyrogram import Client, filters
from pyrogram.handlers import MessageHandler, CallbackQueryHandler
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait, UserIsBlocked, InputUserDeactivated

API_ID = int(os.getenv("API_ID", "0") or 0)
API_HASH = os.getenv("API_HASH", "")
TOKEN = os.getenv("FILESTORE_BOT_TOKEN", "").strip()
DB_URL = os.getenv("DATABASE_URL", "")
OWNER_IDS = {int(x) for x in re.split(r"[,\s]+", os.getenv("OWNER_ID", "")) if x.strip().isdigit()}
FORCE_SUBS = [x.strip() for x in re.split(r"[,\s]+", os.getenv("FORCE_SUB", "")) if x.strip() and not x.strip().isdigit()]
FORCE_IDS = [int(x) for x in re.split(r"[,\s]+", os.getenv("FORCE_SUB", "")) if x.strip().lstrip("-").isdigit()]
AUTO_DELETE = int(os.getenv("AUTO_DELETE", "0") or 0)
CUSTOM_CAPTION = os.getenv("FS_CUSTOM_CAPTION", "")
CUSTOM_BTN_RAW = os.getenv("FS_CUSTOM_BTN", "")
STORAGE_CHANNEL = os.getenv("FS_STORAGE_CHANNEL", "")

client = None
pool = None
bot_me = None

WELCOME = (
    "Hello {name} ✨,\n\n"
    "I am a permanent file store bot — "
    "mujhe file bhejo, main shareable link bana dunga.\n"
    "Link kholne wale ko file turant mil jayegi.\n\n"
    "To know more click help button"
)
HELP_TEXT = (
    "✨ <b>Help Menu</b>\n\n"
    "I am a permanent file store bot.\n"
    "Moderators file bhejte hain — main shareable link deta hoon.\n"
    "Koi bhi link kholke file le sakta hai.\n\n"
    "<b>📝 Commands:</b>\n"
    "/start — main menu\n"
    "/id — apna Telegram ID dekho\n\n"
    "<b>🔧 Moderators:</b>\n"
    "/genlink — file ka link banao (reply karke ya seedha file bhejo)\n"
    "/batch <code>link1 link2</code> — channel range ka ek link\n"
    "/broadcast — reply karke sab users ko bhejo\n"
    "/stats — total users/files\n"
    "/ban /unban — user control\n\n"
    "💡 <b>Tip:</b> moderator seedha koi bhi file bhejega — link khud ban jayega!"
)
ABOUT_TEXT = (
    "✨ <b>About Me</b>\n\n"
    "⭐ <b>Name:</b> {bot}\n"
    "⭐ <b>Type:</b> Permanent File Store Bot\n"
    "⭐ <b>Storage:</b> Telegram + Neon DB\n"
    "⭐ <b>Powered by:</b> AnimeFlix"
)

# ---------------- helpers ----------------

def is_mod(uid):
    return uid in OWNER_IDS


def main_kb():
    rows = [[InlineKeyboardButton("🆘 HELP", callback_data="help"),
             InlineKeyboardButton("ℹ️ ABOUT", callback_data="about")]]
    if CUSTOM_BTN_RAW and "|" in CUSTOM_BTN_RAW:
        t, u = CUSTOM_BTN_RAW.split("|", 1)
        rows.append([InlineKeyboardButton(t.strip(), url=u.strip())])
    return InlineKeyboardMarkup(rows)


def btn_kb():
    if CUSTOM_BTN_RAW and "|" in CUSTOM_BTN_RAW:
        t, u = CUSTOM_BTN_RAW.split("|", 1)
        return InlineKeyboardMarkup([[InlineKeyboardButton(t.strip(), url=u.strip())]])
    return None


def parse_tme_link(u):
    """https://t.me/c/2700515710/49 -> (-1002700515710, 49)
       https://t.me/username/49 -> ('username', 49)"""
    if not u:
        return None, None
    u = u.strip()
    m = re.match(r"(?:https?://)?t\.me/c/(\d+)/(\d+)", u)
    if m:
        return int("-100" + m.group(1)), int(m.group(2))
    m = re.match(r"(?:https?://)?t\.me/([A-Za-z0-9_]{4,})/(\d+)", u)
    if m:
        return m.group(1), int(m.group(2))
    return None, None


async def make_link(chat_id, msg_ids, by):
    lid = secrets.token_urlsafe(6).replace("-", "A").replace("_", "B")[:8]
    await pool.execute(
        "INSERT INTO fs_files (link_id, chat_id, msg_ids, created_by) VALUES ($1,$2,$3,$4)",
        lid, chat_id, json.dumps(msg_ids), by,
    )
    return lid, f"https://t.me/{bot_me.username}?start={lid}"


async def link_result_msg(lid, url, n):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 SHARE URL", url=f"https://t.me/share/url?url={url}")],
    ])
    txt = f"✅ <b>Here is your link:</b>\n\n<code>{url}</code>\n\n📦 {n} file(s) stored — permanent!"
    return txt, kb

# ---------------- delivery ----------------

async def _delete_later(msgs, sec):
    await asyncio.sleep(sec)
    for m in msgs:
        try:
            await m.delete()
        except Exception:
            pass


async def check_force_sub(uid):
    not_joined = []
    for ch in FORCE_SUBS:
        try:
            m = await client.get_chat_member(ch, uid)
            if m.status in ("left", "kicked"):
                not_joined.append(ch)
        except Exception:
            not_joined.append(ch)
    for ch in FORCE_IDS:
        try:
            m = await client.get_chat_member(ch, uid)
            if m.status in ("left", "kicked"):
                not_joined.append(ch)
        except Exception:
            not_joined.append(ch)
    return not_joined


async def deliver(uid, link_id):
    row = await pool.fetchrow("SELECT * FROM fs_files WHERE link_id=$1", link_id)
    if not row:
        await client.send_message(uid, "❌ Link invalid hai ya delete ho chuka.")
        return
    not_joined = await check_force_sub(uid)
    if not_joined:
        rows = []
        for i, ch in enumerate(not_joined):
            uname = ch.lstrip("@") if isinstance(ch, str) else str(ch)
            rows.append([InlineKeyboardButton(f"📢 Join Channel {i + 1}", url=f"https://t.me/{uname}")])
        rows.append([InlineKeyboardButton("✅ Joined — Try Again", callback_data=f"retry:{link_id}")])
        await client.send_message(
            uid, "🔒 <b>Pehle channel(s) join karo, phir file milegi!</b>",
            reply_markup=InlineKeyboardMarkup(rows))
        return
    msg_ids = json.loads(row["msg_ids"])
    sent = []
    for i, mid in enumerate(msg_ids):
        try:
            cap = None
            if i == 0 and CUSTOM_CAPTION:
                cap = CUSTOM_CAPTION
            kb = btn_kb() if i == 0 else None
            m = await client.copy_message(
                chat_id=uid, from_chat_id=row["chat_id"], message_id=mid,
                caption=cap, reply_markup=kb)
            sent.append(m)
        except FloodWait as e:
            await asyncio.sleep(min(int(e.value), 20))
            try:
                m = await client.copy_message(
                    chat_id=uid, from_chat_id=row["chat_id"], message_id=mid,
                    caption=cap if i == 0 else None, reply_markup=btn_kb() if i == 0 else None)
                sent.append(m)
            except Exception:
                pass
        except Exception as e:
            print(f"[filebot] deliver fail mid={mid}: {str(e)[:80]}")
    if AUTO_DELETE and sent:
        asyncio.create_task(_delete_later(sent, AUTO_DELETE))

# ---------------- handlers ----------------

async def on_message(_, m):
    global bot_me
    try:
        uid = m.from_user.id if m.from_user else 0
        name = (m.from_user.first_name if m.from_user else "friend") or "friend"
        text = (m.text or m.caption or "").strip()
    except Exception:
        return

    # user-track
    try:
        await pool.execute(
            "INSERT INTO fs_users (user_id, name) VALUES ($1,$2) ON CONFLICT (user_id) DO NOTHING",
            uid, name)
    except Exception:
        pass

    # banned?
    try:
        if await pool.fetchval("SELECT 1 FROM fs_banned WHERE user_id=$1", uid):
            return
    except Exception:
        pass

    # ----- /start -----
    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        if len(parts) > 1 and parts[1].strip():
            await deliver(uid, parts[1].strip())
        else:
            await m.reply(WELCOME.format(name=name), reply_markup=main_kb())
        return

    if text.startswith("/id"):
        await m.reply(f"🆔 Tumhara ID: <code>{uid}</code>")
        return

    if text.startswith("/help"):
        await m.reply(HELP_TEXT, reply_markup=main_kb())
        return

    if text.startswith("/about"):
        await m.reply(ABOUT_TEXT.format(bot="@" + bot_me.username if bot_me else "FileStore"),
                      reply_markup=main_kb())
        return

    # ----- owner/admin commands -----
    if text.startswith("/stats"):
        if not is_mod(uid):
            return await m.reply("❌ Owner only")
        users = await pool.fetchval("SELECT count(*) FROM fs_users")
        files = await pool.fetchval("SELECT count(*) FROM fs_files")
        await m.reply(f"📊 <b>Stats</b>\n👥 Users: {users}\n📦 Links: {files}")
        return

    if text.startswith("/ban") or text.startswith("/unban"):
        if not is_mod(uid):
            return await m.reply("❌ Owner only")
        ban = text.startswith("/ban")
        target = None
        parts = text.split()
        if len(parts) > 1 and parts[1].lstrip("-").isdigit():
            target = int(parts[1])
        elif m.reply_to_message and m.reply_to_message.from_user:
            target = m.reply_to_message.from_user.id
        if not target:
            return await m.reply("Usage: /ban 123456789 (ya kisi message pe reply)")
        if ban:
            await pool.execute("INSERT INTO fs_banned (user_id) VALUES ($1) ON CONFLICT DO NOTHING", target)
            await m.reply(f"🚫 Banned: <code>{target}</code>")
        else:
            await pool.execute("DELETE FROM fs_banned WHERE user_id=$1", target)
            await m.reply(f"✅ Unbanned: <code>{target}</code>")
        return

    if text.startswith("/broadcast"):
        if not is_mod(uid):
            return await m.reply("❌ Owner only")
        r = m.reply_to_message
        if not r:
            return await m.reply("📢 Kisi message pe <b>reply</b> karke /broadcast bhejo.")
        ids = [row["user_id"] for row in await pool.fetch("SELECT user_id FROM fs_users")]
        ok = blk = fail = 0
        status = await m.reply(f"⏳ Broadcast chalu... 0/{len(ids)}")
        for i, t_uid in enumerate(ids):
            try:
                await r.copy(t_uid)
                ok += 1
            except (UserIsBlocked, InputUserDeactivated):
                blk += 1
            except FloodWait as e:
                await asyncio.sleep(min(int(e.value), 15))
                try:
                    await r.copy(t_uid)
                    ok += 1
                except Exception:
                    fail += 1
            except Exception:
                fail += 1
            if i and i % 20 == 0:
                try:
                    await status.edit_text(f"⏳ Broadcast... {i + 1}/{len(ids)} | ✅ {ok} ❌ {fail}")
                except Exception:
                    pass
            await asyncio.sleep(0.05)
        await m.reply(
            f"📢 <b>Broadcast done</b>\n👥 Total: {len(ids)}\n✅ OK: {ok}\n🚫 Blocked: {blk}\n❌ Fail: {fail}")
        return

    # ----- /genlink -----
    if text.startswith("/genlink"):
        if not is_mod(uid):
            return await m.reply("❌ Moderators only")
        r = m.reply_to_message
        if r:
            chat_id, mid = r.chat.id, r.id
            # storage channel me copy karke permanent banao
            if STORAGE_CHANNEL.lstrip("-").isdigit():
                try:
                    cp = await client.copy_message(
                        chat_id=int(STORAGE_CHANNEL), from_chat_id=chat_id, message_id=mid)
                    chat_id, mid = int(STORAGE_CHANNEL), cp.id
                except Exception as e:
                    print(f"[filebot] storage copy fail: {str(e)[:80]}")
            lid, url = await make_link(chat_id, [mid], uid)
            txt, kb = await link_result_msg(lid, url, 1)
            await m.reply(txt, reply_markup=kb, disable_web_page_preview=True)
            return
        # link argument?
        parts = text.split()
        if len(parts) > 1:
            chat_id, mid = parse_tme_link(parts[1])
            if chat_id:
                lid, url = await make_link(chat_id, [mid], uid)
                txt, kb = await link_result_msg(lid, url, 1)
                await m.reply(txt, reply_markup=kb, disable_web_page_preview=True)
                return
        await m.reply("💡 File bhejo ya kisi message pe reply karke /genlink likho.")
        return

    # ----- /batch -----
    if text.startswith("/batch"):
        if not is_mod(uid):
            return await m.reply("❌ Moderators only")
        parts = text.split()
        if len(parts) < 3:
            return await m.reply(
                "Usage: <code>/batch https://t.me/c/xxx/10 https://t.me/c/xxx/50</code>\n"
                "(pehli aur aakhri message ke links — beech ki sab ek link me aa jayengi)")
        c1, s = parse_tme_link(parts[1])
        c2, e = parse_tme_link(parts[2])
        if not c1 or not c2 or c1 != c2 or e < s:
            return await m.reply("❌ Dono links same channel ke hone chahiye (pehla chhota number, doosra bada)")
        if e - s > 300:
            return await m.reply("❌ Max 300 messages ek link me")
        msg_ids = list(range(s, e + 1))
        lid, url = await make_link(c1, msg_ids, uid)
        txt, kb = await link_result_msg(lid, url, len(msg_ids))
        await m.reply(txt, reply_markup=kb, disable_web_page_preview=True)
        return

    # ----- moderator ne seedha file bheji → auto-link! -----
    if is_mod(uid) and (m.video or m.document or m.audio or m.photo or m.animation):
        chat_id, mid = m.chat.id, m.id
        if STORAGE_CHANNEL.lstrip("-").isdigit():
            try:
                cp = await client.copy_message(
                    chat_id=int(STORAGE_CHANNEL), from_chat_id=chat_id, message_id=mid)
                chat_id, mid = int(STORAGE_CHANNEL), cp.id
            except Exception as e:
                print(f"[filebot] storage copy fail: {str(e)[:80]}")
        lid, url = await make_link(chat_id, [mid], uid)
        txt, kb = await link_result_msg(lid, url, 1)
        await m.reply(txt, reply_markup=kb, disable_web_page_preview=True)
        return

    # default replies
    if is_mod(uid):
        await m.reply("💡 File bhejo — link khud ban jayega! Ya /help likho.")
    else:
        await m.reply(WELCOME.format(name=name), reply_markup=main_kb())


async def on_callback(_, cq):
    data = cq.data or ""
    try:
        if data == "help":
            await cq.message.edit_text(HELP_TEXT, reply_markup=main_kb())
        elif data == "about":
            await cq.message.edit_text(
                ABOUT_TEXT.format(bot="@" + bot_me.username if bot_me else "FileStore"),
                reply_markup=main_kb())
        elif data.startswith("retry:"):
            await cq.answer()
            await deliver(cq.from_user.id, data.split(":", 1)[1])
            return
        else:
            await cq.answer()
    except Exception:
        try:
            await cq.answer()
        except Exception:
            pass
        return
    try:
        await cq.answer()
    except Exception:
        pass

# ---------------- lifecycle ----------------

async def start():
    global client, pool, bot_me
    if not TOKEN:
        print("[filebot] FILESTORE_BOT_TOKEN missing — file bot OFF")
        return
    if not DB_URL or not API_ID or not API_HASH:
        print("[filebot] API_ID/API_HASH/DATABASE_URL missing — file bot OFF")
        return
    pool = await asyncpg.create_pool(DB_URL, statement_cache_size=0, min_size=1, max_size=5)
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS fs_files (
            id SERIAL PRIMARY KEY,
            link_id TEXT UNIQUE NOT NULL,
            chat_id BIGINT NOT NULL,
            msg_ids TEXT NOT NULL,
            created_by BIGINT,
            created_at TIMESTAMP DEFAULT now()
        )""")
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS fs_users (
            user_id BIGINT PRIMARY KEY,
            name TEXT,
            joined TIMESTAMP DEFAULT now()
        )""")
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS fs_banned (
            user_id BIGINT PRIMARY KEY
        )""")
    client = Client("filebot", api_id=API_ID, api_hash=API_HASH,
                    bot_token=TOKEN, in_memory=True)
    client.add_handler(MessageHandler(on_message, filters.private))
    client.add_handler(CallbackQueryHandler(on_callback))
    await client.start()
    bot_me = await client.get_me()
    print(f"[filebot] LIVE: @{bot_me.username}")


async def stop():
    global client, pool
    if client:
        try:
            await client.stop()
        except Exception:
            pass
    if pool:
        try:
            await pool.close()
        except Exception:
            pass


if __name__ == "__main__":
    async def _main():
        await start()
        print("[filebot] standalone mode — Ctrl+C to stop")
        await asyncio.Event().wait()
    asyncio.run(_main())
