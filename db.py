import os

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./data.db").strip()

# Render/Neon "postgres://" URL ko asyncpg ke liye convert karo
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

is_pg = DATABASE_URL.startswith("postgresql")

# Neon/Supabase jaise URL me ?sslmode=require hota hai — asyncpg ka connect()
# "sslmode" kwarg accept nahi karta (SQLAlchemy error deta hai). Query string hatao;
# SSL connect_args se lagta hai (neeche "ssl": "require").
if is_pg and "?" in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.split("?")[0]

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import UniqueConstraint, BigInteger, text

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"ssl": "require"} if is_pg else {},
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Playlist(Base):
    __tablename__ = "playlists"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]
    category: Mapped[str] = mapped_column(default="Other")
    desc: Mapped[str] = mapped_column(default="")
    emoji: Mapped[str] = mapped_column(default="🎬")
    thumb: Mapped[str] = mapped_column(default="", nullable=True)  # base64 jpeg
    created_at: Mapped[float] = mapped_column(default=0)


class Episode(Base):
    __tablename__ = "episodes"
    id: Mapped[int] = mapped_column(primary_key=True)
    playlist_id: Mapped[int] = mapped_column()
    season: Mapped[int] = mapped_column(default=1)
    ep_num: Mapped[int] = mapped_column(default=1)
    title: Mapped[str] = mapped_column(default="")
    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column()
    bot_index: Mapped[int] = mapped_column(default=0)
    size: Mapped[int] = mapped_column(default=0)
    duration: Mapped[int] = mapped_column(default=0)
    mime: Mapped[str] = mapped_column(default="video/mp4")
    views: Mapped[int] = mapped_column(default=0)
    ref: Mapped[str] = mapped_column(default="")  # kis link se aaya
    sources_json: Mapped[str] = mapped_column(default="", nullable=True)  # multi-quality sources
    created_at: Mapped[float] = mapped_column(default=0)


class TgPeer(Base):
    """Bot ki channel-memory (access hash) — deploy/restart ke baad restore hoti hai."""
    __tablename__ = "tg_peers"
    bot_index: Mapped[int] = mapped_column(primary_key=True)
    peer_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    access_hash: Mapped[int] = mapped_column(BigInteger)
    peer_type: Mapped[str] = mapped_column(default="channel")
    username: Mapped[str] = mapped_column(default="")
    updated_at: Mapped[float] = mapped_column(default=0)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    ext_id: Mapped[str] = mapped_column(unique=True)  # google sub ya demo:name
    name: Mapped[str] = mapped_column()
    email: Mapped[str] = mapped_column(default="")
    is_admin: Mapped[bool] = mapped_column(default=False)


class Comment(Base):
    __tablename__ = "comments"
    id: Mapped[int] = mapped_column(primary_key=True)
    playlist_id: Mapped[int]
    user_id: Mapped[int]
    user_name: Mapped[str]
    text: Mapped[str]
    created_at: Mapped[float] = mapped_column(default=0)


class Rating(Base):
    __tablename__ = "ratings"
    __table_args__ = (UniqueConstraint("playlist_id", "user_id", name="uq_rating"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    playlist_id: Mapped[int]
    user_id: Mapped[int]
    stars: Mapped[int]


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # migration: bade Telegram IDs ke liye BIGINT (Postgres/Neon)
        if is_pg:
            for stmt in [
                "ALTER TABLE episodes ALTER COLUMN chat_id TYPE BIGINT",
                "ALTER TABLE tg_peers ALTER COLUMN peer_id TYPE BIGINT",
                "ALTER TABLE tg_peers ALTER COLUMN access_hash TYPE BIGINT",
            ]:
                try:
                    await conn.execute(text(stmt))
                except Exception:
                    pass
