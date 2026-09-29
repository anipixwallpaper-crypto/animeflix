"""
FILE STORE BOT v2 — MAIN BOT + CLONE SYSTEM
=============================================
MAIN BOT (FILESTORE_BOT_TOKEN): sirf CLONE FACTORY —
  - koi bhi user /start kare → CREATE MY OWN CLONE → token bheje → uska apna file store bot ready
  - max 6 clones per user, max 6 force-sub per clone
CLONE BOTS: asli file store —
  - /genlink, /special_link (create+modify+delete+edit), /batch, /broadcast, /ban, /unban, /stats
  - file bhejne se kuch NAHI hota — sirf command ke baad (owner order)
  - force sub: join now + try again; private channel pe join-request ya normal mode

ENV:
  FILESTORE_BOT_TOKEN — MAIN bot ka token
  OWNER_ID            — asli malik (super admin)
  FB_UPDATE_LINK     — update channel ka link YA -100 channel ID (bot admin ho to khud link banayega)
  FB_MAX_CLONES      — total clones limit (default 25)
"""
import os
import re
import json
import secrets
import asyncio
import asyncpg
from pyrogram import Client, filters
from pyrogram.handlers import MessageHandler, CallbackQueryHandler, ChatJoinRequestHandler
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait, UserIsBlocked, InputUserDeactivated

API_ID = int(os.getenv("API_ID", "0") or 0)
API_HASH = os.getenv("API_HASH", "")
TOKEN = os.getenv("FILESTORE_BOT_TOKEN", "").strip()
DB_URL = os.getenv("DATABASE_URL", "")
SUPER_OWNER = int(os.getenv("OWNER_ID", "0") or 0)
FILEBOT_VERSION = "v30 (29 Sep: force-sub core REWRITE — live request-list + record fallback + exact error)"  # /version se dikhta hai
SUPER_OWNER_USERNAME = None  # owner ka @username (message aane par auto-capture)
UPDATE_LINK = os.getenv("FB_UPDATE_LINK", "https://t.me/+eSfza2-yNXpmNDk1").strip()  # link YA -100 channel ID
UPDATE_LINK_RAW = UPDATE_LINK
SYSTEM_FSUB_LINK = os.getenv("FB_SYS_FSUB_LINK", "https://t.me/+_hPJlkI9jNBmMTU1")  # owner ka LOCKED fsub


async def get_setting(key, default=None):
    try:
        v = await pool.fetchval("SELECT value FROM fb_settings WHERE key=$1", key)
    except Exception:
        return default
    return v if v is not None else default


async def set_setting(key, value):
    await pool.execute(
        "INSERT INTO fb_settings (key, value) VALUES ($1,$2) ON CONFLICT (key) DO UPDATE SET value=$2",
        key, str(value))


async def resolve_update_link():
    """UPDATE_LINK me channel ID (-100...) diya ho to bot khud
    invite link bana lega (main bot us channel me ADMIN ho to).
    Ban gaya link DB me save hota hai — restart ke baad bhi rehta hai."""
    global UPDATE_LINK
    if UPDATE_LINK_RAW.startswith("http"):
        return
    try:
        saved_for = await get_setting("update_link_for")
        saved = await get_setting("update_link")
        if saved and saved_for == UPDATE_LINK_RAW:
            UPDATE_LINK = saved
            return
    except Exception:
        pass
    try:
        cid = int(UPDATE_LINK_RAW)
        link = await _export_link(main_client, cid)
        if link:
            try:
                await set_setting("update_link", link)
                await set_setting("update_link_for", UPDATE_LINK_RAW)
            except Exception:
                pass
            UPDATE_LINK = link
            print(f"[filebot] UPDATE channel link ban gaya: {link}")
            return
    except Exception as e:
        print("[filebot] update-link fail:", str(e)[:60])
    s = UPDATE_LINK_RAW
    UPDATE_LINK = f"https://t.me/c/{s[4:]}" if s.startswith("-100") else f"https://t.me/c/{s.lstrip('-')}"


async def sys_fsub_row():
    """configured system fsub — {chat_id, title} warna None"""
    cid = await get_setting("sys_fsub_chat_id")
    if not cid or not str(cid).lstrip("-").isdigit():
        return None
    return {"chat_id": int(cid), "title": await get_setting("sys_fsub_title") or "Update Channel"}
MAX_CLONES_PER_USER = 6
MAX_FSUB = 6
MAX_TOTAL = int(os.getenv("FB_MAX_CLONES", "25") or 25)

pool = None
main_client = None
main_me = None
clone_clients = {}   # clone_id -> Client
states = {}          # key -> state dict

DEFAULT_CLONE_WELCOME = (
    "Hello {name} ✨,\n\n"
    "I am a permanent file store bot — "
    "moderators mujhe files bhejte hain, main shareable permanent link deta hoon.\n"
    "Link kholne wale ko file turant mil jayegi.\n\n"
    "To know more click menu button"
)
CLONE_HELP = (
    "✨ <b>Help Menu</b>\n\n"
    "I am a permanent file store bot.\n"
    "Moderators files store karte hain — users link se lete hain.\n\n"
    "<b>👤 Users:</b>\n"
    "/start — menu | link kholo file lo\n"
    "/menu — ye menu | /about — mere baare me\n\n"
    "<b>🔧 Moderators/Owner:</b>\n"
    "/genlink — ek file ka link (command ke baad file bhejo)\n"
    "/special_link — kai files ka EK edit hone wala link\n"
    "/batch <code>link1 link2</code> — channel range ka link\n"
    "/broadcast — (reply) sab users ko message\n"
    "/ban /unban — user control | /stats — stats"
)
MAIN_WELCOME = (
    "Hello {name} ✨,\n\n"
    "Ye <b>AnimeFlix File Store</b> ka MAIN bot hai 🤖\n\n"
    "Mujh se apna <b>khud ka FILE STORE BOT</b> banao —\n"
    "bilkul mere jaisa, TUMHARE control me!\n\n"
    "1️⃣ @BotFather se naya bot banao (/newbot)\n"
    "2️⃣ Jo token mile, yahan paste karo\n"
    "3️⃣ Tumhara bot ready! 🎉\n\n"
    "⚠️ Max 6 clones per user\n\n"
    f"\U0001F4E6 v: {FILEBOT_VERSION}"
)

# ---------------- helpers ----------------

def sk(uid, cid=None):
    return (cid or "main", uid)


def _status_str(member) -> str:
    """'ChatMemberStatus.ADMINISTRATOR' → 'ADMINISTRATOR' (enum/string dono)"""
    return str(getattr(member, "status", "") or "").upper().split(".")[-1].strip()


def _is_not_joined(member) -> bool:
    """pyrogram status ENUM ya STRING — dono me kaam kare"""
    return _status_str(member) in ("LEFT", "BANNED", "KICKED", "")


def _is_adminish(member) -> bool:
    """ADMIN / OWNER / MEMBER — teeno theek (enum + string dono)"""
    return _status_str(member) in ("ADMINISTRATOR", "OWNER", "CREATOR", "MEMBER")


def safe_handler(func):
    """handler crash ho to chup na rahe — error user ko dikhe + log ho"""
    async def wrapper(*a, **kw):
        try:
            await func(*a, **kw)
        except Exception as e:
            import traceback
            print(f"[filebot] {func.__name__} ERR:", traceback.format_exc()[-500:])
            try:
                client = a[0]
                evt = a[1] if len(a) > 1 else None
                uid = getattr(getattr(evt, "from_user", None), "id", None)
                if uid:
                    await client.send_message(
                        uid, f"⚠️ Bot me error (admin ko screenshot bhejo): {str(e)[:150]}")
            except Exception:
                pass
    return wrapper


def is_clone_mod(clone, uid):
    if uid == SUPER_OWNER or uid == clone["owner_id"]:
        return True
    return bool(pool) and uid in clone.get("_mods", set())


async def load_clone(cid):
    row = await pool.fetchrow("SELECT * FROM fb_clones WHERE id=$1", cid)
    if row:
        mods = {r["user_id"] for r in await pool.fetch(
            "SELECT user_id FROM fb_mods WHERE clone_id=$1", cid)}
        d = dict(row)
        d["_mods"] = mods
        return d
    return None


def main_link():
    return f"https://t.me/{main_me.username}" if main_me else "https://t.me/AnimeFlixFile_bot"


def clone_welcome_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🆘 HELP", callback_data="help"),
         InlineKeyboardButton("ℹ️ ABOUT", callback_data="about")],
        [InlineKeyboardButton("📢 UPDATE CHANNEL", url=UPDATE_LINK)],
        [InlineKeyboardButton("🤖 CREATE MY OWN CLONE", url=main_link())],
    ])


def about_text(clone, bot_name):
    owner_name = clone.get("owner_name") or "Owner"
    # username ho to PERMANENT t.me link (hamesha clickable) — warna tg://user fallback
    owner_un = clone.get("owner_username")
    if owner_un:
        owner_link = f"https://t.me/{owner_un.lstrip('@')}"
    else:
        owner_link = f"tg://user?id={clone['owner_id']}"
    sup_link = f"https://t.me/{SUPER_OWNER_USERNAME.lstrip('@')}" if SUPER_OWNER_USERNAME else f"tg://user?id={SUPER_OWNER}"
    return (
        "✨ ᴀʙᴏᴜᴛ ᴍᴇ\n\n"
        f"✰ ᴍʏ ɴᴀᴍᴇ: {bot_name}\n"
        f"✰ ᴄʟᴏɴᴇ ᴏꜰ: <a href='{main_link()}'>AnimeFlix File Store</a>\n"
        f"✰ ᴍʏ ᴏᴡɴᴇʀ: <a href='{owner_link}'>{owner_name}</a>\n"
        f"✰ ᴜᴘᴅᴀᴛᴇs: <a href='{UPDATE_LINK}'>AnimeFlix File Store</a>\n"
        f"✰ sᴜᴘᴘᴏʀᴛ: <a href='{sup_link}'>Lovely anime</a>\n"
        f"✰ ᴄᴏɴᴛᴀᴄᴛ ꜰᴏʀ ʙᴏᴛ ᴅᴇᴠᴇʟᴏᴘɪɴɢ: <a href='{sup_link}'>AnimeFlix</a>"
    )


class _RawEnt:
    """pyrogram ko RAW entity dena — peer-resolution ke bina
    (deploy ke baad bhi profile links HAMESHA clickable)"""
    def __init__(self, raw_entity):
        self._raw_entity = raw_entity

    async def write(self):
        return self._raw_entity


