"""
Telegram layer — 6 bots, storage channel auto-index, link parsing, proxy streaming.
Pyrogram (MTProto) use karta hai — Bot API ki 20MB limit nahi lagti.
"""
import os
import re
import time
import asyncio
from typing import Optional

# --- SPEED PATCH: TgCrypto (10x streaming speed) ---
# Pyrogram import se PEHLE install karna zaroori hai warna load nahi hota.
# --only-binary: bina compiler ke fast fail (galat Python version pe). Safe.
SPEED_ON = False
try:
    import tgcrypto  # noqa: F401
    SPEED_ON = True
    print("[tg] TgCrypto ON")
except ImportError:
    try:
        import subprocess, sys
        for _a in ([sys.executable, "-m", "pip", "install", "--user", "--only-binary", ":all:", "-q", "tgcrypto"],
                   [sys.executable, "-m", "pip", "install", "--only-binary", ":all:", "-q", "tgcrypto"]):
            try:
                subprocess.run(_a, timeout=90, capture_output=True)
                import tgcrypto  # noqa: F401
                SPEED_ON = True
                print("[tg] TgCrypto install ho gaya — streaming 10x fast!")
                break
            except Exception:
                continue
        else:
            print("[tg] TgCrypto wheel nahi mili — normal speed (PYTHON_VERSION=3.11 set karo)")
    except Exception:
        pass

from pyrogram import Client
from pyrogram.handlers import MessageHandler
from pyrogram.enums import ChatType

from sqlalchemy import select
from db import SessionLocal, Playlist, Episode, TgPeer

API_ID = int(os.environ.get("API_ID", "0") or 0)
API_HASH = os.environ.get("API_HASH", "").strip()
BOT_TOKENS = [t.strip() for t in os.environ.get("BOT_TOKENS", "").split(",") if t.strip()]
STORAGE_CHANNEL_ID = int(os.environ.get("STORAGE_CHANNEL_ID", "0") or 0)
ADMIN_TG_IDS = {int(x) for x in os.environ.get("ADMIN_TG_IDS", "").split(",") if x.strip()}
SESSION_DIR = os.environ.get("SESSION_DIR", "./sessions")

# Link parsers — dono type ke links
RE_CHAN = re.compile(r"(?:https?://)?t\.me/c/(\d+)/(\d+)", re.I)
RE_BOT = re.compile(r"(?:https?://)?t\.me/([A-Za-z0-9_]+)\?start=([A-Za-z0-9_\-+=/]+)", re.I)

clients = {}
AUTO_PL_TITLE = "My Uploads"  # channel pe seedha aayi videos is playlist me jayengi


def configured() -> bool:
    return bool(API_ID and API_HASH and BOT_TOKENS and STORAGE_CHANNEL_ID)


def _video_media(message):
    if message is None:
        return None
    if message.video:
        return message.video
    if message.document:
        doc = message.document
        if doc.mime_type and doc.mime_type.startswith("video/"):
            return doc
    return None


async def _index_media_message(msg, bot_index: int, fallback_title: str = "") -> Optional[int]:
    """Kisi bhi message (channel post ya copy) se episode banao. Duplicate-safe."""
    media = _video_media(msg)
    if media is None:
        return None
    async with SessionLocal() as s:
        existing = (await s.execute(
            Episode.__table__.select().where(
                Episode.chat_id == msg.chat.id, Episode.message_id == msg.id
            ).limit(1)
        )).first()
        if existing:
            return existing.id
        # "My Uploads" playlist dhundo ya banao
        pl = (await s.execute(
            Playlist.__table__.select().where(Playlist.title == AUTO_PL_TITLE).limit(1)
        )).first()
        if not pl:
            np = Playlist(title=AUTO_PL_TITLE, category="Uploads", desc="",
                          emoji="📥", created_at=time.time())
            s.add(np)
            await s.flush()
            pl_id = np.id
        else:
            pl_id = pl.id
        # agla episode number
        row = (await s.execute(
            Episode.__table__.select().where(Episode.playlist_id == pl_id)
        )).all()
        ep_num = (max([r.ep_num for r in row], default=0) + 1) if row else 1
        e = Episode(
            playlist_id=pl_id, season=1, ep_num=ep_num,
            title=(fallback_title or getattr(media, "file_name", "") or f"Video {ep_num}")[:300],
            chat_id=msg.chat.id, message_id=msg.id, bot_index=bot_index,
            size=int(getattr(media, "file_size", 0) or 0),
            duration=int(getattr(media, "duration", 0) or 0),
            mime=(getattr(media, "mime_type", "") or "video/mp4"),
            ref="", created_at=time.time(),
        )
        s.add(e)
        await s.commit()
        return e.id


