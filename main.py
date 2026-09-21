"""
AnimeFlix — asli app backend.
FastAPI + Pyrogram. Telegram storage + proxy streaming + Google sign-in.
"""
# --- Python 3.12+/3.13 + Pyrogram compat: event loop pre-create ---
import asyncio as _asyncio
try:
    _asyncio.get_event_loop()
except RuntimeError:
    _asyncio.set_event_loop(_asyncio.new_event_loop())
# -------------------------------------------------------------------
import base64
import hashlib
import hmac
import json
import os
import re
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, Response, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, func, delete

import tg
from db import init_db, SessionLocal, Playlist, Episode, User, Comment, Rating

APP_NAME = os.environ.get("APP_NAME", "AnimeFlix")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}
ADMIN_DEMO_NAME = os.environ.get("ADMIN_DEMO_NAME", "").strip()  # demo mode me admin ka naam
SECRET = os.environ.get("SECRET", "animeflix-secret-change-me")
MAX_THUMB_KB = 400  # base64 thumbnail limit

CHUNK = 1024 * 1024


@asynccontextmanager
async def lifespan(app):
    await init_db()
    await tg.start()
    yield
    await tg.stop()


app = FastAPI(title=APP_NAME, lifespan=lifespan)


# ---------------- auth utils ----------------

def make_token(uid: int) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"uid": uid, "exp": time.time() + 30 * 86400}).encode()
    ).decode().rstrip("=")
    sig = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return payload + "." + sig


def parse_token(tok: str):
    try:
        payload, sig = tok.split(".")
        if hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest() != sig:
            return None
        pad = "=" * (-len(payload) % 4)
        d = json.loads(base64.urlsafe_b64decode(payload + pad))
        return int(d["uid"]) if d.get("exp", 0) > time.time() else None
    except Exception:
        return None


async def get_user(request: Request) -> User | None:
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return None
    uid = parse_token(auth[7:])
    if uid is None:
        return None
    async with SessionLocal() as s:
        return await s.get(User, uid)


async def require_admin(request: Request) -> User:
    u = await get_user(request)
    if not u or not u.is_admin:
        raise HTTPException(status_code=403, detail="Sirf admin ke liye")
    return u


def pl_stats_row(pl, ecount, seasons, views, rating, rcount, last_added=0):
    return {
        "id": pl.id, "title": pl.title, "category": pl.category,
        "desc": pl.desc, "emoji": pl.emoji, "has_thumb": bool(pl.thumb),
        "episodes": ecount, "seasons": seasons, "views": views,
        "rating": round(rating, 1) if rcount else 0, "rating_count": rcount,
        "last_added": last_added or pl.created_at,
    }


# ---------------- config / auth ----------------

@app.get("/api/config")
async def api_config():
    return {
        "app_name": APP_NAME,
        "google_client_id": GOOGLE_CLIENT_ID,
        "demo_mode": not bool(GOOGLE_CLIENT_ID),
        "telegram_connected": tg.configured(),
        "channel_ready": (await tg.channel_ready()) if tg.clients else False,
    }


@app.post("/api/auth/google")
async def api_auth_google(request: Request):
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=400, detail="Google sign-in configured nahi hai")
    body = await request.json()
    cred = body.get("credential", "")
    try:
        from google.oauth2 import id_token as gid_token
        from google.auth.transport import requests as greq
        info = gid_token.verify_oauth2_token(cred, greq.Request(), GOOGLE_CLIENT_ID)
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Google token invalid: {str(e)[:100]}")
    email = (info.get("email") or "").lower()
    ext = "g:" + (info.get("sub") or email)
    async with SessionLocal() as s:
        u = (await s.execute(select(User).where(User.ext_id == ext))).scalar_one_or_none()
        if not u:
            u = User(ext_id=ext, name=info.get("name") or "User", email=email,
                     is_admin=email in ADMIN_EMAILS)
            s.add(u)
        else:
            u.is_admin = u.is_admin or (email in ADMIN_EMAILS)
        await s.commit()
        return {"token": make_token(u.id), "name": u.name, "is_admin": u.is_admin}


@app.post("/api/auth/demo")
async def api_auth_demo(request: Request):
    if GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=400, detail="Google sign-in use karo")
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Naam likho")
    ext = "d:" + name.lower()
    is_admin = bool(ADMIN_DEMO_NAME) and name.lower() == ADMIN_DEMO_NAME.lower()
    async with SessionLocal() as s:
        u = (await s.execute(select(User).where(User.ext_id == ext))).scalar_one_or_none()
        if not u:
            u = User(ext_id=ext, name=name, email="", is_admin=is_admin)
            s.add(u)
        else:
            u.is_admin = u.is_admin or is_admin
        await s.commit()
        return {"token": make_token(u.id), "name": u.name, "is_admin": u.is_admin}