def about_raw(clone, bot_name):
    """ABOUT text + RAW entities — tg://user ka peer problem HAMESHA khatam.
    User mention: InputUser(id, 0) — bots ke liye special (server khud resolve karta hai)."""
    from pyrogram.raw.types import MessageEntityTextUrl, InputMessageEntityMentionName, InputUser
    owner_name = clone.get("owner_name") or "Owner"
    t = "✨ ᴀʙᴏᴜᴛ ᴍᴇ\n\n"
    ents = []

    def line(prefix, label, url=None, uid=None):
        nonlocal t
        off = len(t) + len(prefix)
        if url:
            ents.append(_RawEnt(MessageEntityTextUrl(offset=off, length=len(label), url=url)))
        elif uid:
            ents.append(_RawEnt(InputMessageEntityMentionName(
                offset=off, length=len(label),
                user_id=InputUser(user_id=uid, access_hash=0))))
        t = t + prefix + label + "\n"

    t = t + f"✰ ᴍʏ ɴᴀᴍᴇ: {bot_name}\n"
    line("✰ ᴄʟᴏɴᴇ ᴏꜰ: ", "AnimeFlix File Store", url=main_link())
    line("✰ ᴍʏ ᴏᴡɴᴇʀ: ", owner_name, uid=clone["owner_id"])
    line("✰ ᴜᴘᴅᴀᴛᴇs: ", "AnimeFlix File Store", url=UPDATE_LINK)
    line("✰ sᴜᴘᴘᴏʀᴛ: ", "Lovely anime", uid=SUPER_OWNER)
    line("✰ ᴄᴏɴᴛᴀᴄᴛ ꜰᴏʀ ʙᴏᴛ ᴅᴇᴠᴇʟᴏᴘɪɴɢ: ", "AnimeFlix", uid=SUPER_OWNER)
    return t.rstrip("\n"), ents


async def _send_about(client, chat_id, clone, bot_name, edit_message=None, reply_markup=None):
    """ABOUT bhejo — RAW entities se (fallback: HTML text)"""
    text, ents = about_raw(clone, bot_name)
    try:
        if edit_message is not None:
            await edit_message.edit_text(text, entities=ents,
                                         reply_markup=reply_markup,
                                         disable_web_page_preview=True)
        else:
            await client.send_message(chat_id, text, entities=ents,
                                      reply_markup=reply_markup,
                                      disable_web_page_preview=True)
    except Exception as e:
        print(f"[filebot] about-raw fail: {str(e)[:80]}")
        t = about_text(clone, bot_name)
        if edit_message is not None:
            await edit_message.edit_text(t, reply_markup=reply_markup,
                                         disable_web_page_preview=True)
        else:
            await client.send_message(chat_id, t, reply_markup=reply_markup,
                                      disable_web_page_preview=True)


def chat_ref(s):
    """'-100123' ya 'username' — jaisa pyrogram ko chahiye"""
    s = str(s)
    return int(s) if s.lstrip("-").isdigit() else s


async def _export_link(client, chat_id, join_request=False, access_hash=0, username=None):
    """peer-loss-proof invite link — deploy ke baad bhi kaam kare.
    join_request=True ho to REQUEST wali link (direct add nahi)"""
    peer = None
    try:
        peer = await client.resolve_peer(chat_id)
    except Exception:
        s = str(chat_id)
        if access_hash and s.lstrip("-").isdigit():
            try:
                from pyrogram.raw.types import InputPeerChannel
                cid = int(s[4:]) if s.startswith("-100") else abs(int(s))
                peer = InputPeerChannel(channel_id=cid, access_hash=int(access_hash))
            except Exception:
                peer = None
        if peer is None and username:
            try:
                peer = await client.resolve_peer(username)
            except Exception:
                return None
    if peer is None:
        return None
    try:
        from pyrogram.raw.functions.messages import ExportChatInvite
        if join_request:
            try:
                r = await client.invoke(ExportChatInvite(peer=peer, request_needed=True))
            except TypeError:
                r = await client.invoke(ExportChatInvite(peer=peer, creates_join_request=True))
        else:
            r = await client.invoke(ExportChatInvite(peer=peer))
        return getattr(r, "link", None)
    except Exception as e:
        print(f"[filebot] export fail: {str(e)[:70]}")
        return None


def parse_tme_link(u):
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


async def make_link(cid, chat_id, msg_ids, by, special=False):
    lid = secrets.token_urlsafe(6).replace("-", "A").replace("_", "B")[:8]
    await pool.execute(
        "INSERT INTO fb_files (link_id, clone_id, chat_id, msg_ids, created_by, special) "
        "VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (link_id) DO NOTHING",
        lid, cid, str(chat_id), json.dumps(msg_ids), by, special)
    return lid


async def get_bot_link(cid, lid):
    row = await pool.fetchrow("SELECT * FROM fb_files WHERE link_id=$1 AND clone_id=$2", lid, cid)
    return row


def link_kb(url):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 SHARE URL", url=f"https://t.me/share/url?url={url}")],
    ])


# ---------------- force sub ----------------

