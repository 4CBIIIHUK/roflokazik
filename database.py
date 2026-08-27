"""SQLAlchemy async-модели + пул подключений."""
from __future__ import annotations

import copy
import secrets
import string
from datetime import datetime, date
from typing import Any, Optional

from decimal import Decimal

from sqlalchemy import (
    BigInteger, Boolean, Date, Integer, Numeric, String, Text, DateTime,
    select, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import (
    AsyncSession, async_sessionmaker, create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from config import (
    DATABASE_URL, DEFAULT_GAME_SETTINGS, REF_BONUS_REGISTER, START_BALANCE, START_STARS,
)

engine = create_async_engine(DATABASE_URL, pool_pre_ping=True, pool_size=10)
SessionMaker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # NUMERIC(40,10): дробные значения (0.0001) и гиганты (1e21) без потери точности
    balance: Mapped[Decimal] = mapped_column(Numeric(40, 10), default=START_BALANCE, nullable=False)
    stars: Mapped[int] = mapped_column(Integer, default=START_STARS, nullable=False)
    games_played: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_wins: Mapped[Decimal] = mapped_column(Numeric(40, 10), default=0, nullable=False)
    total_losses: Mapped[Decimal] = mapped_column(Numeric(40, 10), default=0, nullable=False)
    referrer_id: Mapped[Optional[int]] = mapped_column(Integer)
    referral_code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    first_game_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    reached_10k_bonus_given: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    active_buffs: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    last_bonus_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    tg_id: Mapped[Optional[int]] = mapped_column(BigInteger, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class Transaction(Base):
    __tablename__ = "transactions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(40, 10), nullable=False)
    meta: Mapped[Optional[dict]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class Referral(Base):
    __tablename__ = "referrals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    referrer_id: Mapped[int] = mapped_column(Integer, nullable=False)
    referred_id: Mapped[int] = mapped_column(Integer, nullable=False)
    level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    chain_path: Mapped[list] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class ReferralBonus(Base):
    __tablename__ = "referral_bonuses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(40, 10), nullable=False)
    loss_amount: Mapped[Decimal] = mapped_column(Numeric(40, 10), nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class Cashback(Base):
    __tablename__ = "cashback"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    total_losses: Mapped[Decimal] = mapped_column(Numeric(40, 10), default=0, nullable=False)
    cashback_amount: Mapped[Decimal] = mapped_column(Numeric(40, 10), default=0, nullable=False)
    claimed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class Purchase(Base):
    __tablename__ = "purchases"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    buff_type: Mapped[str] = mapped_column(String(50), nullable=False)
    stars_cost: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class PromoCode(Base):
    __tablename__ = "promo_codes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False)
    used_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class PromoUse(Base):
    __tablename__ = "promo_uses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    promo_id: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class BotSetting(Base):
    __tablename__ = "bot_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class MinerSession(Base):
    __tablename__ = "miner_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    bet: Mapped[Decimal] = mapped_column(Numeric(40, 10), nullable=False)
    board: Mapped[list] = mapped_column(JSONB, nullable=False)
    revealed: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # множитель ×10000 (fixed-point): 12345 = ×1.2345
    multiplier: Mapped[int] = mapped_column(Integer, default=10000, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class BotFSM(Base):
    """Персистентный aiogram FSM: состояние ручного ввода переживает рестарт Railway."""
    __tablename__ = "bot_fsm"
    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    state: Mapped[Optional[str]] = mapped_column(String(255))
    data: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


def _make_ref_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "ref_" + "".join(secrets.choice(alphabet) for _ in range(8))


async def get_or_create_user(
    tg_id: int, username: str | None, ref_code: str | None = None,
) -> tuple[User, bool]:
    uname = (username or f"user{tg_id}").lower().lstrip("@")[:64]
    async with SessionMaker() as s:
        u = (await s.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
        if u:
            if u.username != uname:
                # try rename if free
                conflict = (await s.execute(
                    select(User).where(User.username == uname, User.user_id != u.user_id)
                )).scalar_one_or_none()
                if not conflict:
                    u.username = uname
                    await s.commit()
            return u, False

        u = (await s.execute(select(User).where(User.username == uname))).scalar_one_or_none()
        if u:
            u.tg_id = tg_id
            await s.commit()
            return u, False

        referrer: Optional[User] = None
        if ref_code:
            referrer = (
                await s.execute(select(User).where(User.referral_code == ref_code))
            ).scalar_one_or_none()

        new_user = User(
            username=uname,
            tg_id=tg_id,
            referral_code=_make_ref_code(),
            referrer_id=referrer.user_id if referrer else None,
            active_buffs={},
        )
        s.add(new_user)
        await s.flush()

        if referrer:
            chain: list[int] = []
            current: Optional[User] = referrer
            seen: set[int] = set()
            while current and current.user_id not in seen:
                seen.add(current.user_id)
                chain.append(current.user_id)
                if not current.referrer_id:
                    break
                current = (
                    await s.execute(select(User).where(User.user_id == current.referrer_id))
                ).scalar_one_or_none()
            for i, uid in enumerate(chain):
                s.add(Referral(
                    referrer_id=uid,
                    referred_id=new_user.user_id,
                    level=i + 1,
                    chain_path=chain[: i + 1],
                ))
            referrer.balance = int(referrer.balance) + REF_BONUS_REGISTER
            s.add(Transaction(
                user_id=referrer.user_id,
                type="ref_bonus",
                amount=REF_BONUS_REGISTER,
                meta={"reason": "register", "referred": new_user.user_id},
            ))

        await s.commit()
        await s.refresh(new_user)
        return new_user, True


async def get_user(tg_id: int) -> Optional[User]:
    async with SessionMaker() as s:
        return (await s.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()


async def get_user_by_username(username: str) -> Optional[User]:
    uname = username.lower().lstrip("@")
    async with SessionMaker() as s:
        return (await s.execute(select(User).where(User.username == uname))).scalar_one_or_none()


async def get_setting(key: str, default: Any = None) -> Any:
    async with SessionMaker() as s:
        row = (await s.execute(select(BotSetting).where(BotSetting.key == key))).scalar_one_or_none()
        return row.value if row else default


async def set_setting(key: str, value: Any) -> None:
    async with SessionMaker() as s:
        row = (await s.execute(select(BotSetting).where(BotSetting.key == key))).scalar_one_or_none()
        if row:
            row.value = value
            row.updated_at = datetime.utcnow()
        else:
            s.add(BotSetting(key=key, value=value))
        await s.commit()


async def get_game_settings() -> dict:
    """Возвращает полный dict настроек всех мини-игр (дефолты + оверрайды из БД)."""
    stored = await get_setting("game_settings", None)
    base = copy.deepcopy(DEFAULT_GAME_SETTINGS)
    if isinstance(stored, dict):
        for game, conf in stored.items():
            if game in base and isinstance(conf, dict):
                base[game].update(conf)
            elif isinstance(conf, dict):
                base[game] = conf
    return base


async def save_game_settings(settings: dict) -> None:
    await set_setting("game_settings", settings)


async def get_game_cfg(game: str) -> dict:
    all_cfg = await get_game_settings()
    return all_cfg.get(game, copy.deepcopy(DEFAULT_GAME_SETTINGS.get(game, {})))


async def update_game_cfg(game: str, patch: dict) -> dict:
    all_cfg = await get_game_settings()
    cur = all_cfg.get(game, {})
    cur.update(patch)
    all_cfg[game] = cur
    await save_game_settings(all_cfg)
    return cur


# Миграции существующих БД (create_all НЕ добавляет колонки в готовые таблицы!)
_MIGRATION_SQL = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS tg_id BIGINT",
    "CREATE INDEX IF NOT EXISTS ix_users_tg_id ON users (tg_id)",
    "ALTER TABLE users ALTER COLUMN balance TYPE NUMERIC(40,10) USING balance::numeric",
    "ALTER TABLE users ALTER COLUMN balance SET DEFAULT 1000",
    "ALTER TABLE users ALTER COLUMN total_wins TYPE NUMERIC(40,10) USING total_wins::numeric",
    "ALTER TABLE users ALTER COLUMN total_losses TYPE NUMERIC(40,10) USING total_losses::numeric",
    "ALTER TABLE transactions ALTER COLUMN amount TYPE NUMERIC(40,10) USING amount::numeric",
    "ALTER TABLE referral_bonuses ALTER COLUMN amount TYPE NUMERIC(40,10) USING amount::numeric",
    "ALTER TABLE referral_bonuses ALTER COLUMN loss_amount TYPE NUMERIC(40,10) USING loss_amount::numeric",
    "ALTER TABLE cashback ALTER COLUMN total_losses TYPE NUMERIC(40,10) USING total_losses::numeric",
    "ALTER TABLE cashback ALTER COLUMN cashback_amount TYPE NUMERIC(40,10) USING cashback_amount::numeric",
    "ALTER TABLE miner_sessions ALTER COLUMN bet TYPE NUMERIC(40,10) USING bet::numeric",
    "ALTER TABLE miner_sessions ALTER COLUMN multiplier SET DEFAULT 10000",
]


async def database_health() -> dict:
    """Строгая диагностика схемы для startup и команд /health, /miner_debug."""
    required = {
        "users": {"user_id", "tg_id", "balance", "active_buffs"},
        "miner_sessions": {"id", "user_id", "bet", "board", "revealed", "multiplier", "status"},
        "bot_settings": {"key", "value"},
        "bot_fsm": {"key", "state", "data"},
    }
    async with engine.connect() as conn:
        rows = (await conn.execute(text(
            "SELECT table_name, column_name, data_type "
            "FROM information_schema.columns WHERE table_schema='public'"
        ))).all()
        found: dict[str, set[str]] = {}
        types: dict[str, str] = {}
        for table, column, data_type in rows:
            found.setdefault(table, set()).add(column)
            types[f"{table}.{column}"] = data_type
        missing = []
        for table, cols in required.items():
            if table not in found:
                missing.append(f"table:{table}")
            else:
                missing.extend(f"{table}.{c}" for c in sorted(cols - found[table]))
        await conn.execute(text("SELECT 1"))
    ok_numeric = types.get("users.balance") == "numeric" and types.get("miner_sessions.bet") == "numeric"
    return {
        "ok": not missing and ok_numeric,
        "missing": missing,
        "users_balance_type": types.get("users.balance"),
        "miner_bet_type": types.get("miner_sessions.bet"),
        "fsm": "bot_fsm" in found,
    }


async def _cancel_legacy_miner_sessions() -> int:
    """Возвращает ставки и отменяет старые поля с boom/x1.2/... ровно один раз."""
    async with engine.begin() as conn:
        legacy = (await conn.execute(text("""
            SELECT id, user_id, bet
            FROM miner_sessions ms
            WHERE status = 'active'
              AND EXISTS (
                SELECT 1 FROM jsonb_array_elements_text(ms.board) cell
                WHERE cell NOT IN ('mine', 'gem')
              )
            FOR UPDATE
        """))).all()
        for session_id, user_id, bet in legacy:
            await conn.execute(text(
                "UPDATE users SET balance = balance + :bet WHERE user_id = :uid"
            ), {"bet": bet, "uid": user_id})
            await conn.execute(text("""
                INSERT INTO transactions (user_id, type, amount, meta, created_at)
                VALUES (:uid, 'miner_refund', :bet,
                        jsonb_build_object('session', :sid, 'reason', 'legacy_miner_migration'),
                        CURRENT_TIMESTAMP)
            """), {"uid": user_id, "bet": bet, "sid": session_id})
            await conn.execute(text("""
                UPDATE miner_sessions
                SET status = 'cancelled', finished_at = CURRENT_TIMESTAMP
                WHERE id = :sid
            """), {"sid": session_id})
    return len(legacy)


async def init_db() -> dict:
    import logging
    log = logging.getLogger(__name__)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    for stmt in _MIGRATION_SQL:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(stmt))
        except Exception as exc:
            # НЕ молчим: в Railway Logs будет конкретный SQL и ошибка.
            log.exception("DB migration failed: %s", stmt)
            raise RuntimeError(f"Миграция БД не выполнена: {stmt}") from exc
    cancelled = await _cancel_legacy_miner_sessions()
    if cancelled:
        log.warning("Cancelled %s legacy Miner sessions and refunded bets", cancelled)
    existing = await get_setting("game_settings", None)
    if existing is None:
        await save_game_settings(copy.deepcopy(DEFAULT_GAME_SETTINGS))
    health = await database_health()
    if not health["ok"]:
        raise RuntimeError(f"Схема PostgreSQL не готова: {health}")
    log.info("Database schema verified: %s", health)
    return health


__all__ = [
    "SessionMaker", "engine", "init_db",
    "User", "Transaction", "Referral", "ReferralBonus",
    "Cashback", "Purchase", "PromoCode", "PromoUse",
    "BotSetting", "BotFSM", "MinerSession",
    "get_or_create_user", "get_user", "get_user_by_username",
    "get_setting", "set_setting", "database_health",
    "get_game_settings", "save_game_settings", "get_game_cfg", "update_game_cfg",
    "select", "func",
]