@app.get("/api/me")
async def api_me(request: Request):
    u = await get_user(request)
    if not u:
        return {"user": None}
    return {"user": {"id": u.id, "name": u.name, "email": u.email, "is_admin": u.is_admin}}


# ---------------- playlists ----------------

@app.get("/api/playlists")
async def api_playlists():
    async with SessionLocal() as s:
        pls = (await s.execute(select(Playlist).order_by(Playlist.id))).scalars().all()
        ecount = dict((await s.execute(
            select(Episode.playlist_id, func.count(Episode.id)).group_by(Episode.playlist_id)
        )).all())
        seasons = dict((await s.execute(
            select(Episode.playlist_id, func.count(func.distinct(Episode.season))).group_by(Episode.playlist_id)
        )).all())
        views = dict((await s.execute(
            select(Episode.playlist_id, func.coalesce(func.sum(Episode.views), 0)).group_by(Episode.playlist_id)
        )).all())
        rates = (await s.execute(
            select(Rating.playlist_id, func.avg(Rating.stars), func.count(Rating.id)).group_by(Rating.playlist_id)
        )).all()
        rmap = {r[0]: (r[1] or 0, r[2]) for r in rates}
        lastadd = dict((await s.execute(
            select(Episode.playlist_id, func.max(Episode.created_at)).group_by(Episode.playlist_id)
        )).all())
    return [
        pl_stats_row(p, ecount.get(p.id, 0), seasons.get(p.id, 0),
                     views.get(p.id, 0), rmap.get(p.id, (0, 0))[0], rmap.get(p.id, (0, 0))[1],
                     lastadd.get(p.id, 0))
        for p in pls
    ]


def _ep_qualities(e):
    """Episode ke available quality labels (multi-quality)."""
    try:
        import json as _j
        srcs = _j.loads(e.sources_json or "[]")
        return [s.get("label", "Original") for s in srcs if isinstance(s, dict)]
    except Exception:
        return []


def ep_row(e):
    return {
        "id": e.id, "playlist_id": e.playlist_id, "season": e.season, "ep_num": e.ep_num,
        "title": e.title, "size": e.size, "duration": e.duration, "views": e.views,
        "has_link": bool(e.ref),
        "qualities": _ep_qualities(e),
    }


@app.get("/api/admin/stats")
async def api_admin_stats(request: Request):
    await require_admin(request)
    async with SessionLocal() as s:
        users = (await s.execute(select(func.count(User.id)))).scalar() or 0
        pls = (await s.execute(select(func.count(Playlist.id)))).scalar() or 0
        eps = (await s.execute(select(func.count(Episode.id)))).scalar() or 0
        views = (await s.execute(select(func.coalesce(func.sum(Episode.views), 0)))).scalar() or 0
        cmts = (await s.execute(select(func.count(Comment.id)))).scalar() or 0
        rts = (await s.execute(select(func.count(Rating.id)))).scalar() or 0
        sumv = func.coalesce(func.sum(Episode.views), 0)
        top = (await s.execute(
            select(Playlist.title, sumv).outerjoin(Episode, Episode.playlist_id == Playlist.id)
            .group_by(Playlist.id).order_by(sumv.desc()).limit(5)
        )).all()
        recent = (await s.execute(select(Comment).order_by(Comment.id.desc()).limit(5))).scalars().all()
    return {"users": users, "playlists": pls, "episodes": eps, "views": int(views),
            "comments": cmts, "ratings": rts,
            "top": [{"title": t, "views": int(v)} for t, v in top],
            "recent_comments": [{"name": c.user_name, "text": (c.text or "")[:100], "at": c.created_at} for c in recent]}