async def _is_member(client, r, uid):
    """True=member | False=nahi | None=pata nahi.
    ORDER bilkul SHURU wale jaisa: (1) seedha direct check,
    (2) username (public), (3) access_hash (private) — deploy ke baad bhi kaam kare.
    USER_NOT_PARTICIPANT = member NAHI = False."""
    rd = dict(r) if not isinstance(r, dict) else r

    # ---- 1) DIRECT CHECK (jaise PEHLE tha) + hash backfill (self-heal) ----
    try:
        chat_id = rd.get("chat_id")
        ref = chat_ref(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id
        m = await client.get_chat_member(ref, uid)
        try:
            p = await client.resolve_peer(ref)
            ah2 = int(getattr(p, "access_hash", 0) or 0)
            if ah2:
                await pool.execute(
                    "UPDATE fb_fsub SET access_hash=$1 WHERE chat_id=$2",
                    ah2, str(chat_id))
                print(f"[filebot] fsub HEALED (hash saved): {chat_id}")
        except Exception:
            pass
        return not _is_not_joined(m)
    except Exception as e:
        if "PARTICIPANT" in str(e).upper():
            return False
        if "PEER_ID_INVALID" not in str(e).upper():
            print(f"[filebot] member-direct err: chat={rd.get('chat_id')} err={str(e)[:70]}")

    # ---- 2) username se (public channels) ----
    uname = rd.get("username")
    if uname:
        try:
            m = await client.get_chat_member(uname, uid)
            return not _is_not_joined(m)
        except Exception as e:
            if "PARTICIPANT" in str(e).upper():
                return False

    # ---- 3) access_hash se (private channels) ----
    try:
        s = str(rd.get("chat_id"))
        cid = int(s[4:]) if s.startswith("-100") else abs(int(s))
        ah = int(rd.get("access_hash") or 0)
        if ah:
            from pyrogram.raw.functions.channels import GetParticipant
            from pyrogram.raw.types import InputChannel, InputUser
            await client.invoke(GetParticipant(
                channel=InputChannel(channel_id=cid, access_hash=ah),
                participant=InputUser(user_id=uid, access_hash=0)))
            return True
    except Exception as e:
        if "PARTICIPANT" in str(e).upper():
            return False
        print(f"[filebot] member-check err: chat={rd.get('chat_id')} err={str(e)[:70]}")
    return None


async def _peer_for(client, rd):
    """row ke liye peer banao — resolve nahi ho to access_hash se (deploy-proof)"""
    s = str(rd.get("chat_id"))
    try:
        return await client.resolve_peer(s)
    except Exception:
        ah = int(rd.get("access_hash") or 0)
        if ah and s.lstrip("-").isdigit():
            from pyrogram.raw.types import InputPeerChannel
            cid = int(s[4:]) if s.startswith("-100") else abs(int(s))
            return InputPeerChannel(channel_id=cid, access_hash=ah)
    return None


async def _requests_lookup(client, rd, uid):
    """LIVE PENDING join-request list (APPROVAL LIST) — bot APPROVE nahi karta, sirf dekhta hai.
    return (True/False/None, err_text): True=mila, False=nahi mila, None=API error."""
    try:
        peer = await _peer_for(client, rd)
        if peer is None:
            return None, "peer-missing (REPAIR karo)"
        from pyrogram.raw.functions.messages import GetChatInviteImporters
        from pyrogram.raw.types import InputUserEmpty
        r = await client.invoke(GetChatInviteImporters(
            peer=peer, offset_date=0, offset_user=InputUserEmpty(),
            limit=100, requested=True))
        for imp in (getattr(r, "importers", None) or []):
            u = getattr(imp, "user", None)
            if (getattr(imp, "user_id", 0) or (getattr(u, "id", 0) if u else 0)) == uid:
                return True, ""
        return False, ""
    except Exception as e:
        return None, str(e)[:70]


async def _in_requests(client, rd, uid):
    """PENDING REQUEST me user? (OWNER RULE: request bheje user ko content milta hai)
    1) LIVE API (bot channel ka ADMIN ho to chalti hai)
    2) LIVE fail → handler-record fallback: ChatJoinRequestHandler ne request save ki thi
       (bot admin hai to request aayi thi — deploy ke baad bhi record DB me safe hai)
    True=mila | False=nahi mila | None=dono tarike fail"""
    ok, err = await _requests_lookup(client, rd, uid)
    if ok is not None:
        return ok
    if err:
        print(f"[filebot] requests-live err: chat={rd.get('chat_id')} {err}")
    try:
        row = await pool.fetchrow(
            "SELECT 1 FROM fb_jreq WHERE chat_id=$1 AND user_id=$2",
            str(rd.get("chat_id")), uid)
        if row:
            return True
    except Exception:
        pass
    return None


async def fsub_check_user(client, rd, uid):
    """FORCE-SUB CHECK — PURA DOBARA LIKHA (v3), OWNER ke rules:
    1) PENDING REQUEST (approval list) me user?  → CONTENT
    2) MEMBER/SUBSCRIBER? (direct → username → hash) → CONTENT
    3) verify hi nahi ho paya (API error)         → CONTENT (member ko lock nahi karenge)
    4) request nahi + member nahi                  → JOIN NOW
    True=content | False=JOIN NOW"""
    rd = dict(rd) if not isinstance(rd, dict) else rd
    # ---- 1) PENDING REQUESTS (live → fallback record) ----
    r = await _in_requests(client, rd, uid)
    if r is True:
        return True
    # ---- 2) MEMBER CHECK (shuru jaisa: direct → username → hash) ----
    m = await _is_member(client, rd, uid)
    if m is True:
        # member ban gaya — purana request-record saaf kar do (stale se bacho)
        try:
            await pool.execute("DELETE FROM fb_jreq WHERE chat_id=$1 AND user_id=$2",
                               str(rd.get("chat_id")), uid)
        except Exception:
            pass
        return True
    if m is False:
        return False
    return True


async def fsub_not_joined(cid, uid, client):
    """JOIN NOW dikhane wale fsub rows — SYSTEM (locked) sabse pehle.
    Bot APPROVE kabhi nahi karta — sirf: request-list → member → JOIN NOW."""
    not_joined = []
    # ---- SYSTEM force sub (owner ka — LOCKED) ----
    sysr = await sys_fsub_row()
    if sysr and main_client:
        uname = await get_setting("sys_fsub_username")
        ah = int(await get_setting("sys_fsub_access_hash", 0) or 0)
        pseudo = {"chat_id": str(sysr["chat_id"]), "username": uname,
                  "access_hash": ah, "join_request": False}
        if not await fsub_check_user(main_client, pseudo, uid):
            not_joined.append({"chat_id": str(sysr["chat_id"]),
                               "title": sysr["title"], "link": SYSTEM_FSUB_LINK,
                               "join_request": False, "system": True,
                               "username": uname, "access_hash": ah})
    rows = await pool.fetch("SELECT * FROM fb_fsub WHERE clone_id=$1", cid)
    for r in rows:
        if not await fsub_check_user(client, r, uid):
            not_joined.append(r)
    names = [str(x.get("title") or x.get("chat_id")) if isinstance(x, dict) else str(x["chat_id"]) for x in not_joined]
    print(f"[filebot] FSUB CHECK: clone={cid} user={uid} channels={len(rows)} "
          f"blocked={len(not_joined)} list={names}")
    return not_joined


async def join_buttons(cid, uid, client, lid):
    rows = await fsub_not_joined(cid, uid, client)
    if not rows:
        return None, []
    kb = []
    for r in rows:
        rd = dict(r) if not isinstance(r, dict) else r
        link = rd.get("link")
        title = rd.get("title") or "Channel"
        if not link:
            # link regenerate (peer-loss-proof: hash + username ke saath)
            try:
                link = await _export_link(client, chat_ref(rd.get("chat_id")),
                                          join_request=bool(rd.get("join_request")),
                                          access_hash=int(rd.get("access_hash") or 0),
                                          username=rd.get("username"))
                if link:
                    await pool.execute(
                        "UPDATE fb_fsub SET link=$1 WHERE clone_id=$2 AND chat_id=$3",
                        link, cid, rd.get("chat_id"))
            except Exception:
                link = None
        if link:
            kb.append([InlineKeyboardButton("JOIN NOW", url=link)])
        else:
            kb.append([InlineKeyboardButton(f"⚠️ {title} — link missing", callback_data="noop")])
    kb.append([InlineKeyboardButton("✅ TRY AGAIN", callback_data=f"try:{lid}")])
    return InlineKeyboardMarkup(kb), rows


# ---------------- delivery ----------------

async def _delete_later(client, msgs, sec):
    await asyncio.sleep(sec)
    for m in msgs:
        try:
            await client.delete_messages(m.chat.id, m.id)
        except Exception:
            pass


async def deliver(cid, uid, lid, client):
    row = await get_bot_link(cid, lid)
    if not row:
        await client.send_message(uid, "❌ Link invalid hai ya delete ho chuka.")
        return
    kb, frows = await join_buttons(cid, uid, client, lid)
    if kb:
        names = "\n".join(
            f"• { (dict(r).get('title') or 'Channel') if not isinstance(r, dict) else (r.get('title') or 'Channel') }"
            for r in frows)
        msg = ("🔒 <b>Pehle ye channel(s) join karo — phir TRY AGAIN dabao!</b>\n\n"
               f"{names}")
        # ---- OWNER/MOD ke liye AUTO-DEBUG (kaunsa channel kyu block ho raha) ----
        try:
            c2 = await load_clone(cid)
            if uid == SUPER_OWNER or (c2 and uid == c2["owner_id"]) or (c2 and is_clone_mod(c2, uid)):
                dbg = []
                for r in frows:
                    rd = dict(r) if not isinstance(r, dict) else r
                    mem = await _is_member(client, rd, uid)
                    rok, rerr = await _requests_lookup(client, rd, uid)
                    rtxt = str(rok) if rok is not None else f"ERR:{rerr}"
                    dbg.append(f"🔍 {rd.get('title') or rd.get('chat_id')}: "
                               f"member={mem} request={rtxt} "
                               f"flag={'on' if rd.get('join_request') else 'off'} "
                               f"hash={'✅' if int(rd.get('access_hash') or 0) else '❌'}")
                msg += "\n\n" + "\n".join(dbg)
        except Exception as e:
            print(f"[filebot] owner-dbg err: {str(e)[:60]}")
        await client.send_message(uid, msg, reply_markup=kb)
        return
    clone = await load_clone(cid)
    ad = clone["auto_delete"] if clone else 0
    msg_ids = json.loads(row["msg_ids"])
    sent = []
    for mid in msg_ids:
        for attempt in range(2):
            try:
                m = await client.copy_message(
                    chat_id=uid, from_chat_id=chat_ref(row["chat_id"]), message_id=mid)
                sent.append(m)
                break
            except FloodWait as e:
                await asyncio.sleep(min(int(e.value), 20))
            except Exception as e:
                print(f"[filebot] deliver fail {mid}: {str(e)[:80]}")
                break
    if ad and sent:
        asyncio.create_task(_delete_later(client, sent, ad))


# =========================================================
#                    MAIN BOT (clone factory)
# =========================================================

async def main_on_message(_, m):
    try:
        uid = m.from_user.id if m.from_user else 0
        name = (m.from_user.first_name if m.from_user else "friend") or "friend"
    except Exception:
        return
    # ---- SUPER OWNER ka @username auto-capture (permanent profile links ke liye) ----
    global SUPER_OWNER_USERNAME
    if uid == SUPER_OWNER and getattr(m.from_user, "username", None):
        if SUPER_OWNER_USERNAME != m.from_user.username:
            SUPER_OWNER_USERNAME = m.from_user.username
            try:
                await set_setting("super_owner_username", SUPER_OWNER_USERNAME)
            except Exception:
                pass
    text = (m.text or m.caption or "").strip()

    if text.startswith("/start"):
        states.pop(sk(uid), None)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🤖 CREATE MY OWN CLONE", callback_data="manage")],
            [InlineKeyboardButton("📂 MY CLONES", callback_data="manage"),
             InlineKeyboardButton("📢 UPDATE CHANNEL", url=UPDATE_LINK)],
        ])
        await m.reply(MAIN_WELCOME.format(name=name), reply_markup=kb, disable_web_page_preview=True)
        return

    if text.startswith("/menu") or text.startswith("/help"):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🤖 CREATE MY OWN CLONE", callback_data="manage")],
            [InlineKeyboardButton("📂 MY CLONES", callback_data="manage")],
        ])
        await m.reply("🤖 <b>MAIN BOT Menu</b>\n\nMain sirf <b>CLONES</b> banata hoon!\n"
                      "Neeche button se apna file store bot banao (max 6).", reply_markup=kb)
        return

    if text.startswith("/about"):
        sup_link = f"https://t.me/{SUPER_OWNER_USERNAME.lstrip('@')}" if SUPER_OWNER_USERNAME else f"tg://user?id={SUPER_OWNER}"
        await m.reply(
            f"✨ ᴀʙᴏᴜᴛ ᴍᴇ\n\n✰ ᴍʏ ɴᴀᴍᴇ: {main_me.first_name if main_me else 'AnimeFlix File Store'}\n"
            f"✰ ᴍʏ ᴏᴡɴᴇʀ: <a href='{sup_link}'>Lovely anime</a>\n"
            f"✰ ᴜᴘᴅᴀᴛᴇs: <a href='{UPDATE_LINK}'>Update Channel</a>\n"
            f"✰ ᴄᴏɴᴛᴀᴄᴛ ꜰᴏʀ ʙᴏᴛ ᴅᴇᴠᴇʟᴏᴘɪɴɢ: <a href='{sup_link}'>AnimeFlix</a>")
        return

    if text.startswith("/version"):
        await m.reply(f"📦 <b>Code version:</b> <code>{FILEBOT_VERSION}</code>")
        return

    if text.startswith("/set_sys"):
        if uid != SUPER_OWNER:
            return
        chats = []
        async for d in main_client.get_dialogs(limit=100):
            chat = d.chat
            if chat.type in ("channel", "supergroup") and chat.id:
                try:
                    member = await main_client.get_chat_member(chat.id, "me")
                    if _is_adminish(member):
                        chats.append(chat)
                except Exception:
                    pass
        if not chats:
            await m.reply("❌ Koi admin channel nahi mila — pehle mujhe (@AnimeFlixFile_bot) apne channel me ADMIN banao!")
            return
        kb = [[InlineKeyboardButton(f"🔒 {c.title or c.id}", callback_data=f"syspick:{c.id}")]
              for c in chats[:15]]
        await m.reply(
            "🔒 <b>SYSTEM FORCE-SUB setup</b>\nApna wo channel chuno jo SAB clones me "
            "hamesha LOCKED rahega (koi owner remove nahi kar payega):",
            reply_markup=InlineKeyboardMarkup(kb))
        return

    st = states.get(sk(uid))
    if not st:
        return

    # ---- fsub REPAIR (forward se purane channel ki permanent ID save) ----
    if st.get("flow") == "fsubfix":
        cid = st["cid"]
        fchat = getattr(m, "forward_from_chat", None)
        if not fchat:
            states.pop(sk(uid), None)
            await m.reply("❌ CHANNEL ka message FORWARD karna hai (copy nahi). FORCE SUB → 🔧 REPAIR se dobara kholo.")
            return
        ah = 0
        try:
            p = await main_client.resolve_peer(fchat.id)
            ah = int(getattr(p, "access_hash", 0) or 0)
        except Exception:
            pass
        un = getattr(fchat, "username", None)
        sysr = await sys_fsub_row()
        if sysr and str(sysr["chat_id"]) == str(fchat.id):
            if not ah:
                await m.reply("⚠️ ID nahi nikli — dobara forward karo.")
                return
            await set_setting("sys_fsub_access_hash", ah)
            if un:
                await set_setting("sys_fsub_username", un)
            await m.reply(f"✅ SYSTEM channel REPAIR DONE: <b>{fchat.title}</b>\n\nAur channel forward karo ya /start")
            return
        row = await pool.fetchrow("SELECT * FROM fb_fsub WHERE clone_id=$1 AND chat_id=$2",
                                  cid, str(fchat.id))
        if not row:
            await m.reply("❌ Ye channel is clone ki force-sub list me nahi hai — ADD CHANNEL se add karo.\n\nAur repair karne ke liye agla channel forward karo.")
            return
        if not ah:
            await m.reply("⚠️ ID nahi nikli — dobara forward karo.")
            return
        await pool.execute(
            "UPDATE fb_fsub SET access_hash=$1, username=$2 WHERE clone_id=$3 AND chat_id=$4",
            ah, un, cid, str(fchat.id))
        await m.reply(f"✅ REPAIR DONE: <b>{fchat.title}</b>\n<code>ID: {fchat.id}</code>\nAb is channel ka JOIN NOW hamesha kaam karega!\n\nAur channel forward karo ya /start")
        return

    # ---- force-sub channel add (link/username paste ya FORWARD kiya) ----
    if st.get("flow") == "fsubchat":
        cid = st["cid"]
        states.pop(sk(uid), None)
        fchat = getattr(m, "forward_from_chat", None)
        if fchat is not None:
            # FORWARD se add — sabse aasan tarika!
            bot_client = clone_clients.get(cid)
            if not bot_client:
                await m.reply("⚠️ Clone bot offline — 5 min baad dobara try karo (auto-restart hota hai)")
                return
            ah = 0
            try:
                p = await main_client.resolve_peer(fchat.id)
                ah = int(getattr(p, "access_hash", 0) or 0)
            except Exception:
                pass
            ok_admin = False
            try:
                member = await bot_client.get_chat_member(fchat.id, "me")
                ok_admin = _is_adminish(member)
            except Exception:
                if ah:
                    try:
                        from pyrogram.raw.functions.channels import GetParticipant
                        from pyrogram.raw.types import InputChannel, InputPeerSelf
                        s0 = str(fchat.id)
                        cid0 = int(s0[4:]) if s0.startswith("-100") else abs(int(s0))
                        r0 = await bot_client.invoke(GetParticipant(
                            channel=InputChannel(channel_id=cid0, access_hash=ah),
                            participant=InputPeerSelf()))
                        part = getattr(r0, "participant", None)
                        ok_admin = part is not None and type(part).__name__ in (
                            "ChannelParticipantAdmin", "ChannelParticipantCreator")
                    except Exception:
                        ok_admin = False
            if not ok_admin:
                clone_row = await load_clone(cid)
                bname = f"@{clone_row['bot_username']}" if clone_row and clone_row.get("bot_username") else "clone bot"
                await m.reply(
                    f"⚠️ <b>{bname}</b> (CLONE bot) is channel me ADMIN nahi hai!\n\n"
                    f"👉 Bot ko channel me ADMIN banao — phir dobara forward karo!")
                return
            cnt = await pool.fetchval("SELECT count(*) FROM fb_fsub WHERE clone_id=$1", cid)
            if cnt >= MAX_FSUB - 1:
                await m.reply(f"⚠️ Limit full — max {MAX_FSUB - 1} channels (1 system LOCKED slot)")
                return
            un = getattr(fchat, "username", None)
            title = fchat.title or "Channel"
            if un:
                pub = f"https://t.me/{un}"
                await pool.execute(
                    "INSERT INTO fb_fsub (clone_id, chat_id, title, link, join_request, access_hash, username) "
                    "VALUES ($1,$2,$3,$4,false,$5,$6) ON CONFLICT (clone_id, chat_id) DO UPDATE SET "
                    "title=$3, link=$4, access_hash=$5, username=$6",
                    cid, str(fchat.id), title, pub, ah, un)
                await m.reply(f"✅ Force-sub ADD: {title}\n🔗 {pub}")
                return
            # private → pehle row insert (permanent ID ke saath), phir mode poochho
            await pool.execute(
                "INSERT INTO fb_fsub (clone_id, chat_id, title, link, join_request, access_hash) "
                "VALUES ($1,$2,$3,NULL,false,$4) ON CONFLICT (clone_id, chat_id) DO UPDATE SET "
                "title=$3, access_hash=$4",
                cid, str(fchat.id), title, ah)
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📨 JOIN REQUEST MODE", callback_data=f"fsubmode:{cid}:{fchat.id}:1")],
                [InlineKeyboardButton("🔓 NORMAL MODE", callback_data=f"fsubmode:{cid}:{fchat.id}:0")],
            ])
            await m.reply(
                f"Ye PRIVATE channel hai (<b>{title}</b>) — mode chuno:\n\n"
                "📨 <b>Join Request</b>: users request bhejenge, request ke baad content milega\n"
                "🔓 <b>Normal</b>: seedha join ho jayenge", reply_markup=kb)
            return
        raw = text.strip()
        ref = None
        mu = re.match(r"^@([A-Za-z0-9_]{4,})$", raw)
        mu2 = re.match(r"^(?:https?://)?t\.me/([A-Za-z0-9_]{4,})(?:/.*)?$", raw)
        mc = re.match(r"^(?:https?://)?t\.me/c/(\d+)", raw)
        if mu:
            ref = mu.group(1)
        elif mu2:
            ref = mu2.group(1)
        elif mc:
            ref = int("-100" + mc.group(1))
        elif raw.lstrip("-").isdigit():
            ref = int(raw)
        if not ref:
            await m.reply("❌ Samajh nahi aaya — @username, t.me link ya -100 ID bhejo")
            return
        bot_client = clone_clients.get(cid)
        if not bot_client:
            await m.reply("⚠️ Clone bot offline — 5 min baad dobara try karo (auto-restart hota hai)")
            return
        try:
            chat = await bot_client.get_chat(ref)
            member = await bot_client.get_chat_member(chat.id, "me")
        except Exception as e:
            await m.reply("❌ Channel nahi mila ya bot usme add nahi hai.\n"
                          f"Detail: {str(e)[:100]}\n\nBot ko channel me ADMIN bana ke dobara bhejo!")
            return
        mstatus = str(getattr(member, "status", "unknown"))
        if not _is_adminish(member):
            clone_row = await load_clone(cid)
            bname = f"@{clone_row['bot_username']}" if clone_row and clone_row.get("bot_username") else "clone bot"
            await m.reply(
                f"⚠️ <b>{bname}</b> (CLONE bot) is channel me ADMIN nahi hai!\n"
                f"(status jo mila: <code>{mstatus}</code>)\n\n"
                f"👉 Channel ke admins me <b>{bname}</b> ko admin banao —\n"
                "MAIN bot @AnimeFlixFile_bot NAHI — CLONE bot!\n"
                "Phir link dobara bhejo!")
            return
        cnt = await pool.fetchval("SELECT count(*) FROM fb_fsub WHERE clone_id=$1", cid)
        if cnt >= MAX_FSUB - 1:
            await m.reply(f"⚠️ Limit full — max {MAX_FSUB - 1} channels (1 system LOCKED slot)")
            return
        ah = 0
        try:
            p = await bot_client.resolve_peer(chat.id)
            ah = int(getattr(p, "access_hash", 0) or 0)
        except Exception:
            pass
        link = None
        try:
            link = await _export_link(bot_client, chat.id, access_hash=ah)
        except Exception:
            pass
        if chat.username:
            pub = f"https://t.me/{chat.username}"
            await pool.execute(
                "INSERT INTO fb_fsub (clone_id, chat_id, title, link, join_request, access_hash, username) "
                "VALUES ($1,$2,$3,$4,false,$5,$6) ON CONFLICT (clone_id, chat_id) DO NOTHING",
                cid, str(chat.id), chat.title or "Channel", pub, ah, chat.username)
            await m.reply(f"✅ Force-sub ADD: {chat.title}\n🔗 {pub}")
            return
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📨 JOIN REQUEST MODE", callback_data=f"fsubmode:{cid}:{chat.id}:1")],
            [InlineKeyboardButton("🔓 NORMAL MODE", callback_data=f"fsubmode:{cid}:{chat.id}:0")],
        ])
        extra = f"\n\n🔗 Bot ka banaya link: <code>{link}</code>" if link else ""
        await m.reply(
            "Ye PRIVATE channel hai — mode chuno:\n\n"
            "📨 <b>Join Request</b>: users request bhejenge, tum approve karoge\n"
            "🔓 <b>Normal</b>: seedha join ho jayenge" + extra, reply_markup=kb)
        return

    # ---- token receive ----
    if st.get("flow") == "token":
        states.pop(sk(uid), None)
        tok = text.strip()
        if not re.match(r"^\d{6,}:[A-Za-z0-9_-]{30,}$", tok):
            await m.reply("❌ Ye token sahi nahi lag raha. @BotFather se jo token aaya wahi bhejo.")
            return
        cnt = await pool.fetchval("SELECT count(*) FROM fb_clones WHERE owner_id=$1", uid)
        if cnt >= MAX_CLONES_PER_USER:
            await m.reply(f"⚠️ Tumhare already {cnt} clones hain — max {MAX_CLONES_PER_USER} allowed!")
            return
        total = await pool.fetchval("SELECT count(*) FROM fb_clones WHERE active=true")
        if total >= MAX_TOTAL:
            await m.reply("⚠️ Server full hai — thodi der baad try karo.")
            return
        if await pool.fetchval("SELECT 1 FROM fb_clones WHERE token=$1", tok):
            await m.reply("❌ Ye token pehle se kisi clone me use ho raha hai.")
            return
        status = await m.reply("⏳ Bot check ho raha hai...")
        try:
            c = Client(f"clone_{secrets.token_hex(4)}", api_id=API_ID, api_hash=API_HASH,
                       bot_token=tok, in_memory=True)
            await c.start()
            me = await c.get_me()
            await c.stop()
        except Exception as e:
            await status.edit_text(f"❌ Token kaam nahi kar raha: {str(e)[:120]}")
            return
        row = await pool.fetchrow(
            "INSERT INTO fb_clones (owner_id, owner_name, token, bot_username) "
            "VALUES ($1,$2,$3,$4) RETURNING *", uid, name, tok, me.username)
        ok = await boot_clone(dict(row))
        if not ok:
            await asyncio.sleep(6)
            ok = await boot_clone(dict(row))
        if not ok:
            await status.edit_text("⚠️ Token theek hai par bot abhi start nahi ho paya — thodi der baad /start karke Manage kholo.")
            return
        await status.edit_text(
            f"🎉 <b>CLONE READY!</b>\n\n👤 Owner: {name}\n🤖 Bot: @{me.username}\n\n"
            f"Ab us bot ko kholo: <a href='https://t.me/{me.username}'>@{me.username}</a> — /start bhejo!\n"
            f"Settings ke liye yahan <b>MY CLONES</b> kholo.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("📂 MY CLONES", callback_data="manage")]]))
        return

    # ---- start msg text ----
    if st.get("flow") == "startmsg":
        cid = st["cid"]
        states.pop(sk(uid), None)
        await pool.execute("UPDATE fb_clones SET start_msg=$1 WHERE id=$2", text, cid)
        await m.reply("✅ Start message save ho gaya!")
        return

    # ---- start photo ----
    if st.get("flow") == "startphoto":
        cid = st["cid"]
        states.pop(sk(uid), None)
        if m.photo:
            await pool.execute("UPDATE fb_clones SET start_photo=$1 WHERE id=$2",
                               m.photo.file_id, cid)
            await m.reply("✅ Start photo save ho gayi!")
        else:
            await m.reply("❌ Photo nahi mili — dobara try karo.")
        return

    # ---- fsub manual link fallback ----
    if st.get("flow") == "fsublink":
        cid, chat_id, jr = st["cid"], st["chat_id"], st.get("jr", False)
        states.pop(sk(uid), None)
        link = text.strip()
        if not link.startswith("https://t.me/"):
            await m.reply("❌ Link https://t.me/... se shuru hona chahiye. Dobara Add Channel karo.")
            return
        bot_client = clone_clients.get(cid)
        ah = 0
        if bot_client:
            try:
                p = await bot_client.resolve_peer(chat_ref(str(chat_id)))
                ah = int(getattr(p, "access_hash", 0) or 0)
            except Exception:
                ah = 0
        await pool.execute(
            "INSERT INTO fb_fsub (clone_id, chat_id, title, link, join_request, access_hash) "
            "VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (clone_id, chat_id) DO UPDATE SET link=$4, "
            "access_hash=CASE WHEN EXCLUDED.access_hash>0 THEN EXCLUDED.access_hash ELSE fb_fsub.access_hash END",
            cid, str(chat_id), st.get("title") or "Channel", link, jr, ah)
        if ah:
            await m.reply("✅ Force-sub channel add ho gaya!")
        else:
            await m.reply(
                "✅ Force-sub channel add ho gaya!\n\n"
                "⚠️ <b>ZAROORI KAAM:</b> Is channel ki permanent ID abhi save NAHI hui —\n"
                "<b>FORCE SUB → 🔧 REPAIR</b> kholo aur is channel ka koi bhi message\n"
                "<b>FORWARD</b> kar do, warna is channel ka JOIN NOW users ko nahi dikhega!")
        return

    # ---- moderator add ----
    if st.get("flow") == "modadd":
        cid = st["cid"]
        states.pop(sk(uid), None)
        mid = text.split()[0] if text.split() else ""
        if not mid.lstrip("-").isdigit():
            await m.reply("❌ Sirf numeric ID bhejo (/id se milega).")
            return
        await pool.execute("INSERT INTO fb_mods (clone_id, user_id) VALUES ($1,$2) ON CONFLICT DO NOTHING",
                           cid, int(mid))
        await m.reply(f"✅ Moderator add: <code>{mid}</code>")
        return

    # ---- auto delete ----
    if st.get("flow") == "autodel":
        cid = st["cid"]
        states.pop(sk(uid), None)
        if not text.isdigit():
            await m.reply("❌ Sirf number (seconds) bhejo — 0 = off")
            return
        await pool.execute("UPDATE fb_clones SET auto_delete=$1 WHERE id=$2", int(text), cid)
        await m.reply(f"✅ Auto-delete: {int(text)} sec" if int(text) else "✅ Auto-delete OFF")
        return


def clone_menu_kb(cid, clone):
    rows = [
        [InlineKeyboardButton("💬 START MSG", callback_data=f"cfg:msg:{cid}"),
         InlineKeyboardButton("🔒 FORCE SUB", callback_data=f"cfg:fsub:{cid}")],
        [InlineKeyboardButton("👮 MODERATORS", callback_data=f"cfg:mods:{cid}"),
         InlineKeyboardButton("⏱ AUTO DELETE", callback_data=f"cfg:ad:{cid}")],
        [InlineKeyboardButton("📊 STATS", callback_data=f"cfg:stats:{cid}"),
         InlineKeyboardButton("🗑 DELETE", callback_data=f"cfg:del:{cid}")],
        [InlineKeyboardButton("🔙 BACK", callback_data="manage")],
    ]
    return InlineKeyboardMarkup(rows)


async def main_on_callback(_, cq):
    data = cq.data or ""
    uid = cq.from_user.id
    try:
        if data == "noop":
            await cq.answer("Link missing — owner ko bolo bot ko admin banaye")
            return
        if data == "syslock":
            await cq.answer("🔒 Ye SYSTEM force-sub hai — isko koi remove NAHI kar sakta!", show_alert=True)
            return
        if data.startswith("syspick:"):
            chat_id = int(data.split(":")[1])
            title = "Update Channel"
            ah = 0
            uname = None
            try:
                chat = await main_client.get_chat(chat_id)
                title = chat.title or "Update Channel"
                uname = getattr(chat, "username", None)
            except Exception:
                chat = None
            try:
                p = await main_client.resolve_peer(chat_id)
                ah = int(getattr(p, "access_hash", 0) or 0)
            except Exception:
                pass
            await set_setting("sys_fsub_chat_id", chat_id)
            await set_setting("sys_fsub_title", title)
            await set_setting("sys_fsub_access_hash", ah)
            if uname:
                await set_setting("sys_fsub_username", uname)
            await pool.execute("DELETE FROM fb_fsub WHERE chat_id=$1", str(chat_id))
            await cq.message.edit_text(
                f"✅ <b>SYSTEM force-sub set: {title}</b>\n\n"
                f"🔗 {SYSTEM_FSUB_LINK}\n\n"
                "Ab ye channel HAR clone me LOCKED rahega —\n"
                "join kiye bina kisi ko file nahi milegi! 🔒")
            await cq.answer()
            return
        if data == "manage":
            clones = await pool.fetch("SELECT * FROM fb_clones WHERE owner_id=$1 ORDER BY id", uid)
            if uid == SUPER_OWNER:
                clones = await pool.fetch("SELECT * FROM fb_clones ORDER BY id")
            rows = []
            for c in clones:
                rows.append([InlineKeyboardButton(
                    f"🤖 {c['bot_username'] or ('#' + str(c['id']))}",
                    callback_data=f"clone:{c['id']}")])
            if len(clones) < MAX_CLONES_PER_USER:
                rows.append([InlineKeyboardButton("➕ CREATE NEW CLONE", callback_data="newclone")])
            rows.append([InlineKeyboardButton("🔙 BACK", callback_data="backhome")])
            await cq.message.edit_text(
                f"📂 <b>My Clones</b> ({len(clones)}/{MAX_CLONES_PER_USER})\n"
                "Clone pe click karke settings kholo:", 
                reply_markup=InlineKeyboardMarkup(rows))
            await cq.answer()
            return

        if data == "newclone":
            states[sk(uid)] = {"flow": "token"}
            await cq.message.edit_text(
                "🤖 <b>CREATE YOUR OWN CLONE</b>\n\n"
                "1️⃣ @BotFather kholo → /newbot bhejo\n"
                "2️⃣ Naam aur username set karo\n"
                "3️⃣ Jo <b>token</b> mile (123456:ABC-xyz... format) —\n"
                "   ab yahan paste kar do!\n\n"
                "⏳ Token ka intezaar hai...")
            await cq.answer()
            return

        if data == "backhome":
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🤖 CREATE MY OWN CLONE", callback_data="manage")],
                [InlineKeyboardButton("📂 MY CLONES", callback_data="manage"),
                 InlineKeyboardButton("📢 UPDATE CHANNEL", url=UPDATE_LINK)],
            ])
            await cq.message.edit_text(MAIN_WELCOME.format(name=cq.from_user.first_name or "friend"),
                                       reply_markup=kb, disable_web_page_preview=True)
            await cq.answer()
            return

        # clone:X
        if data.startswith("clone:"):
            cid = int(data.split(":")[1])
            clone = await load_clone(cid)
            if not clone or (clone["owner_id"] != uid and uid != SUPER_OWNER):
                await cq.answer("❌ Ye tumhara clone nahi hai!", show_alert=True)
                return
            await cq.message.edit_text(
                f"🪄 <b>Customize Clone</b>\n"
                f"➜ Name: @{clone['bot_username']}\n"
                f"➜ Owner: {clone['owner_name']}\n\n"
                "Configure Your Clone Settings Using Given Buttons",
                reply_markup=clone_menu_kb(cid, clone))
            await cq.answer()
            return

        # cfg:xxx:cid
        parts = data.split(":")
        if parts[0] == "cfg":
            cid = int(parts[2])
            clone = await load_clone(cid)
            if not clone or (clone["owner_id"] != uid and uid != SUPER_OWNER):
                await cq.answer("❌ Ye tumhara clone nahi hai!", show_alert=True)
                return
            what = parts[1]

            if what == "msg":
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✏️ Edit", callback_data=f"msg:edit:{cid}"),
                     InlineKeyboardButton("👀 See", callback_data=f"msg:see:{cid}"),
                     InlineKeyboardButton("♻️ Default", callback_data=f"msg:def:{cid}")],
                    [InlineKeyboardButton("🖼 PHOTO", callback_data=f"photo:{cid}")],
                    [InlineKeyboardButton("🔙 BACK", callback_data=f"clone:{cid}")],
                ])
                await cq.message.edit_text("💬 <b>Start Message</b>\nApne clone ka welcome text set karo.",
                                           reply_markup=kb)
            elif what == "photo":
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ Add", callback_data=f"photo:add:{cid}"),
                     InlineKeyboardButton("🗑 Delete", callback_data=f"photo:del:{cid}")],
                    [InlineKeyboardButton("🔙 BACK", callback_data=f"cfg:msg:{cid}")],
                ])
                await cq.message.edit_text("🖼 <b>Start Photo</b>\nWelcome message ke saath photo.",
                                           reply_markup=kb)
            elif what == "fsub":
                frows = await pool.fetch("SELECT * FROM fb_fsub WHERE clone_id=$1", cid)
                sysr = await sys_fsub_row()
                kb = [[InlineKeyboardButton(
                    f"🔒 {sysr['title'] if sysr else 'System Channel'} — LOCKED 🔒",
                    callback_data="syslock")]]
                for r in frows:
                    kb.append([InlineKeyboardButton(
                        f"❌ {r['title'] or r['chat_id']}" + (" (join-req)" if r["join_request"] else ""),
                        callback_data=f"fsubdel:{cid}:{r['chat_id'].lstrip('-')}")])
                if len(frows) < MAX_FSUB - 1:
                    kb.append([InlineKeyboardButton("➕ ADD CHANNEL", callback_data=f"fsubadd:{cid}")])
                kb.append([InlineKeyboardButton("🔧 REPAIR (forward se)", callback_data=f"fsubfix:{cid}")])
                kb.append([InlineKeyboardButton("🔙 BACK", callback_data=f"clone:{cid}")])
                await cq.message.edit_text(
                    f"🔒 <b>Force Sub ({len(frows) + 1}/{MAX_FSUB} — 1 LOCKED)</b>\n"
                    "Users ko file tabhi milegi jab wo ye channels join kare.\n"
                    "🔒 wala SYSTEM channel hai — isko koi remove nahi kar sakta!\n"
                    "➕ Add karne ke liye bot ko pehle channel me <b>admin</b> banao!",
                    reply_markup=InlineKeyboardMarkup(kb))
            elif what == "mods":
                mrows = await pool.fetch("SELECT user_id FROM fb_mods WHERE clone_id=$1", cid)
                lst = "\n".join(f"👮 <code>{r['user_id']}</code>" for r in mrows) or "— koi nahi"
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ ADD (ID bhejo)", callback_data=f"modadd:{cid}")],
                    [InlineKeyboardButton("➖ REMOVE LAST", callback_data=f"moddel:{cid}")],
                    [InlineKeyboardButton("🔙 BACK", callback_data=f"clone:{cid}")],
                ])
                await cq.message.edit_text(f"👮 <b>Moderators</b>\n{lst}\n\n"
                                           "Moderators ko /genlink /special_link /broadcast ke powers.",
                                           reply_markup=kb)
            elif what == "ad":
                states[sk(uid)] = {"flow": "autodel", "cid": cid}
                await cq.message.edit_text(
                    "⏱ <b>Auto Delete</b>\nKitne second baad file auto-delete ho? (0 = off)\n\n"
                    "Ab number bhejo!")
            elif what == "stats":
                users = await pool.fetchval("SELECT count(*) FROM fb_users WHERE clone_id=$1", cid)
                files = await pool.fetchval("SELECT count(*) FROM fb_files WHERE clone_id=$1", cid)
                await cq.answer(f"👥 {users} users | 📦 {files} links", show_alert=True)
                return
            elif what == "del":
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("⚠️ HAAN, DELETE KARO", callback_data=f"del:yes:{cid}")],
                    [InlineKeyboardButton("🔙 BACK", callback_data=f"clone:{cid}")],
                ])
                await cq.message.edit_text("🗑 <b>Delete Clone?</b>\nYe wapas nahi aayega!", reply_markup=kb)
            await cq.answer()
            return

        # msg:edit:def / msg:see
        if parts[0] == "msg":
            cid = int(parts[2])
            if parts[1] == "edit":
                states[sk(uid)] = {"flow": "startmsg", "cid": cid}
                await cq.message.edit_text("✏️ Naya start message text bhejo:\n"
                                           "(placeholders: {name} = user ka naam)")
            elif parts[1] == "see":
                clone = await load_clone(cid)
                await cq.message.reply(clone["start_msg"] or DEFAULT_CLONE_WELCOME)
            elif parts[1] == "def":
                await pool.execute("UPDATE fb_clones SET start_msg=NULL WHERE id=$1", cid)
                await cq.message.edit_text("✅ Default start message set!")
            await cq.answer()
            return

        if data.startswith("photo:"):
            sub = data.split(":")
            if sub[1] == "add":
                cid = int(sub[2])
                clone = await load_clone(cid)
                bname = f"@{clone['bot_username']}" if clone and clone.get("bot_username") else "clone bot"
                await cq.message.edit_text(
                    f"🖼 <b>Photo ka rule:</b> photo CLONE bot ke paas set hoti hai\n"
                    f"(photo ka ID har bot ke liye alag hota hai — isliye!)\n\n"
                    f"1️⃣ {bname} kholo\n"
                    "2️⃣ /setphoto bhejo\n"
                    "3️⃣ Photo bhejo — DONE! ✅")
                await cq.answer()
                return
            elif sub[1] == "del":
                cid = int(sub[2])
                await pool.execute("UPDATE fb_clones SET start_photo=NULL WHERE id=$1", cid)
                await cq.message.edit_text("✅ Photo delete ho gayi!")
            else:
                cid = int(sub[1])
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ Add", callback_data=f"photo:add:{cid}"),
                     InlineKeyboardButton("🗑 Delete", callback_data=f"photo:del:{cid}")],
                    [InlineKeyboardButton("🔙 BACK", callback_data=f"cfg:msg:{cid}")],
                ])
                await cq.message.edit_text("🖼 <b>Start Photo</b>\nWelcome message ke saath photo.",
                                           reply_markup=kb)
            await cq.answer()
            return

        # fsubadd:cid → owner se channel ka link/username lena (bots dialogs nahi de sakte!)
        if data.startswith("fsubadd:"):
            cid = int(data.split(":")[1])
            states[sk(uid)] = {"flow": "fsubchat", "cid": cid}
            await cq.message.edit_text(
                "📢 <b>Channel/Group add karo — link bhejo ya channel ka message FORWARD karo:</b>\n\n"
                "• <code>@username</code>\n"
                "• <code>https://t.me/username</code>\n"
                "• <code>https://t.me/c/123456789</code> (channel ka koi bhi post link)\n"
                "• <code>-100123456789</code> (numeric ID)\n"
                "• ya channel ka <b>koi bhi message FORWARD</b> kar do (sabse aasan!)\n\n"
                "⚠️ Bot us channel me <b>ADMIN</b> hona chahiye!\n\n"
                "Ab bhejo!")
            await cq.answer()
            return

        # fsubfix:cid → purane channels ka REPAIR (forward se permanent ID save)
        if data.startswith("fsubfix:"):
            cid = int(data.split(":")[1])
            states[sk(uid)] = {"flow": "fsubfix", "cid": cid}
            await cq.message.edit_text(
                "🔧 <b>REPAIR — Force-sub channels</b>\n\n"
                "Jis channel ka force-sub theek karna hai, us channel ka\n"
                "<b>koi bhi ek message yahan FORWARD kar do</b>.\n\n"
                "Bot us channel ki permanent ID save kar dega — phir JOIN NOW\n"
                "hamesha sahi dikhega (deploy ke baad bhi).\n\n"
                "(Ek-ek karke sab channels forward karo)")
            await cq.answer()
            return

        # fsubpick:cid:chatid → invite link banao + mode poochho (private ho to)
        if data.startswith("fsubpick:"):
            _, cids, chatid = data.split(":")
            cid = int(cids)
            bot_client = clone_clients.get(cid)
            if not bot_client:
                await cq.answer("Bot offline!", show_alert=True)
                return
            cid_int = int(chatid)
            try:
                chat = await bot_client.get_chat(cid_int)
            except Exception:
                await cq.answer("Channel info nahi mila", show_alert=True)
                return
            link = None
            try:
                link = await bot_client.export_chat_invite_link(cid_int)
            except Exception:
                pass
            is_private = getattr(chat, "username", None) is None
            if is_private:
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("📨 JOIN REQUEST MODE", callback_data=f"fsubmode:{cid}:{chatid}:1")],
                    [InlineKeyboardButton("🔓 NORMAL MODE", callback_data=f"fsubmode:{cid}:{chatid}:0")],
                ])
                extra = ""
                if link:
                    extra = f"\n\n🔗 Bot ka banaya link: <code>{link}</code>"
                await cq.message.edit_text(
                    "Ye PRIVATE channel hai — mode chuno:\n\n"
                    "📨 <b>Join Request</b>: users request bhejenge, tum approve karoge\n"
                    "🔓 <b>Normal</b>: seedha join ho jayenge" + extra, reply_markup=kb)
            else:
                uname_link = f"https://t.me/{chat.username}"
                await pool.execute(
                    "INSERT INTO fb_fsub (clone_id, chat_id, title, link, join_request) "
                    "VALUES ($1,$2,$3,$4,false) ON CONFLICT (clone_id, chat_id) DO NOTHING",
                    cid, str(cid_int), chat.title or "Channel", uname_link)
                await cq.message.edit_text(
                    f"✅ Force-sub add: {chat.title}\n🔗 {uname_link}")
            await cq.answer()
            return

        # fsubmode:cid:chatid:0/1
        if data.startswith("fsubmode:"):
            _, cids, chatid, mode = data.split(":")
            cid, cid_int, jr = int(cids), int(chatid), mode == "1"
            bot_client = clone_clients.get(cid)
            link = None
            title = "Channel"
            ah = 0
            uname = None
            if bot_client:
                try:
                    ch = await bot_client.get_chat(cid_int)
                    title = ch.title or "Channel"
                    uname = getattr(ch, "username", None)
                except Exception:
                    pass
                try:
                    p = await bot_client.resolve_peer(cid_int)
                    ah = int(getattr(p, "access_hash", 0) or 0)
                except Exception:
                    ah = 0
                if not ah:
                    # DB se hash lo (forward-add ne save kiya hoga)
                    try:
                        ah = int(await pool.fetchval(
                            "SELECT access_hash FROM fb_fsub WHERE clone_id=$1 AND chat_id=$2",
                            cid, str(cid_int)) or 0)
                    except Exception:
                        ah = 0
                link = await _export_link(bot_client, cid_int, join_request=jr, access_hash=ah)
            if not link:
                states[sk(uid)] = {"flow": "fsublink", "cid": cid, "chat_id": str(cid_int), "jr": jr}
                await cq.message.edit_text(
                    "⚠️ Bot invite link nahi bana paya (admin permission missing).\n"
                    "Channel ka invite link paste karo (https://t.me/+... wala):")
                await cq.answer()
                return
            await pool.execute(
                "INSERT INTO fb_fsub (clone_id, chat_id, title, link, join_request, access_hash, username) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7) ON CONFLICT (clone_id, chat_id) DO UPDATE SET "
                "title=$3, link=$4, join_request=$5, access_hash=$6, username=$7",
                cid, str(cid_int), title, link, jr, ah, uname)
            await cq.message.edit_text(f"✅ Force-sub add ho gaya! (mode: {'join-request' if jr else 'normal'})\n🔗 <code>{link}</code>")
            await cq.answer()
            return

        # fsubdel:cid:chatid
        if data.startswith("fsubdel:"):
            parts2 = data.split(":")
            cid = int(parts2[1])
            chat_id = "-" + parts2[2] if not parts2[2].isdigit() else parts2[2]
            await pool.execute("DELETE FROM fb_fsub WHERE clone_id=$1 AND chat_id LIKE $2",
                               cid, "%" + parts2[2])
            await cq.answer("❌ Remove ho gaya")
            await cq.message.edit_text("🗑 Remove ho gaya! Refresh: /start se MY CLONES kholo.")
            return

        if data.startswith("modadd:"):
            cid = int(data.split(":")[1])
            states[sk(uid)] = {"flow": "modadd", "cid": cid}
            await cq.message.edit_text("👮 Moderator ka numeric ID bhejo:\n(user ko @userinfobot se /id karke milega)")
            await cq.answer()
            return

        if data.startswith("moddel:"):
            cid = int(data.split(":")[1])
            await pool.execute(
                "DELETE FROM fb_mods WHERE ctid IN (SELECT ctid FROM fb_mods WHERE clone_id=$1 "
                "ORDER BY user_id DESC LIMIT 1)", cid)
            await cq.answer("➖ Last moderator removed")
            return

        if data.startswith("del:yes:"):
            cid = int(data.split(":")[2])
            c = clone_clients.pop(cid, None)
            if c:
                try:
                    await c.stop()
                except Exception:
                    pass
            await pool.execute("UPDATE fb_clones SET active=false WHERE id=$1", cid)
            await pool.execute("DELETE FROM fb_fsub WHERE clone_id=$1", cid)
            await pool.execute("DELETE FROM fb_mods WHERE clone_id=$1", cid)
            await cq.message.edit_text("🗑 Clone delete ho gaya. 📂 MY CLONES se bacha hua dekho.")
            await cq.answer()
            return

        await cq.answer()
    except Exception as e:
        print(f"[filebot] main cb err: {str(e)[:100]}")
        try:
            await cq.answer()
        except Exception:
            pass