def _make_handler(bot_index: int):
    async def on_message(client: Client, message):
        try:
            # 0) Channel-memory save (deploy-proof)
            if message.chat:
                await _save_peer_memory(client, bot_index, message.chat.id)

            # 1) Channel post (storage channel me nayi video) → auto-index
            if (message.chat and message.chat.type in (ChatType.CHANNEL, ChatType.SUPERGROUP)
                    and message.chat.id == STORAGE_CHANNEL_ID):
                if bot_index == 0 and _video_media(message):
                    vid = await _index_media_message(message, 0,
                                                     fallback_title=message.caption or "")
                    if vid:
                        print(f"[tg] auto-indexed video #{message.id} -> episode {vid}")
                return

            # 2) Admin ka private message
            if not message.from_user or message.from_user.id not in ADMIN_TG_IDS:
                return

            # channel ID helper: kisi bhi chat ka message forward karo
            if message.forward_from_chat:
                await message.reply_text(
                    f"This chat's ID: {message.forward_from_chat.id}"
                )
                return

            media = _video_media(message)
            if media is None:
                if message.text and message.text.strip().startswith("/id"):
                    await message.reply_text(f"Your Telegram ID: {message.from_user.id}")
                return

            # Video ko storage channel me copy karo (turant, bina download)
            copy = await message.copy(STORAGE_CHANNEL_ID)
            vid = await _index_media_message(copy, bot_index,
                                             fallback_title=message.caption or "")
            if vid:
                await message.reply_text(
                    f"✅ App me add ho gaya! (Episode ID: {vid})\n"
                    "App me playlist/season/episode set karne ke liye Add Video se "
                    "is message ka link bhi use kar sakte ho."
                )
        except Exception as e:
            print(f"[tg] handler error: {e}")

    return on_message


async def _save_peer_memory(client, bot_index: int, chat_id: int):
    """Bot ki channel-memory (access hash) DB me save karo — deploy ke baad restore ke liye."""
    try:
        r = await client.storage.get_peer_by_id(chat_id)
        if not r:
            return
        access_hash = int(r[1]); ptype = (r[2] if len(r) > 2 else None) or "channel"
        async with SessionLocal() as s:
            obj = await s.get(TgPeer, (bot_index, chat_id))
            if obj:
                obj.access_hash = access_hash
                obj.peer_type = ptype
            else:
                s.add(TgPeer(bot_index=bot_index, peer_id=chat_id,
                             access_hash=access_hash, peer_type=ptype))
            await s.commit()
    except Exception as e:
        print("[tg] peer save fail:", e)


async def _restore_peer_memory(client, bot_index: int):
    """Deploy/restart ke baad bot ko pehle se jaane wale channels yaad dilao."""
    try:
        async with SessionLocal() as s:
            rows = (await s.execute(
                select(TgPeer).where(TgPeer.bot_index == bot_index)
            )).scalars().all()
        if rows:
            await client.storage.update_peers([
                (r.peer_id, r.access_hash, r.peer_type or "channel", r.username or "", None)
                for r in rows
            ])
            print(f"[tg] bot{bot_index}: {len(rows)} channel(s) ki memory restore hui")
    except Exception as e:
        print("[tg] peer restore fail:", e)


