# AnimeFlix — Telegram Storage + Proxy Streaming wala Video App

Apna video platform: videos **tumhare Telegram channel** me store hote hain, aur **app ke andar hi chalte hain** — play bhi, download bhi. Server **proxy** ban kar Telegram se video stream karta hai (browser seedha Telegram se baat nahi kar sakta, isi liye ye zaroori hai).

**Features:** Playlist → Season → Episode system · search (sirf playlists) · playlist thumbnails (upload) · views · ratings · comments · continue watching · watch later · auto-next episode · Google Sign-in · channel pe video bhejo to AUTO-ADD · PWA (Android pe install hota hai) · zero ads.

---

## Setup (ek baar, ~30 min)

### 1. Telegram credentials (free)
- **my.telegram.org** → API development tools → app banao → **api_id** + **api_hash** note karo

### 2. Storage channel
- Apna **private channel** banao (ya purana use karo)
- **Apne saare 6 bots ko channel me ADMIN banao** (sabhi 6 — round-robin streaming ke liye)
- Channel ka ID chahiye hoga: channel ka koi bhi message **apne kisi bot ko forward** karo → bot reply me ID dega (`-100...` wala)

### 3. Apni Telegram user ID
- **@userinfobot** ko /start karo → ID note karo (sirf tum videos add kar paoge)

### 4. Database (free, permanent)
- **neon.tech** → account → project → **connection string** copy karo (`postgresql://...`)

### 5. Render pe deploy (free)
1. Ye folder GitHub repo me upload karo
2. **render.com** → New → **Web Service** → repo connect karo
3. Runtime: Python 3 · Build: `pip install -r requirements.txt && (pip install tgcrypto || echo skipped)`
   Start: `uvicorn backend.main:app --host 0.0.0.0 --port $PORT` · Plan: **Free**
4. Environment variables:

| Variable | Value |
|---|---|
| `API_ID` | Step 1 ka api_id |
| `API_HASH` | Step 1 ka api_hash |
| `BOT_TOKENS` | Saare 6 bot tokens comma se: `123:abc,456:def,...` |
| `STORAGE_CHANNEL_ID` | Channel ID (Step 2, `-100...` wala) |
| `ADMIN_TG_IDS` | Tumhari user ID (Step 3) |
| `DATABASE_URL` | Neon connection string (Step 4) |
| `APP_NAME` | (optional) App ka naam, jaise "AnimeFlix" |
| `SECRET` | koi bhi lamba random string (login tokens ke liye) |
| `ADMIN_DEMO_NAME` | (agar Google nahi lagaya) admin ka login naam |
| `GOOGLE_CLIENT_ID` | (optional) Google Sign-in — niche dekho |
| `ADMIN_EMAILS` | (Google mode me) admin emails comma se |

5. Deploy → 2-3 min me live: `https://tumhara-app.onrender.com`

### 6. Google Sign-in (optional but recommended)
1. **console.cloud.google.com** → naya project → "OAuth consent screen" (External) →
   "Credentials" → "Create Credentials" → **OAuth Client ID** → Web application
2. Authorized JavaScript origins me apna app URL daalo: `https://tumhara-app.onrender.com`
3. Client ID copy karke Render me `GOOGLE_CLIENT_ID` me daalo
4. `ADMIN_EMAILS` me apni Gmail daalo — wahi admin ban jayega

## Videos add karne ke 3 tarike

1. **Seedha channel me video bhejo** (sabse aasan) — app me **"My Uploads"** playlist me khud aa jayegi (auto)
2. **Add Video (app me)** — kisi bhi channel message ka link paste karo:
   `https://t.me/c/2700515710/49` — playlist + season + episode choose karke add karo.
   Wahi link dobara dekar episode ko dusri playlist/season me **move** bhi kar sakte ho.
3. **Bot ko video bhejo** — kisi apne bot ko Telegram me video bhejo, wo channel me save karke app me add kar dega

> **Important:** Link wala tarika sirf un channels pe chalega jahan tumhare **bots admin** hon.
> `t.me/bot?start=...` wale filestore-bot links **nahi** chalenge (file us bot ke private chat me hoti hai) —
> uski jagah us video ka **channel message link** (t.me/c/...) use karo.

## Rozana
- Website kholo → playlist → episode → play. Prev/Next/Download sab player page pe hain
- 30 sec me auto-refresh — channel pe nayi video app me khud dikh jayegi
- Android: Chrome → menu (⋮) → **"Add to Home screen"** — proper app ban jayegi

## Common problems

| Problem | Fix |
|---|---|
| "Telegram env vars missing" | Render me API_ID, API_HASH, BOT_TOKENS, STORAGE_CHANNEL_ID check karo |
| Link add karte pe "channel accessible nahi" | Us channel me saare 6 bots ko **admin** banao |
| Video nahi chal rahi | Message delete hua ho sakta hai, ya file MKV/AVI hai — **MP4 (H.264)** best hai |
| Pehli baar page slow | Render free idle pe sleep karta hai — 30-50 sec normal hai (UptimeRobot ping laga sakte ho) |
| Google button nahi dikh raha | `GOOGLE_CLIENT_ID` set karo + origins me app URL daalo |

## Zaroori baatein
- Videos **public** dikhti hain — copyright content (pirated movies) upload karne pe Telegram/hosting block kar sakta hai. Apna content safest hai.
- Neon DB sirf **list** rakhti hai — asli video Telegram me hai. Telegram se message delete = app se bhi video gayi.
- Agar bot tokens leak ho jaye to BotFather se /revoke karke naya daalo.

## Local test
```bash
pip install -r requirements.txt
uvicorn backend.main:app --reload   # http://localhost:8000
```

## Tech
FastAPI + Pyrogram (MTProto proxy streaming — 20MB Bot API limit bypass) · SQLAlchemy (SQLite/Postgres) · Vanilla JS PWA · Neon free DB · Render free hosting