@app.get("/api/playlists/{pid}")
async def api_playlist_detail(pid: int, request: Request):
    async with SessionLocal() as s:
        pl = await s.get(Playlist, pid)
        if not pl:
            raise HTTPException(status_code=404, detail="Playlist nahi mili")
        eps = (await s.execute(
            select(Episode).where(Episode.playlist_id == pid)
            .order_by(Episode.season, Episode.ep_num)
        )).scalars().all()
        comments = (await s.execute(
            select(Comment).where(Comment.playlist_id == pid).order_by(Comment.id.desc())
        )).scalars().all()
        my_rating = 0
        u = await get_user(request)
        if u:
            r = (await s.execute(
                select(Rating).where(Rating.playlist_id == pid, Rating.user_id == u.id)
            )).scalar_one_or_none()
            my_rating = r.stars if r else 0
        all_rates = (await s.execute(
            select(Rating.stars).where(Rating.playlist_id == pid)
        )).scalars().all()

    seasons = {}
    for e in eps:
        seasons.setdefault(e.season, []).append(ep_row(e))
    return {
        "id": pl.id, "title": pl.title, "category": pl.category, "desc": pl.desc,
        "emoji": pl.emoji, "has_thumb": bool(pl.thumb),
        "seasons": [{"season": k, "episodes": v} for k, v in sorted(seasons.items())],
        "views": sum(e.views for e in eps), "episodes": len(eps),
        "rating": round(sum(all_rates) / len(all_rates), 1) if all_rates else 0,
        "rating_count": len(all_rates), "my_rating": my_rating,
        "comments": [
            {"id": c.id, "name": c.user_name, "text": c.text,
             "at": c.created_at} for c in comments
        ],
    }


def _clean_thumb(data_url: str) -> str:
    """data:image/jpeg;base64,.... — size limit check"""
    if not data_url.startswith("data:image/"):
        raise HTTPException(status_code=400, detail="Thumbnail image hi honi chahiye")
    if len(data_url) > MAX_THUMB_KB * 1024:
        raise HTTPException(status_code=400, detail="Thumbnail bahut badi hai — chhoti image do")
    return data_url


@app.post("/api/playlists")
async def api_create_playlist(request: Request):
    await require_admin(request)
    body = await request.json()
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Playlist ka title likho")
    thumb = body.get("thumb") or ""
    if thumb:
        thumb = _clean_thumb(thumb)
    async with SessionLocal() as s:
        pl = Playlist(
            title=title[:200], category=(body.get("category") or "Other")[:60],
            desc=(body.get("desc") or "")[:1000],
            emoji=(body.get("emoji") or "🎬")[:8], thumb=thumb, created_at=time.time(),
        )
        s.add(pl)
        await s.commit()
        return {"ok": True, "id": pl.id}


@app.patch("/api/playlists/{pid}")
async def api_update_playlist(pid: int, request: Request):
    await require_admin(request)
    body = await request.json()
    async with SessionLocal() as s:
        pl = await s.get(Playlist, pid)
        if not pl:
            raise HTTPException(status_code=404, detail="Playlist nahi mili")
        if "title" in body and body["title"].strip():
            pl.title = body["title"].strip()[:200]
        if "category" in body:
            pl.category = (body["category"] or "Other")[:60]
        if "desc" in body:
            pl.desc = (body["desc"] or "")[:1000]
        if "emoji" in body:
            pl.emoji = (body["emoji"] or "🎬")[:8]
        if "thumb" in body:
            pl.thumb = _clean_thumb(body["thumb"]) if body["thumb"] else ""
        await s.commit()
    return {"ok": True}


@app.delete("/api/playlists/{pid}")
async def api_delete_playlist(pid: int, request: Request):
    await require_admin(request)
    async with SessionLocal() as s:
        pl = await s.get(Playlist, pid)
        if not pl:
            raise HTTPException(status_code=404, detail="Playlist nahi mili")
        await s.execute(delete(Episode).where(Episode.playlist_id == pid))
        await s.execute(delete(Comment).where(Comment.playlist_id == pid))
        await s.execute(delete(Rating).where(Rating.playlist_id == pid))
        await s.delete(pl)
        await s.commit()
    return {"ok": True}


@app.get("/api/thumb/{pid}")
async def api_playlist_thumb(pid: int):
    async with SessionLocal() as s:
        pl = await s.get(Playlist, pid)
        if not pl or not pl.thumb:
            raise HTTPException(status_code=404)
    # pl.thumb = "data:image/jpeg;base64,..."
    try:
        b64 = pl.thumb.split(",", 1)[1]
        return Response(content=base64.b64decode(b64), media_type="image/jpeg")
    except Exception:
        raise HTTPException(status_code=404)


# ---------------- add via link (PROXY ka core) ----------------