async def _learn_channel_peer(client, chat_id: int) -> bool:
    """Bot ke liye channel ka peer (memory) seekhne ki koshish — khud-doctor.
    Tareeka 1: channels.getChannels (access_hash=0) — agar bot channel me hai to mil sakta hai
    Tareeka 2: getDialogs scan — bots ke saare chats"""
    import pyrogram.utils as putils
    from pyrogram import raw as _raw

    async def _known():
        try:
            r = await client.storage.get_peer_by_id(chat_id)
            return bool(r)
        except Exception:
            return False

    if await _known():
        return True

    peers = []
    # Tareeka 1: GetChannels
    try:
        bare = int(str(chat_id).replace("-100", ""))
        r = await client.invoke(_raw.functions.channels.GetChannels(
            id=[_raw.types.InputChannel(channel_id=bare, access_hash=0)]))
        for peer in (getattr(r, "chats", []) or []):
            if isinstance(peer, _raw.types.Channel):
                pid = putils.get_channel_id(peer.id)
                peers.append((pid, peer.access_hash,
                              "channel" if peer.broadcast else "supergroup",
                              peer.username, None))
    except Exception:
        pass
    # Tareeka 2: GetDialogs
    if not peers:
        try:
            r = await client.invoke(_raw.functions.messages.GetDialogs(
                offset_date=0, offset_id=0, offset_peer=_raw.types.InputPeerEmpty(),
                limit=100, hash=0))
            for peer in (getattr(r, "chats", []) or []):
                if isinstance(peer, _raw.types.Channel):
                    pid = putils.get_channel_id(peer.id)
                    peers.append((pid, peer.access_hash,
                                  "channel" if peer.broadcast else "supergroup",
                                  peer.username, None))
        except Exception:
            pass
    if peers:
        try:
            await client.storage.update_peers(peers)
        except Exception:
            return False
        return await _known()
    return False


async def channel_ready() -> bool:
    """Traffic light: kya bots storage channel ko jaante hain?"""
    if not clients or not STORAGE_CHANNEL_ID:
        return False
    for c in clients.values():
        try:
            r = await c.storage.get_peer_by_id(STORAGE_CHANNEL_ID)
            if r:
                return True
        except Exception:
            continue
    return False


async def start():
    if not configured():
        print("[tg] Telegram env vars missing — Telegram OFFLINE mode.")
        return
    os.makedirs(SESSION_DIR, exist_ok=True)
    for idx, token in enumerate(BOT_TOKENS):
        c = Client(
            f"bot{idx}", api_id=API_ID, api_hash=API_HASH,
            bot_token=token, workdir=SESSION_DIR,
        )
        c.add_handler(MessageHandler(_make_handler(idx)))
        try:
            await c.start()
        except Exception as e:
            # FloodWait chhota ho to wait karke ek baar retry, warna bot skip
            w = int(getattr(e, "value", 0) or 0)
            if w and 0 < w <= 90:
                print(f"[tg] bot{idx}: FloodWait {w}s — wait karke retry")
                await asyncio.sleep(w + 2)
                try:
                    await c.start()
                except Exception as e2:
                    print(f"[tg] bot{idx} start fail — skip: {str(e2)[:80]}")
                    continue
            else:
                print(f"[tg] bot{idx} start fail — skip: {str(e)[:80]}")
                continue
        await _restore_peer_memory(c, idx)
        # channel memory khud seekhne ki koshish (khud-doctor)
        if STORAGE_CHANNEL_ID:
            try:
                ok = await _learn_channel_peer(c, STORAGE_CHANNEL_ID)
                if ok:
                    print(f"[tg] bot{idx}: channel memory mil gayi (auto)")
            except Exception:
                pass
        clients[idx] = c
    if not clients:
        print("[tg] SAB bots offline — app phir bhi chalegi (video add/stream baad me try karo)")
    print(f"[tg] {len(clients)} bot(s) connected.")


async def stop():
    for c in clients.values():
        try:
            await c.stop()
        except Exception:
            pass
    clients.clear()