# =========================================================
#                    CLONE BOT handlers
# =========================================================

def make_clone_handlers(cid):
    async def on_message(_, m):
        try:
            uid = m.from_user.id if m.from_user else 0
            name = (m.from_user.first_name if m.from_user else "friend") or "friend"
        except Exception:
            return
        text = (m.text or m.caption or "").strip()
        clone = await load_clone(cid)
        if not clone or not clone["active"]:
            return
        # ---- clone OWNER ka @username auto-capture (about link ke liye) ----
        if uid == clone["owner_id"] and getattr(m.from_user, "username", None):
            if clone.get("owner_username") != m.from_user.username:
                try:
                    await pool.execute("UPDATE fb_clones SET owner_username=$1 WHERE id=$2",
                                       m.from_user.username, cid)
                except Exception:
                    pass
        mod = is_clone_mod(clone, uid)
        try:
            await pool.execute(
                "INSERT INTO fb_users (clone_id, user_id, name) VALUES ($1,$2,$3) "
                "ON CONFLICT (clone_id, user_id) DO NOTHING", cid, uid, name)
        except Exception:
            pass
        try:
            if await pool.fetchval("SELECT 1 FROM fb_banned WHERE clone_id=$1 AND user_id=$2", cid, uid):
                return
        except Exception:
            pass
        if text.startswith("/start"):
            parts = text.split(maxsplit=1)
            if len(parts) > 1 and parts[1].strip():
                await deliver(cid, uid, parts[1].strip().split()[0], _)
                return
            welcome = clone["start_msg"] or DEFAULT_CLONE_WELCOME
            kb = clone_welcome_kb()
            if clone["start_photo"]:
                try:
                    await _.send_photo(uid, clone["start_photo"], caption=welcome.format(name=name),
                                       reply_markup=kb)
                except Exception:
                    # kharab/purani file_id — saaf karke text welcome bhejo
                    await pool.execute("UPDATE fb_clones SET start_photo=NULL WHERE id=$1", cid)
                    await _.send_message(uid, welcome.format(name=name), reply_markup=kb)
            else:
                await _.send_message(uid, welcome.format(name=name), reply_markup=kb)
            return

        if text.startswith("/menu") or text.startswith("/help"):
            await _.send_message(uid, CLONE_HELP)
            return

        if text.startswith("/about"):
            await _send_about(_, uid, clone,
                              f"@{clone['bot_username']}" if clone["bot_username"] else "File Store Bot")
            return

        if text.startswith("/version"):
            await _.send_message(
                uid, f"📦 <b>Code version:</b> <code>{FILEBOT_VERSION}</code>")
            return

        if text.startswith("/id"):
            await _.send_message(uid, f"🆔 Tumhara ID: <code>{uid}</code>")
            return

        # ---- FSUB DEBUG: owner/mod apne liye dekhe KAUNSA channel kyu block ho raha ----
        if text.startswith("/fsdebug"):
            if uid != clone["owner_id"] and uid != SUPER_OWNER and not mod:
                return
            target = uid
            for part in text.split():
                if part.lstrip("-").isdigit() and len(part) >= 5:
                    target = int(part)
                    break
            lines = [f"🔍 <b>FSUB DEBUG — user {target}</b>", ""]
            rows = await pool.fetch("SELECT * FROM fb_fsub WHERE clone_id=$1", cid)
            sysr = await sys_fsub_row()
            for r in rows:
                rd = dict(r)
                rok, rerr = await _requests_lookup(_, rd, target)
                req = rok if rok is not None else f"ERR:{rerr}"
                mem = await _is_member(_, rd, target)
                ok = await fsub_check_user(_, rd, target)
                lines.append(
                    f"• <b>{rd.get('title') or rd.get('chat_id')}</b>\n"
                    f"   chat_id: <code>{rd.get('chat_id')}</code> | "
                    f"hash: {'✅' if int(rd.get('access_hash') or 0) else '❌ NAHI'} | "
                    f"username: {rd.get('username') or '—'} | "
                    f"join_request: {'haan' if rd.get('join_request') else 'nahi'}\n"
                    f"   request-list: {req} | member-check: {mem} | "
                    f"result: {'✅ CONTENT' if ok else '🔒 JOIN NOW'}")
            if sysr and main_client:
                uname = await get_setting("sys_fsub_username")
                ah = int(await get_setting("sys_fsub_access_hash", 0) or 0)
                pseudo = {"chat_id": str(sysr["chat_id"]), "username": uname,
                          "access_hash": ah, "join_request": False}
                mem = await _is_member(main_client, pseudo, target)
                ok = await fsub_check_user(main_client, pseudo, target)
                lines.append(
                    f"• <b>SYSTEM ({sysr.get('title')})</b>\n"
                    f"   member-check: {mem} | result: {'✅ CONTENT' if ok else '🔒 JOIN NOW'}")
            lines.append("\n💡 hash ❌ = REPAIR karo (FORCE SUB → 🔧 REPAIR, channel ka message forward karo)")
            await _.send_message(uid, "\n".join(lines), disable_web_page_preview=True)
            return

        if text.startswith("/setphoto"):
            if not mod:
                return
            states[sk(uid, cid)] = {"flow": "setphoto"}
            await _.send_message(uid, "🖼 Ab photo bhejo — ye welcome photo ban jayegi!")
            return

        if text.startswith("/stats"):
            if not mod:
                return
            users = await pool.fetchval("SELECT count(*) FROM fb_users WHERE clone_id=$1", cid)
            files = await pool.fetchval("SELECT count(*) FROM fb_files WHERE clone_id=$1", cid)
            await _.send_message(uid, f"📊 <b>Stats</b>\n👥 Users: {users}\n📦 Links: {files}")
            return

        if text.startswith("/ban") or text.startswith("/unban"):
            if not mod:
                return
            ban = text.startswith("/ban")
            parts = text.split()
            target = None
            if len(parts) > 1 and parts[1].lstrip("-").isdigit():
                target = int(parts[1])
            elif m.reply_to_message and m.reply_to_message.from_user:
                target = m.reply_to_message.from_user.id
            if not target:
                await _.send_message(uid, "Usage: /ban 123456789 (ya reply)")
                return
            if ban:
                await pool.execute("INSERT INTO fb_banned (clone_id, user_id) VALUES ($1,$2) ON CONFLICT DO NOTHING", cid, target)
                await _.send_message(uid, f"🚫 Banned: <code>{target}</code>")
            else:
                await pool.execute("DELETE FROM fb_banned WHERE clone_id=$1 AND user_id=$2", cid, target)
                await _.send_message(uid, f"✅ Unbanned: <code>{target}</code>")
            return

        if text.startswith("/broadcast"):
            if not mod:
                return
            r = m.reply_to_message
            if not r:
                await _.send_message(uid, "📢 Kisi message pe reply karke /broadcast bhejo.")
                return
            ids = [row["user_id"] for row in await pool.fetch(
                "SELECT user_id FROM fb_users WHERE clone_id=$1", cid)]
            ok = blk = fail = 0
            status = await _.send_message(uid, f"⏳ Broadcast... 0/{len(ids)}")
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
                        await status.edit_text(f"⏳ {i + 1}/{len(ids)} | ✅ {ok} ❌ {fail}")
                    except Exception:
                        pass
                await asyncio.sleep(0.05)
            await _.send_message(
                uid, f"📢 <b>Broadcast done</b>\n👥 Total: {len(ids)}\n✅ {ok}\n🚫 {blk}\n❌ {fail}")
            return

        if text.startswith("/genlink"):
            if not mod:
                return
            r = m.reply_to_message
            if r:
                lid = await make_link(cid, str(r.chat.id), [r.id], uid)
                url = f"https://t.me/{clone['bot_username']}?start={lid}"
                await _.send_message(
                    uid, f"✅ <b>Here is your link:</b>\n\n<code>{url}</code>",
                    reply_markup=link_kb(url), disable_web_page_preview=True)
            else:
                states[sk(uid, cid)] = {"flow": "genlink"}
                await _.send_message(
                    uid, "📤 Ab file/message bhejo — link bana dunga. (/cancel se ruko)")
            return

        if text.startswith("/special_link"):
            if not mod:
                return
            states[sk(uid, cid)] = {"flow": "special", "msg_ids": []}
            await _.send_message(
                uid, "📥 <b>SPECIAL LINK</b>\n\nJitni files bhejni hain bhejo — "
                "jab sab bhej chho to <b>/done</b> likho.\n❌ Cancel: /cancel")
            return

        if text.startswith("/batch"):
            if not mod:
                return
            parts = text.split()
            if len(parts) < 3:
                await _.send_message(
                    uid, "Usage: <code>/batch https://t.me/c/xxx/10 https://t.me/c/xxx/50</code>")
                return
            c1, s = parse_tme_link(parts[1])
            c2, e = parse_tme_link(parts[2])
            if not c1 or not c2 or c1 != c2 or e < s:
                await _.send_message(uid, "❌ Dono links same channel ke + pehla chhota number")
                return
            if e - s > 300:
                await _.send_message(uid, "❌ Max 300 messages")
                return
            lid = await make_link(cid, str(c1), list(range(s, e + 1)), uid)
            url = f"https://t.me/{clone['bot_username']}?start={lid}"
            await _.send_message(
                uid, f"✅ <b>Link ready</b> ({e - s + 1} files):\n\n<code>{url}</code>",
                reply_markup=link_kb(url), disable_web_page_preview=True)
            return

        if text.startswith("/cancel"):
            states.pop(sk(uid, cid), None)
            await _.send_message(uid, "❌ Cancel ho gaya.")
            return

        if text.startswith("/done"):
            st = states.get(sk(uid, cid))
            if st and st.get("flow") == "special":
                if not st["msg_ids"]:
                    await _.send_message(uid, "❌ Pehle kuch files bhejo!")
                    return
                lid = await make_link(cid, str(m.chat.id), st["msg_ids"], uid, special=True)
                states.pop(sk(uid, cid), None)
                url = f"https://t.me/{clone['bot_username']}?start={lid}"
                await _.send_message(
                    uid, f"✅ <b>Special link ready</b> ({len(st['msg_ids'])} files):\n\n<code>{url}</code>",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("✏️ MODIFY LINK", callback_data=f"mod:{lid}")],
                        [InlineKeyboardButton("🔗 SHARE URL", url=f"https://t.me/share/url?url={url}")],
                    ]), disable_web_page_preview=True)
            return

        # ---- state-based file collection ----
        st = states.get(sk(uid, cid))
        if st:
            if st.get("flow") == "setphoto":
                if m.photo:
                    await pool.execute("UPDATE fb_clones SET start_photo=$1 WHERE id=$2",
                                       m.photo.file_id, cid)
                    states.pop(sk(uid, cid), None)
                    await _.send_message(uid, "✅ Start photo save ho gayi! /start se dekho.")
                return
            has_media = m.video or m.document or m.audio or m.photo or m.animation
            if st.get("flow") == "genlink":
                if has_media:
                    lid = await make_link(cid, str(m.chat.id), [m.id], uid)
                    states.pop(sk(uid, cid), None)
                    url = f"https://t.me/{clone['bot_username']}?start={lid}"
                    await _.send_message(
                        uid, f"✅ <b>Here is your link:</b>\n\n<code>{url}</code>",
                        reply_markup=link_kb(url), disable_web_page_preview=True)
                return
            if st.get("flow") == "special":
                if has_media or m.reply_to_message:
                    mid = m.reply_to_message.id if m.reply_to_message else m.id
                    chat = m.reply_to_message.chat.id if m.reply_to_message else m.chat.id
                    st["msg_ids"].append(mid)
                    await _.send_message(uid, f"➕ Added ({len(st['msg_ids'])}) — /done se khatam karo")
                return
            if st.get("flow") == "sp_add":
                if has_media:
                    row = await get_bot_link(cid, st["link_id"])
                    if row:
                        ids = json.loads(row["msg_ids"])
                        ids.append(m.id)
                        await pool.execute("UPDATE fb_files SET msg_ids=$1 WHERE link_id=$2",
                                           json.dumps(ids), st["link_id"])
                        await _.send_message(uid, f"✅ Added ({len(ids)} total) — /done ya aur bhejo")
                return
        # NO auto response on plain files/text — sirf command ke baad (owner ka order!)
        return

    async def on_callback(_, cq):
        data = cq.data or ""
        uid = cq.from_user.id
        clone = await load_clone(cid)
        if not clone:
            return
        mod = is_clone_mod(clone, uid)
        try:
            if data in ("help",):
                await cq.message.edit_text(CLONE_HELP, reply_markup=clone_welcome_kb())
            elif data == "about":
                try:
                    await cq.message.delete()
                except Exception:
                    pass
                await _send_about(_, uid, clone,
                                  f"@{clone['bot_username']}" if clone['bot_username'] else "Bot",
                                  reply_markup=clone_welcome_kb())
            elif data.startswith("try:"):
                lid = data.split(":", 1)[1]
                await deliver(cid, uid, lid, _)
            elif data.startswith("mod:") and mod:
                lid = data.split(":", 1)[1]
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🗑 DELETE LINK", callback_data=f"mdl:{lid}")],
                    [InlineKeyboardButton("✏️ EDIT CONTENT", callback_data=f"med:{lid}")],
                    [InlineKeyboardButton("❌ CANCEL", callback_data="noop")],
                    [InlineKeyboardButton("🔒 CLOSE", callback_data="close")],
                ])
                await cq.message.edit_text("✏️ <b>Modify Link</b>\nKya karna hai?", reply_markup=kb)
            elif data.startswith("mdl:") and mod:
                lid = data.split(":", 1)[1]
                await pool.execute("DELETE FROM fb_files WHERE link_id=$1", lid)
                await cq.message.edit_text("🗑 Link delete ho gaya!")
            elif data.startswith("med:") and mod:
                lid = data.split(":", 1)[1]
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("➕ ADD CONTENT", callback_data=f"madd:{lid}")],
                    [InlineKeyboardButton("➖ REMOVE CONTENT", callback_data=f"mrem:{lid}")],
                    [InlineKeyboardButton("❌ CANCEL", callback_data="noop")],
                    [InlineKeyboardButton("🔒 CLOSE", callback_data="close")],
                ])
                await cq.message.edit_text("✏️ <b>Edit Content</b>", reply_markup=kb)
            elif data.startswith("madd:") and mod:
                lid = data.split(":", 1)[1]
                states[sk(uid, cid)] = {"flow": "sp_add", "link_id": lid}
                await cq.message.edit_text(
                    f"➕ Ab nayi files bhejo — add hoti jayengi!\nLink: <code>{lid}</code>")
            elif data.startswith("mrem:") and mod:
                lid = data.split(":", 1)[1]
                row = await get_bot_link(cid, lid)
                if not row:
                    await cq.answer("Link nahi mila", show_alert=True)
                    return
                ids = json.loads(row["msg_ids"])
                kb = [[InlineKeyboardButton(f"❌ File {i + 1} (msg {x})",
                                            callback_data=f"mrdel:{lid}:{i}")]
                      for i, x in enumerate(ids[:20])]
                kb.append([InlineKeyboardButton("🔒 CLOSE", callback_data="close")])
                await cq.message.edit_text("➖ Kaunsi file hatani hai?",
                                           reply_markup=InlineKeyboardMarkup(kb))
            elif data.startswith("mrdel:") and mod:
                _, lid, idx = data.split(":")
                row = await get_bot_link(cid, lid)
                if row:
                    ids = json.loads(row["msg_ids"])
                    i = int(idx)
                    if 0 <= i < len(ids):
                        ids.pop(i)
                        await pool.execute("UPDATE fb_files SET msg_ids=$1 WHERE link_id=$2",
                                           json.dumps(ids), lid)
                        await cq.answer("🗑 Remove ho gayi!")
                        await cq.message.edit_text(f"✅ Ab {len(ids)} files hain. (/genlink wapas se dekh lo)")
                        return
                await cq.answer("Error", show_alert=True)
            elif data == "close":
                try:
                    await cq.message.delete()
                except Exception:
                    pass
            elif data == "noop":
                await cq.answer()
            await cq.answer()
        except Exception as e:
            print(f"[filebot] clone cb err: {str(e)[:100]}")
            try:
                await cq.answer()
            except Exception:
                pass

    # ---- join REQUEST record (approve NAHI — bas user ka ID save) ----
    async def on_join_request(_, jr):
        try:
            await pool.execute(
                "INSERT INTO fb_jreq (chat_id, user_id) VALUES ($1,$2) "
                "ON CONFLICT (chat_id, user_id) DO NOTHING",
                str(jr.chat.id), jr.from_user.id)
            print(f"[filebot] join REQUEST saved: user={jr.from_user.id} chat={jr.chat.id}")
        except Exception as e:
            print(f"[filebot] jreq err: {str(e)[:80]}")

    return on_message, on_callback, on_join_request