@app.post("/api/add")
async def api_add(request: Request):
    await require_admin(request)
    if not tg.configured():
        raise HTTPException(status_code=503, detail="Telegram bots offline hain")
    body = await request.json()
    link = (body.get("link") or "").strip()
    season = max(1, int(body.get("season") or 1))
    ep_num = max(1, int(body.get("ep_num") or 1))
    title = (body.get("title") or "").strip()

    # ---- multi-quality links (480p/720p/1080p) + legacy single link ----
    def _qnum(lbl):
        m = re.search(r"(\d{3,4})\s*p", str(lbl), re.I)
        return int(m.group(1)) if m else 0

    items = []
    for it in (body.get("links") or []):
        l = (it.get("link") or "").strip()
        if l:
            items.append(((it.get("label") or "Original"), l))
    if not items and link:
        items.append(("Original", link))
    if not items:
        raise HTTPException(status_code=400, detail="Kam se kam ek Telegram link daalo (480p/720p/1080p)")
    # sabse pehle high quality (Auto default = best)
    items.sort(key=lambda x: _qnum(x[0]), reverse=True)

    sources = []
    primary = None
    for label, l in items:
        result, err, _ = await tg.fetch_episode_media(l)
        if err:
            raise HTTPException(status_code=400, detail=f"{label}: {err}")
        client, msg, media = result
        bot_index = None
        for i, c in tg.clients.items():
            if c is client:
                bot_index = i
                break
        sources.append({
            "label": label, "chat_id": msg.chat.id, "message_id": msg.id,
            "bot_index": bot_index or 0,
            "size": int(getattr(media, "file_size", 0) or 0),
            "duration": int(getattr(media, "duration", 0) or 0),
            "mime": getattr(media, "mime_type", "") or "video/mp4",
        })
        if primary is None:
            primary = (client, msg, media, l)

    # playlist: existing id ya new
    if body.get("new_playlist"):
        np_ = body["new_playlist"]
        t = (np_.get("title") or "").strip()
        if not t:
            raise HTTPException(status_code=400, detail="New playlist ka title likho")
        thumb = np_.get("thumb") or ""
        if thumb:
            thumb = _clean_thumb(thumb)
        async with SessionLocal() as s:
            pl = Playlist(
                title=t[:200], category=(np_.get("category") or "Other")[:60],
                desc=(np_.get("desc") or "")[:1000],
                emoji=(np_.get("emoji") or "🎬")[:8], thumb=thumb, created_at=time.time(),
            )
            s.add(pl)
            await s.commit()
            playlist_id = pl.id
    else:
        playlist_id = int(body.get("playlist_id") or 0)
        async with SessionLocal() as s:
            if not await s.get(Playlist, playlist_id):
                raise HTTPException(status_code=400, detail="Playlist select karo")

    client, msg, media, ref_link = primary
    bot_index = None
    for i, c in tg.clients.items():
        if c is client:
            bot_index = i
            break
    ep_id = await tg.upsert_episode(msg, media, playlist_id, season, ep_num,
                                     title, bot_index, ref=ref_link)
    async with SessionLocal() as s:
        obj = await s.get(Episode, ep_id)
        if obj:
            obj.sources_json = json.dumps(sources)
            await s.commit()
    warning = ""
    fname = (getattr(media, "file_name", "") or "").lower()
    if fname.endswith((".mkv", ".avi", ".flv", ".wmv", ".ts")):
        warning = ("Ye video ka format browser me play NAHI hoga! "
                    "MP4 (H.264) version upload karke uska link add karo.")
    return {"ok": True, "episode_id": ep_id, "playlist_id": playlist_id, "warning": warning}


# ---------------- streaming (PROXY) ----------------

def _parse_range(range_header: str, total: int):
    m = re.match(r"bytes=(\d*)-(\d*)", range_header.strip())
    if not m:
        return None
    start_s, end_s = m.groups()
    start = int(start_s) if start_s else 0
    end = int(end_s) if end_s else total - 1
    end = min(end, total - 1)
    if start >= total or start > end:
        return None
    return start, end