def get_client(bot_index: int) -> Optional[Client]:
    return clients.get(bot_index)


def parse_link(link: str):
    """
    Telegram link parse karo.
    Return: ("channel", chat_id, message_id) ya ("bot", username, start_param) ya None
    """
    link = (link or "").strip()
    m = RE_CHAN.search(link)
    if m:
        chat_id = int("-100" + m.group(1))
        return ("channel", chat_id, int(m.group(2)))
    m = RE_BOT.search(link)
    if m:
        return ("bot", m.group(1), m.group(2))
    return None


async def pick_bot() -> int:
    from sqlalchemy import select, func
    async with SessionLocal() as s:
        rows = (await s.execute(
            select(Episode.bot_index, func.count(Episode.id)).group_by(Episode.bot_index)
        )).all()
    counts = {r[0]: r[1] for r in rows}
    return min(range(len(BOT_TOKENS)), key=lambda i: counts.get(i, 0))


async def fetch_episode_media(link: str):
    """
    Link se video message fetch karo (bots us channel ke admin hone chahiye).
    Return: (bot, message, media) ya (None, error_message, None)
    """
    parsed = parse_link(link)
    if not parsed:
        return None, "Link samajh nahi aaya — t.me/c/CHANNEL_ID/MESSAGE_ID ya t.me/botname?start=... format me bhejo", None

    idx = await pick_bot()
    client = clients.get(idx)
    if client is None:
        return None, "Telegram bots offline hain — server admin se contact karo", None

    kind = parsed[0]
    if kind == "channel":
        chat_id, msg_id = parsed[1], parsed[2]
        try:
            msg = await client.get_messages(chat_id, msg_id)
        except Exception as e:
            # peer bhoola ho to seekh ke ek baar aur try karo (self-heal)
            try:
                await _learn_channel_peer(client, chat_id)
                msg = await client.get_messages(chat_id, msg_id)
            except Exception as e2:
                return None, ("Wo channel hamare bots ke liye accessible nahi hai. "
                              "Us channel me apne saare bots ko admin banao. (Detail: "
                              + str(e2)[:120] + ")"), None
        await _save_peer_memory(client, idx, chat_id)
        media = _video_media(msg)
        if media is None:
            return None, "Us message me koi video nahi mili", None
        return (client, msg, media), None, None

    # bot?start= link — file us bot ke chat me hai, hamare bots us tak nahi pahunch sakte
    return None, ("Bot-link (t.me/bot?start=...) se video sirf wahi bot nikaal sakta hai. "
                  "Us video ka channel message link (t.me/c/...) use karo — filestore bot "
                  "video jis channel me save karta hai, uska message link bhejo."), None


async def upsert_episode(msg, media, playlist_id: int, season: int, ep_num: int,
                         title: str, bot_index: int, ref: str) -> int:
    """Episode banao ya update karo (same chat+message pe move bhi hota hai)."""
    async with SessionLocal() as s:
        existing = (await s.execute(
            Episode.__table__.select().where(
                Episode.chat_id == msg.chat.id, Episode.message_id == msg.id
            ).limit(1)
        )).first()
        if existing:
            # move/update
            obj = await s.get(Episode, existing.id)
            obj.playlist_id = playlist_id
            obj.season = season
            obj.ep_num = ep_num
            if title:
                obj.title = title[:300]
            obj.ref = ref
            await s.commit()
            return obj.id
        e = Episode(
            playlist_id=playlist_id, season=season, ep_num=ep_num,
            title=(title or getattr(media, "file_name", "") or f"Episode {ep_num}")[:300],
            chat_id=msg.chat.id, message_id=msg.id, bot_index=bot_index,
            size=int(getattr(media, "file_size", 0) or 0),
            duration=int(getattr(media, "duration", 0) or 0),
            mime=(getattr(media, "mime_type", "") or "video/mp4"),
            ref=ref, created_at=time.time(),
        )
        s.add(e)
        await s.commit()
        return e.id