# =========================================================
#                    BOOT / LIFECYCLE
# =========================================================

async def boot_clone(clone):
    """clone ko start karo (client + handlers)"""
    cid = clone["id"]
    if cid in clone_clients:
        return True
    try:
        c = Client(f"clone_{cid}", api_id=API_ID, api_hash=API_HASH,
                   bot_token=clone["token"], in_memory=True)
        on_msg, on_cb, on_jr = make_clone_handlers(cid)
        c.add_handler(MessageHandler(safe_handler(on_msg), filters.private))
        c.add_handler(CallbackQueryHandler(safe_handler(on_cb)))
        c.add_handler(ChatJoinRequestHandler(safe_handler(on_jr)))
        await c.start()
        me = await c.get_me()
        if me.username and me.username != clone.get("bot_username"):
            await pool.execute("UPDATE fb_clones SET bot_username=$1 WHERE id=$2", me.username, cid)
        clone_clients[cid] = c
        print(f"[filebot] clone #{cid} LIVE: @{me.username}")
        return True
    except Exception as e:
        print(f"[filebot] clone #{cid} start FAIL: {str(e)[:100]}")
        return False


async def start():
    global pool, main_client, main_me
    if not TOKEN:
        print("[filebot] FILESTORE_BOT_TOKEN missing — file bot OFF")
        return
    if not DB_URL or not API_ID or not API_HASH:
        print("[filebot] API_ID/API_HASH/DATABASE_URL missing — file bot OFF")
        return
    pool = await asyncpg.create_pool(DB_URL, statement_cache_size=0, min_size=1, max_size=5)
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS fb_clones (
            id SERIAL PRIMARY KEY,
            owner_id BIGINT NOT NULL,
            owner_name TEXT,
            token TEXT NOT NULL,
            bot_username TEXT,
            start_msg TEXT,
            start_photo TEXT,
            auto_delete INT DEFAULT 0,
            active BOOLEAN DEFAULT true,
            created_at TIMESTAMP DEFAULT now()
        )""")
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS fb_fsub (
            clone_id INT NOT NULL,
            chat_id TEXT NOT NULL,
            title TEXT,
            link TEXT,
            join_request BOOLEAN DEFAULT false,
            access_hash BIGINT DEFAULT 0,
            username TEXT,
            UNIQUE (clone_id, chat_id)
        )""")
    # purane DB me naye columns add karo (safe — pehle se ho to skip)
    await pool.execute("ALTER TABLE fb_fsub ADD COLUMN IF NOT EXISTS access_hash BIGINT DEFAULT 0")
    await pool.execute("ALTER TABLE fb_fsub ADD COLUMN IF NOT EXISTS username TEXT")
    await pool.execute("ALTER TABLE fb_clones ADD COLUMN IF NOT EXISTS owner_username TEXT")
    try:
        _su = await get_setting("super_owner_username")
        if _su:
            global SUPER_OWNER_USERNAME
            SUPER_OWNER_USERNAME = _su
    except Exception:
        pass
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS fb_jreq (
            chat_id TEXT NOT NULL,
            user_id BIGINT NOT NULL,
            UNIQUE (chat_id, user_id)
        )""")
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS fb_mods (
            clone_id INT NOT NULL,
            user_id BIGINT NOT NULL,
            UNIQUE (clone_id, user_id)
        )""")
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS fb_files (
            link_id TEXT PRIMARY KEY,
            clone_id INT NOT NULL,
            chat_id TEXT NOT NULL,
            msg_ids TEXT NOT NULL,
            created_by BIGINT,
            special BOOLEAN DEFAULT false,
            created_at TIMESTAMP DEFAULT now()
        )""")
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS fb_users (
            clone_id INT NOT NULL,
            user_id BIGINT NOT NULL,
            name TEXT,
            joined TIMESTAMP DEFAULT now(),
            UNIQUE (clone_id, user_id)
        )""")
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS fb_banned (
            clone_id INT NOT NULL,
            user_id BIGINT NOT NULL,
            UNIQUE (clone_id, user_id)
        )""")
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS fb_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )""")

    main_client = Client("filebot_main", api_id=API_ID, api_hash=API_HASH,
                         bot_token=TOKEN, in_memory=True)
    main_client.add_handler(MessageHandler(safe_handler(main_on_message), filters.private))
    main_client.add_handler(CallbackQueryHandler(safe_handler(main_on_callback)))

    # ---- SYSTEM channel ki join REQUESTS record (approve nahi) ----
    async def main_on_join_request(_, jr):
        try:
            await pool.execute(
                "INSERT INTO fb_jreq (chat_id, user_id) VALUES ($1,$2) "
                "ON CONFLICT (chat_id, user_id) DO NOTHING",
                str(jr.chat.id), jr.from_user.id)
            print(f"[filebot] SYS join REQUEST saved: user={jr.from_user.id}")
        except Exception as e:
            print(f"[filebot] main jreq err: {str(e)[:80]}")
    main_client.add_handler(ChatJoinRequestHandler(safe_handler(main_on_join_request)))
    await main_client.start()
    main_me = await main_client.get_me()
    await resolve_update_link()
    print(f"[filebot] RUNNING CODE: {FILEBOT_VERSION}")
    print(f"[filebot] MAIN bot LIVE: @{main_me.username}")

    rows = await pool.fetch("SELECT * FROM fb_clones WHERE active=true ORDER BY id")
    ok = 0
    for r in rows:
        if await boot_clone(dict(r)):
            ok += 1
        await asyncio.sleep(0.3)
    print(f"[filebot] {ok}/{len(rows)} clones booted")

    # ---- SELF-HEALING: har 5 min band clones wapas ON karo ----
    async def _heal():
        while True:
            await asyncio.sleep(300)
            try:
                hrows = await pool.fetch("SELECT * FROM fb_clones WHERE active=true")
                for r in hrows:
                    if r["id"] not in clone_clients:
                        if await boot_clone(dict(r)):
                            try:
                                await main_client.send_message(
                                    SUPER_OWNER, f"🤖 Clone @{r['bot_username']} wapas LIVE ho gaya")
                            except Exception:
                                pass
                    await asyncio.sleep(0.5)
            except Exception as e:
                print("[filebot] heal err:", str(e)[:80])
            # ---- PURANE fsub rows heal: hash save karo jahan tak ho sake ----
            try:
                orows = await pool.fetch(
                    "SELECT DISTINCT chat_id FROM fb_fsub "
                    "WHERE (access_hash IS NULL OR access_hash=0) AND username IS NULL")
                for o in orows:
                    cids = await pool.fetch(
                        "SELECT DISTINCT clone_id FROM fb_fsub WHERE chat_id=$1", o["chat_id"])
                    for cr in cids:
                        cc = clone_clients.get(cr["clone_id"])
                        if not cc:
                            continue
                        try:
                            ref = chat_ref(o["chat_id"])
                            ch = await cc.get_chat(ref)
                            p = await cc.resolve_peer(ref)
                            ah = int(getattr(p, "access_hash", 0) or 0)
                            un = getattr(ch, "username", None)
                            if ah or un:
                                await pool.execute(
                                    "UPDATE fb_fsub SET access_hash=$1, username=$2 WHERE chat_id=$3",
                                    ah, un, str(o["chat_id"]))
                                print(f"[filebot] fsub HEALED: {o['chat_id']}")
                            break
                        except Exception:
                            continue
                    await asyncio.sleep(0.3)
            except Exception as e:
                print("[filebot] heal-fsub err:", str(e)[:80])
            # ---- update channel link retry (bot admin bane to link ban jayega) ----
            if not UPDATE_LINK_RAW.startswith("http") and "/c/" in UPDATE_LINK:
                try:
                    await resolve_update_link()
                except Exception:
                    pass
    asyncio.create_task(_heal())


async def stop():
    global pool, main_client
    if main_client:
        try:
            await main_client.stop()
        except Exception:
            pass
    for c in list(clone_clients.values()):
        try:
            await c.stop()
        except Exception:
            pass
    clone_clients.clear()
    if pool:
        try:
            await pool.close()
        except Exception:
            pass


if __name__ == "__main__":
    async def _main():
        await start()
        print("[filebot] standalone — Ctrl+C to stop")
        await asyncio.Event().wait()
    asyncio.run(_main())