@app.get("/api/stream/{ep_id}")
async def api_stream(ep_id: int, request: Request, download: int = 0, q: str = ""):
    async with SessionLocal() as s:
        e = await s.get(Episode, ep_id)
    if not e:
        raise HTTPException(status_code=404, detail="Video nahi mili")
    # multi-quality source chuno (?q=720p waghera)
    chat_id, message_id, bot_index = e.chat_id, e.message_id, e.bot_index
    try:
        srcs = json.loads(e.sources_json or "[]")
    except Exception:
        srcs = []
    if srcs:
        chosen = None
        if q:
            for s_ in srcs:
                if str(s_.get("label", "")).lower() == str(q).lower():
                    chosen = s_
                    break
        s_ = chosen or srcs[0]
        chat_id = int(s_["chat_id"]); message_id = int(s_["message_id"])
        bot_index = int(s_.get("bot_index", 0))

    client = tg.get_client(bot_index)
    if client is None:
        raise HTTPException(status_code=503, detail="Storage bot offline hai")

    msg = await client.get_messages(chat_id, message_id)
    media = msg.video if msg.video else (msg.document if msg.document else None)
    if media is None:
        raise HTTPException(status_code=404, detail="Video Telegram pe nahi mili "
                            "(message delete ho gaya ho sakta hai)")

    total = int(getattr(media, "file_size", 0) or e.size or 0)
    mime = e.mime or "video/mp4"
    range_header = request.headers.get("range")
    fname = re.sub(r"[^A-Za-z0-9 ._-]", "_", e.title or "video")[:80] or "video"

    # views: sirf initial request pe badhao
    if not range_header or range_header.startswith("bytes=0-"):
        async with SessionLocal() as s:
            obj = await s.get(Episode, ep_id)
            obj.views = obj.views + 1
            await s.commit()

    dl_headers = {}
    if download:
        dl_headers["Content-Disposition"] = f'attachment; filename="{fname}.mp4"'

    if range_header and total > 0:
        rng = _parse_range(range_header, total)
        if rng is None:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{total}"})
        start, end = rng
        limit = end - start + 1

        async def gen():
            async for chunk in client.stream_media(msg, limit=limit, offset=start):
                yield chunk

        return StreamingResponse(
            gen(), status_code=206, media_type=mime,
            headers={
                "Content-Range": f"bytes {start}-{end}/{total}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(limit),
                **dl_headers,
            },
        )

    async def gen():
        async for chunk in client.stream_media(msg):
            yield chunk

    return StreamingResponse(
        gen(), media_type=mime,
        headers={"Accept-Ranges": "bytes", **dl_headers},
    )


@app.get("/api/epthumb/{ep_id}")
async def api_ep_thumb(ep_id: int):
    try:
        async with SessionLocal() as s:
            e = await s.get(Episode, ep_id)
        if not e:
            raise HTTPException(status_code=404)
        client = tg.get_client(e.bot_index)
        if client is None:
            raise HTTPException(status_code=404)
        msg = await client.get_messages(e.chat_id, e.message_id)
        thumbs = []
        if msg.video:
            thumbs = msg.video.thumbs or []
        elif msg.document:
            thumbs = msg.document.thumbs or []
        if not thumbs:
            raise HTTPException(status_code=404)
        bio = await client.download_media(thumbs[-1], in_memory=True)
        return Response(content=bio.getvalue(), media_type="image/jpeg")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=404)


# ---------------- comments & ratings ----------------

@app.post("/api/comments")
async def api_add_comment(request: Request):
    u = await get_user(request)
    if not u:
        raise HTTPException(status_code=401, detail="Pehle sign in karo")
    body = await request.json()
    pid = int(body.get("playlist_id") or 0)
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Kuch likho")
    async with SessionLocal() as s:
        if not await s.get(Playlist, pid):
            raise HTTPException(status_code=404, detail="Playlist nahi mili")
        c = Comment(playlist_id=pid, user_id=u.id, user_name=u.name,
                    text=text[:500], created_at=time.time())
        s.add(c)
        await s.commit()
        return {"ok": True, "id": c.id}


@app.post("/api/rate")
async def api_rate(request: Request):
    u = await get_user(request)
    if not u:
        raise HTTPException(status_code=401, detail="Pehle sign in karo")
    body = await request.json()
    pid = int(body.get("playlist_id") or 0)
    stars = int(body.get("stars") or 0)
    if not (1 <= stars <= 5):
        raise HTTPException(status_code=400, detail="1 se 5 star")
    async with SessionLocal() as s:
        r = (await s.execute(
            select(Rating).where(Rating.playlist_id == pid, Rating.user_id == u.id)
        )).scalar_one_or_none()
        if r:
            r.stars = stars
        else:
            s.add(Rating(playlist_id=pid, user_id=u.id, stars=stars))
        await s.commit()
    return {"ok": True}


# ---------------- frontend (flat files) ----------------
STATIC_FILES = {
    "/": ("index.html", "text/html"),
    "/index.html": ("index.html", "text/html"),
    "/app.js": ("app.js", "text/javascript"),
    "/style.css": ("style.css", "text/css"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/sw.js": ("sw.js", "text/javascript"),
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
}
ROOT = os.path.dirname(os.path.abspath(__file__))

def _serve(fname, mime):
    async def _f():
        return FileResponse(os.path.join(ROOT, fname), media_type=mime)
    return _f

for _p, (_fn, _m) in STATIC_FILES.items():
    app.add_api_route(_p, _serve(_fn, _m), methods=["GET"], include_in_schema=False)
