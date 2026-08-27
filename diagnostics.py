"""Production-диагностика Railway. Этот router подключается ПЕРВЫМ."""
from __future__ import annotations

import logging
import uuid

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import ErrorEvent, Message
from sqlalchemy import select

import keyboards as kb
from config import ADMIN_USERNAMES, BOT_VERSION, BTN_MANUAL, RAILWAY_COMMIT
from database import (
    MinerSession, SessionMaker, User, database_health, get_game_cfg,
    get_or_create_user,
)
from games import MINE

router = Router(name="diagnostics")
log = logging.getLogger(__name__)


def _admin(username: str | None) -> bool:
    return bool(username) and username.lower().lstrip("@") in ADMIN_USERNAMES


def _fmt(n) -> str:
    s = f"{n:f}" if hasattr(n, "as_tuple") else str(n)
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


@router.message(Command("version"))
async def version(message: Message):
    await message.answer(
        f"🤖 Версия: <code>{BOT_VERSION}</code>\n"
        f"Railway commit: <code>{RAILWAY_COMMIT}</code>\n"
        f"Miner: <b>v7 · PostgreSQL FSM · кнопка ручного ввода</b>",
        parse_mode="HTML",
    )


@router.message(Command("health"))
async def health(message: Message):
    try:
        h = await database_health()
        await message.answer(
            f"{'✅ OK' if h.get('ok') else '❌ BROKEN'}\n"
            f"version: <code>{BOT_VERSION}</code>\n"
            f"commit: <code>{RAILWAY_COMMIT}</code>\n"
            f"users.balance: <code>{h.get('users_balance_type')}</code>\n"
            f"miner.bet: <code>{h.get('miner_bet_type')}</code>\n"
            f"PostgreSQL FSM: <b>{'OK' if h.get('fsm') else 'MISSING'}</b>\n"
            f"missing: <code>{h.get('missing')}</code>",
            parse_mode="HTML",
        )
    except Exception as exc:
        code = uuid.uuid4().hex[:8]
        log.error("health error id=%s", code,
                  exc_info=(type(exc), exc, exc.__traceback__))
        await message.answer(
            f"❌ /health error <code>{code}</code>: {type(exc).__name__}",
            parse_mode="HTML",
        )


@router.message(Command("miner_debug"))
async def miner_debug(message: Message, state: FSMContext):
    tg = message.from_user
    if not tg:
        return
    user, _ = await get_or_create_user(
        tg.id, tg.username or f"user{tg.id}", None,
    )
    state_name = await state.get_state()
    data = await state.get_data()
    cfg = await get_game_cfg("miner")
    h = await database_health()
    async with SessionMaker() as s:
        sessions = (await s.execute(
            select(MinerSession).where(
                MinerSession.user_id == user.user_id,
                MinerSession.status == "active",
            )
        )).scalars().all()
    active_lines = []
    for x in sessions:
        size = int(len(x.board) ** 0.5)
        mines = sum(1 for c in x.board if c == MINE)
        active_lines.append(
            f"id={x.id}; {size}x{size}; mines={mines}; bet={_fmt(x.bet)}; "
            f"opened={len(x.revealed)}; mult={x.multiplier/10000:.4f}"
        )
    await message.answer(
        f"🧪 <b>MINER DEBUG</b>\n"
        f"version: <code>{BOT_VERSION}</code>\n"
        f"commit: <code>{RAILWAY_COMMIT}</code>\n"
        f"DB: <b>{'OK' if h.get('ok') else 'BROKEN'}</b>\n"
        f"FSM: <code>{state_name or 'none'}</code>\n"
        f"FSM data keys: <code>{sorted(data.keys())}</code>\n"
        f"manual button: <code>{BTN_MANUAL}</code>\n"
        f"growth: <code>{cfg.get('growth')}</code> · cap: <code>{cfg.get('cap')}</code>\n"
        f"active sessions: <code>{len(sessions)}</code>\n"
        + ("\n".join(f"<code>{x}</code>" for x in active_lines) if active_lines else "—"),
        parse_mode="HTML",
        reply_markup=kb.main_menu_kb(is_admin=_admin(user.username)),
    )


async def global_error_handler(event: ErrorEvent):
    code = uuid.uuid4().hex[:8]
    exc = event.exception
    log.error(
        "UNHANDLED id=%s update_id=%s error=%s",
        code, event.update.update_id, repr(exc),
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    try:
        if event.update.callback_query:
            await event.update.callback_query.answer(
                f"Ошибка {code}. Напиши /miner_debug", show_alert=True,
            )
        elif event.update.message:
            await event.update.message.answer(
                f"❌ Ошибка <code>{code}</code>. Отправь <code>/miner_debug</code>.",
                parse_mode="HTML",
            )
    except Exception:
        pass
    return True
