"""Все обработчики: Reply-кнопки, inline-майнер, Stars, админ-настройки игр."""
from __future__ import annotations

import re
import secrets
import string
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, ChatMemberUpdated, LabeledPrice, Message, PreCheckoutQuery,
)
from sqlalchemy import desc, select

import keyboards as kb
from config import (
    ADMIN_USERNAMES, BONUS_COOLDOWN_SEC, BTN_ADMIN, BTN_BACK, BTN_BALANCE,
    BTN_BJ, BTN_BONUS, BTN_CASHBACK, BTN_DICE, BTN_GAMES, BTN_HL, BTN_LADDER,
    BTN_MANUAL, BTN_MENU, BTN_MINER, BTN_MINER_RESET, BTN_PROMO, BTN_REFERRAL,
    BTN_ROULETTE, BTN_SHOP, BTN_SLOTS, BTN_STATS, BTN_TOP, BUFFS,
    CASHBACK_MAX_DAILY, CASHBACK_MIN,
)
from database import (
    MinerSession, PromoCode, PromoUse, Purchase, Referral, SessionMaker,
    Transaction, User, get_game_cfg, get_game_settings, get_or_create_user,
    get_user_by_username, update_game_cfg,
)
from economy import (
    D, NotEnoughFunds, active_buffs, bonus_amount, claim_cashback,
    compute_today_cashback, credit_win, debit_bet, register_loss,
)
from games import (
    MINE, MINER_MAX_SIZE, MINER_MIN_SIZE, format_hand, generate_miner_board,
    guarantee_first_safe, hand_value, miner_multiplier, new_shoe, play_dice,
    play_highlow, play_ladder, play_roulette, roulette_color, spin_slots,
)

router = Router()


# ==================== FSM ====================
class PlayFSM(StatesGroup):
    choose_bet = State()          # data: game — пресет или «ввести вручную»
    choose_bet_manual = State()   # data: game — ручной ввод ставки текстом
    ladder_step = State()         # data: bet
    highlow_guess = State()       # data: bet
    bj_play = State()             # data: bet, player, dealer, shoe
    dice_pick = State()           # data: bet
    roulette_type = State()       # data: bet


class MinerFSM(StatesGroup):
    """Настройка партии Майнера: размер → мины → ставка.

    Каждый шаг имеет ДВА состояния:
      • <шаг>        — ждём нажатия кнопки-пресета
      • <шаг>_manual — после «✏️ Ввести вручную»: ждём число текстом
    """
    size = State()
    size_manual = State()
    mines = State()               # data: size
    mines_manual = State()        # data: size
    bet = State()                 # data: size, mines
    bet_manual = State()          # data: size, mines
    play = State()                # data: sess_id, size — координаты (большие поля)


class PromoFSM(StatesGroup):
    waiting_code = State()


class AdminFSM(StatesGroup):
    promo_amount = State()
    promo_uses = State()
    promo_desc = State()
    give_username = State()
    give_amount = State()
    stars_username = State()
    stars_amount = State()
    cfg_game = State()            # data: game
    cfg_bets = State()            # data: game  — ввод списка ставок
    cfg_ladder_steps = State()
    cfg_coef = State()            # highlow/dice coef


# ==================== HELPERS ====================
def fmt(n) -> str:
    """Форматирует int/float/Decimal: 1 234 567 или 0.0001, без хвостовых нулей."""
    try:
        d = n if isinstance(n, Decimal) else Decimal(str(n))
    except (InvalidOperation, ValueError):
        return str(n)
    sign = "-" if d < 0 else ""
    d = abs(d)
    i = int(d)
    frac = d - i
    s = f"{i:,}".replace(",", " ")
    if frac:
        fs = f"{frac:.10f}".rstrip("0").rstrip(".")
        if fs.startswith("0."):
            s += fs[1:]
    return sign + s


def parse_amount(text: str) -> Decimal | None:
    """Парсит ставку: '100', '0.0001', '1e6', '1e21', '1 000 000'."""
    t = (text or "").strip().replace(" ", "").replace(",", ".")
    if not t:
        return None
    try:
        d = Decimal(t)
    except InvalidOperation:
        return None
    if not d.is_finite() or d <= 0 or d > Decimal("1e21"):
        return None
    return d


def is_admin(username: Optional[str]) -> bool:
    return bool(username) and username.lower().lstrip("@") in ADMIN_USERNAMES


async def ensure_user(msg: Message | CallbackQuery, ref_code: str | None = None) -> User:
    tg = msg.from_user
    assert tg is not None
    user, _ = await get_or_create_user(tg.id, tg.username or f"user{tg.id}", ref_code)
    return user


async def menu_text(user: User) -> str:
    async with SessionMaker() as s:
        rank_rows = (await s.execute(select(User.user_id).order_by(desc(User.balance)))).all()
        rank = next((i + 1 for i, r in enumerate(rank_rows) if r[0] == user.user_id), 0)
        from sqlalchemy import func
        ref_count = (await s.execute(
            select(func.count()).select_from(User).where(User.referrer_id == user.user_id)
        )).scalar() or 0
        start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        today_delta = (await s.execute(
            select(func.coalesce(func.sum(Transaction.amount), 0))
            .where(Transaction.user_id == user.user_id, Transaction.created_at >= start)
        )).scalar() or 0
    return (
        f"🎰 <b>КАЗИНО НА ФАНТИКИ</b>\n\n"
        f"👤 @{user.username}\n"
        f"💰 Баланс: <b>{fmt(user.balance)} 🍬</b>\n"
        f"⭐ Stars: <b>{user.stars}</b>\n"
        f"🎮 Игр: {fmt(user.games_played)} · 🏆 #{rank or '—'} · 👥 {ref_count}\n"
        f"📈 Сегодня: <b>{'+' if today_delta >= 0 else ''}{fmt(today_delta)} 🍬</b>\n\n"
        f"Выбери действие кнопками внизу ↓"
    )


def main_kb_for(user_or_uname):
    uname = user_or_uname.username if hasattr(user_or_uname, "username") else user_or_uname
    return kb.main_menu_kb(is_admin=is_admin(uname))


# ==================== /start ====================
@router.message(CommandStart(deep_link=True))
async def cmd_start_deep(message: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    payload = command.args or ""
    ref = payload if payload.startswith("ref_") else None
    user = await ensure_user(message, ref)
    if message.chat.type != "private":
        me = await message.bot.me()
        await message.answer(f"👋 Полный функционал в ЛС: https://t.me/{me.username}")
        return
    await message.answer(await menu_text(user), parse_mode="HTML", reply_markup=main_kb_for(user))


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user = await ensure_user(message)
    if message.chat.type != "private":
        me = await message.bot.me()
        await message.answer(
            f"👋 Я — 🎰 Казино на Фантики.\nВ группе: /top · /balance\nИграть: https://t.me/{me.username}"
        )
        return
    await message.answer(await menu_text(user), parse_mode="HTML", reply_markup=main_kb_for(user))


@router.message(Command("menu"))
@router.message(F.text == BTN_MENU)
async def go_menu(message: Message, state: FSMContext):
    await state.clear()
    if message.chat.type != "private":
        await message.answer("Меню доступно в ЛС.")
        return
    user = await ensure_user(message)
    await message.answer(await menu_text(user), parse_mode="HTML", reply_markup=main_kb_for(user))


@router.message(F.text == BTN_BACK)
async def go_back(message: Message, state: FSMContext):
    await go_menu(message, state)


@router.message(Command("balance"))
@router.message(F.text == BTN_BALANCE)
async def cmd_balance(message: Message):
    user = await ensure_user(message)
    text = f"💰 Баланс: <b>{fmt(user.balance)} 🍬</b>\n⭐ Stars: <b>{user.stars}</b>"
    if message.chat.type != "private":
        await message.answer(f"@{user.username}: 💰 {fmt(user.balance)} 🍬")
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=main_kb_for(user))


# ==================== ИГРЫ (hub) ====================
@router.message(Command("games"))
@router.message(F.text == BTN_GAMES)
async def go_games(message: Message, state: FSMContext):
    await state.clear()
    if message.chat.type != "private":
        await message.answer("🎲 Играть можно только в ЛС.")
        return
    await ensure_user(message)
    settings = await get_game_settings()
    enabled = {k: v.get("enabled", True) for k, v in settings.items()}
    await message.answer("🎲 Выбери игру кнопками внизу ↓", reply_markup=kb.games_kb(enabled))


async def _start_choose_bet(message: Message, state: FSMContext, game: str, title: str, extra: str = ""):
    cfg = await get_game_cfg(game)
    if not cfg.get("enabled", True):
        await message.answer("⛔ Игра временно отключена администратором.", reply_markup=kb.games_kb())
        return
    options = list(cfg.get("bet_options") or [100, 500, 1000])
    await state.set_state(PlayFSM.choose_bet)
    await state.update_data(game=game)
    await message.answer(
        f"{title}\n\n{extra}💰 Выбери ставку кнопкой или нажми <b>{BTN_MANUAL}</b> ↓".strip(),
        parse_mode="HTML",
        reply_markup=kb.bet_options_kb(options),
    )


@router.message(F.text == BTN_MINER)
async def game_miner(message: Message, state: FSMContext):
    if message.chat.type != "private":
        return
    user = await ensure_user(message)
    cfg = await get_game_cfg("miner")
    if not cfg.get("enabled", True):
        await message.answer("⛔ Майнер отключён.", reply_markup=kb.games_kb()); return
    # Возобновить активную партию?
    async with SessionMaker() as s:
        active = (await s.execute(
            select(MinerSession).where(
                MinerSession.user_id == user.user_id, MinerSession.status == "active",
            )
        )).scalar_one_or_none()
    if active:
        await _send_miner(message, state, active)
        await message.answer(
            "ℹ️ У тебя есть незавершённая партия. Продолжай — или "
            f"нажми <b>{BTN_MINER_RESET}</b>, чтобы начать заново "
            "(ставка вернётся, если не открыто ни одной клетки).",
            parse_mode="HTML", reply_markup=kb.miner_resume_kb(),
        )
        return
    growth = float(cfg.get("growth", 1.03))
    await state.set_state(MinerFSM.size)
    await message.answer(
        "⛏ <b>МАЙНЕР</b> («Сапёр наоборот»)\n\n"
        f"💎 Каждый алмаз умножает коэффициент на <b>×{growth}</b>:\n"
        f"1 алмаз → ×{growth ** 1:.4f} · 2 → ×{growth ** 2:.4f} · "
        f"3 → ×{growth ** 3:.4f} · 10 → ×{growth ** 10:.4f}\n"
        "💥 Мина — ставка сгорает. Первый ход НИКОГДА не мина.\n\n"
        f"📐 Шаг 1/3 — <b>размер поля</b> ({MINER_MIN_SIZE}–{MINER_MAX_SIZE}).\n"
        f"Выбери кнопкой или нажми <b>{BTN_MANUAL}</b> ↓",
        parse_mode="HTML", reply_markup=kb.miner_size_kb(),
    )


# ---------- Шаг 1: РАЗМЕР ПОЛЯ ----------
@router.message(F.text == BTN_MINER_RESET)
async def miner_reset(message: Message, state: FSMContext):
    """Отказ от начатой партии. Ставка возвращается, если не открыто ни одной клетки."""
    if message.chat.type != "private":
        return
    user = await ensure_user(message)
    async with SessionMaker() as s:
        sess = (await s.execute(
            select(MinerSession).where(
                MinerSession.user_id == user.user_id,
                MinerSession.status == "active",
            )
        )).scalar_one_or_none()
        if not sess:
            await state.clear()
            await message.answer("Нет активной партии.", reply_markup=kb.games_kb())
            return
        if sess.revealed:
            await message.answer(
                "❗ Ты уже открыл клетки — партию сбросить нельзя.\n"
                "Используй <b>💰 Забрать</b>, чтобы зафиксировать выигрыш.",
                parse_mode="HTML",
            )
            return
        # возвращаем ставку
        bet = D(sess.bet)
        u = (await s.execute(select(User).where(User.user_id == user.user_id))).scalar_one()
        u.balance = D(u.balance) + bet
        s.add(Transaction(user_id=u.user_id, type="miner_refund", amount=bet,
                          meta={"session": sess.id}))
        sess.status = "cancelled"
        sess.finished_at = datetime.utcnow()
        await s.commit()
    await state.clear()
    await message.answer(
        f"↩️ Партия сброшена. Ставка <b>{fmt(bet)} 🍬</b> возвращена.\n"
        f"Баланс: <b>{fmt(u.balance)} 🍬</b>\n\n"
        f"Нажми <b>⛏ Майнер</b>, чтобы начать заново.",
        parse_mode="HTML", reply_markup=kb.games_kb(),
    )


@router.message(MinerFSM.size, F.text == BTN_MANUAL)
async def miner_size_manual_btn(message: Message, state: FSMContext):
    await state.set_state(MinerFSM.size_manual)
    await message.answer(
        f"✏️ <b>Введи размер поля вручную</b>\n\n"
        f"Одно число от {MINER_MIN_SIZE} до {MINER_MAX_SIZE} (поле будет N×N).\n"
        f"Например: <code>7</code> → поле 7×7 (49 клеток)",
        parse_mode="HTML",
        reply_markup=kb.manual_input_kb("Например: 7"),
    )


@router.message(MinerFSM.size_manual)
async def miner_size_manual_input(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text in (BTN_MENU, BTN_BACK, BTN_GAMES):
        await go_games(message, state) if text == BTN_GAMES else await go_menu(message, state)
        return
    m = re.match(r"^(\d+)\s*[×xX*]\s*\d+$", text) or re.match(r"^(\d+)$", text)
    if not m:
        await message.answer(
            f"❗ Нужно ОДНО число от {MINER_MIN_SIZE} до {MINER_MAX_SIZE}. "
            f"Например: <code>7</code>",
            parse_mode="HTML", reply_markup=kb.manual_input_kb("Например: 7"),
        )
        return
    await _miner_goto_mines(message, state, int(m.group(1)))


@router.message(MinerFSM.size)
async def miner_size_preset(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text in (BTN_MENU, BTN_BACK, BTN_GAMES):
        await go_games(message, state) if text == BTN_GAMES else await go_menu(message, state)
        return
    m = re.match(r"^(\d+)\s*[×xX*]\s*\d+$", text) or re.match(r"^(\d+)$", text)
    if not m:
        await message.answer(
            f"Нажми кнопку-пресет или <b>{BTN_MANUAL}</b> ↓", parse_mode="HTML",
        )
        return
    await _miner_goto_mines(message, state, int(m.group(1)))


async def _miner_goto_mines(message: Message, state: FSMContext, size: int):
    if size < MINER_MIN_SIZE or size > MINER_MAX_SIZE:
        await message.answer(f"❗ Размер поля: {MINER_MIN_SIZE}–{MINER_MAX_SIZE}.")
        return
    total = size * size
    await state.set_state(MinerFSM.mines)
    await state.update_data(size=size)
    await message.answer(
        f"📐 Поле <b>{size}×{size}</b> ({total} клеток)\n\n"
        f"💣 Шаг 2/3 — <b>количество мин</b> (1–{total - 2}).\n"
        f"Выбери кнопкой или нажми <b>{BTN_MANUAL}</b> ↓",
        parse_mode="HTML", reply_markup=kb.miner_mines_kb(total),
    )


# ---------- Шаг 2: МИНЫ ----------
@router.message(MinerFSM.mines, F.text == BTN_MANUAL)
async def miner_mines_manual_btn(message: Message, state: FSMContext):
    data = await state.get_data()
    size = int(data["size"])
    await state.set_state(MinerFSM.mines_manual)
    await message.answer(
        f"✏️ <b>Введи количество мин вручную</b>\n\n"
        f"Целое число от 1 до {size * size - 2} (поле {size}×{size}).\n"
        f"Например: <code>{max(1, size * size // 6)}</code>",
        parse_mode="HTML",
        reply_markup=kb.manual_input_kb("Например: 6"),
    )


@router.message(MinerFSM.mines_manual)
async def miner_mines_manual_input(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text in (BTN_MENU, BTN_BACK, BTN_GAMES):
        await go_games(message, state) if text == BTN_GAMES else await go_menu(message, state)
        return
    if not text.lstrip("💣 ").isdigit():
        await message.answer(
            "❗ Нужно ОДНО целое число. Например: <code>6</code>",
            parse_mode="HTML", reply_markup=kb.manual_input_kb("Например: 6"),
        )
        return
    await _miner_goto_bet(message, state, int(text.lstrip("💣 ").strip()))


@router.message(MinerFSM.mines)
async def miner_mines_preset(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text in (BTN_MENU, BTN_BACK, BTN_GAMES):
        await go_games(message, state) if text == BTN_GAMES else await go_menu(message, state)
        return
    m = re.match(r"^💣\s*(\d+)$", text) or re.match(r"^(\d+)$", text)
    if not m:
        await message.answer(
            f"Нажми кнопку-пресет или <b>{BTN_MANUAL}</b> ↓", parse_mode="HTML",
        )
        return
    await _miner_goto_bet(message, state, int(m.group(1)))


async def _miner_goto_bet(message: Message, state: FSMContext, mines: int):
    data = await state.get_data()
    size = int(data["size"])
    total = size * size
    if mines < 1 or mines > total - 2:
        await message.answer(f"❗ Мины: от 1 до {total - 2}.")
        return
    user = await ensure_user(message)
    await state.set_state(MinerFSM.bet)
    await state.update_data(mines=mines)
    await message.answer(
        f"📐 {size}×{size} · 💣 {mines} мин ({mines / total * 100:.1f}%)\n\n"
        f"💰 Шаг 3/3 — <b>ставка</b>.\n"
        f"Твой баланс: <b>{fmt(user.balance)} 🍬</b>\n\n"
        f"Выбери кнопкой или нажми <b>{BTN_MANUAL}</b> ↓",
        parse_mode="HTML", reply_markup=kb.miner_bet_kb(),
    )


# ---------- Шаг 3: СТАВКА ----------
@router.message(MinerFSM.bet, F.text == BTN_MANUAL)
async def miner_bet_manual_btn(message: Message, state: FSMContext):
    user = await ensure_user(message)
    await state.set_state(MinerFSM.bet_manual)
    await message.answer(
        f"✏️ <b>Введи ставку вручную</b>\n\n"
        f"Любое положительное число с плавающей точкой до 1e21.\n"
        f"Примеры: <code>0.0001</code> · <code>0.5</code> · <code>250</code> · "
        f"<code>1e6</code> · <code>1000000000000000000000</code>\n\n"
        f"Баланс: <b>{fmt(user.balance)} 🍬</b>",
        parse_mode="HTML",
        reply_markup=kb.manual_input_kb("Например: 250"),
    )


@router.message(MinerFSM.bet_manual)
async def miner_bet_manual_input(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text in (BTN_MENU, BTN_BACK, BTN_GAMES):
        await go_games(message, state) if text == BTN_GAMES else await go_menu(message, state)
        return
    bet = parse_amount(text)
    if bet is None:
        await message.answer(
            "❗ ВВЕДИ ставку числом (положительное, до 1e21).\n"
            "Примеры: <code>0.01</code> · <code>500</code> · <code>1e9</code>",
            parse_mode="HTML", reply_markup=kb.manual_input_kb("Например: 250"),
        )
        return
    await _miner_start(message, state, bet)


@router.message(MinerFSM.bet)
async def miner_bet_preset(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text in (BTN_MENU, BTN_BACK, BTN_GAMES):
        await go_games(message, state) if text == BTN_GAMES else await go_menu(message, state)
        return
    bet = parse_amount(text)
    if bet is None:
        await message.answer(
            f"Нажми кнопку-пресет или <b>{BTN_MANUAL}</b> ↓", parse_mode="HTML",
        )
        return
    await _miner_start(message, state, bet)


async def _miner_start(message: Message, state: FSMContext, bet):
    data = await state.get_data()
    size, mines = int(data["size"]), int(data["mines"])
    user = await ensure_user(message)
    try:
        await debit_bet(user.user_id, bet, {"game": "miner", "size": size, "mines": mines})
    except NotEnoughFunds:
        await message.answer(
            f"❌ Недостаточно Фантиков. Баланс: {fmt(user.balance)} 🍬.\n"
            f"Введи ставку поменьше:",
            reply_markup=kb.manual_input_kb("Ставка поменьше"),
        )
        return
    board = generate_miner_board(size, mines)
    async with SessionMaker() as s:
        sess = MinerSession(user_id=user.user_id, bet=bet, board=board,
                            revealed=[], multiplier=10000)
        s.add(sess)
        await s.commit()
        await s.refresh(sess)
    await state.clear()
    await _send_miner(message, state, sess)


@router.message(F.text == BTN_LADDER)
async def game_ladder(message: Message, state: FSMContext):
    if message.chat.type != "private":
        return
    await ensure_user(message)
    cfg = await get_game_cfg("ladder")
    steps = cfg.get("steps") or []
    lines = "\n".join(f"{i}. ×{s['coef']} · {s['chance']}%" for i, s in enumerate(steps, 1))
    await _start_choose_bet(message, state, "ladder", f"⬆ <b>ЛЕСЕНКА</b>\n\n{lines}")


@router.message(F.text == BTN_BJ)
async def game_bj(message: Message, state: FSMContext):
    if message.chat.type != "private":
        return
    await ensure_user(message)
    cfg = await get_game_cfg("blackjack")
    await _start_choose_bet(
        message, state, "blackjack",
        f"♠ <b>БЛЭКДЖЕК</b>\n{cfg.get('decks', 6)} колод · дилер до {cfg.get('dealer_stand', 17)}",
    )


@router.message(F.text == BTN_HL)
async def game_hl(message: Message, state: FSMContext):
    if message.chat.type != "private":
        return
    await ensure_user(message)
    cfg = await get_game_cfg("highlow")
    await _start_choose_bet(
        message, state, "highlow",
        f"🔺 <b>БОЛЬШЕ / МЕНЬШЕ</b>\nЧисло 1..100 · 50 = проигрыш · ×{cfg.get('coef', 1.9)}",
    )


@router.message(F.text == BTN_DICE)
async def game_dice(message: Message, state: FSMContext):
    if message.chat.type != "private":
        return
    await ensure_user(message)
    cfg = await get_game_cfg("dice")
    await _start_choose_bet(
        message, state, "dice",
        f"🎲 <b>КОСТИ</b>\nУгадай 1..6 · ×{cfg.get('coef', 5.5)}",
    )


@router.message(F.text == BTN_SLOTS)
async def game_slots(message: Message, state: FSMContext):
    if message.chat.type != "private":
        return
    await ensure_user(message)
    await _start_choose_bet(message, state, "slots", "🎰 <b>СЛОТЫ</b>\n3 барабана · 7️⃣7️⃣7️⃣ ×500")


@router.message(F.text == BTN_ROULETTE)
async def game_roulette(message: Message, state: FSMContext):
    if message.chat.type != "private":
        return
    await ensure_user(message)
    await _start_choose_bet(message, state, "roulette", "🔴 <b>РУЛЕТКА</b>\nКрасное/Чёрное/Чёт/Нечёт ×2")


@router.message(PlayFSM.choose_bet, F.text == BTN_MANUAL)
async def play_bet_manual_btn(message: Message, state: FSMContext):
    user = await ensure_user(message)
    await state.set_state(PlayFSM.choose_bet_manual)
    await message.answer(
        f"✏️ <b>Введи ставку вручную</b>\n\n"
        f"Любое положительное число с плавающей точкой до 1e21.\n"
        f"Примеры: <code>0.0001</code> · <code>0.5</code> · <code>250</code> · "
        f"<code>1e6</code>\n\n"
        f"Баланс: <b>{fmt(user.balance)} 🍬</b>",
        parse_mode="HTML",
        reply_markup=kb.manual_input_kb("Например: 250"),
    )


@router.message(PlayFSM.choose_bet_manual)
async def play_bet_manual_input(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text in (BTN_MENU, BTN_BACK, BTN_GAMES):
        if text == BTN_GAMES:
            await go_games(message, state)
        else:
            await go_menu(message, state)
        return
    bet = parse_amount(text)
    if bet is None:
        await message.answer(
            "❗ ВВЕДИ ставку числом (положительное, до 1e21).\n"
            "Примеры: <code>0.01</code> · <code>500</code> · <code>1e9</code>",
            parse_mode="HTML", reply_markup=kb.manual_input_kb("Например: 250"),
        )
        return
    await on_choose_bet(message, state, bet_override=bet)


@router.message(PlayFSM.choose_bet)
async def on_choose_bet(message: Message, state: FSMContext, bet_override=None):
    text = (message.text or "").strip()
    if bet_override is None:
        if text in (BTN_MENU, BTN_BACK, BTN_GAMES):
            if text == BTN_GAMES:
                await go_games(message, state)
            else:
                await go_menu(message, state)
            return
        bet = parse_amount(text)
        if bet is None:
            await message.answer(
                f"Нажми кнопку-пресет или <b>{BTN_MANUAL}</b> ↓", parse_mode="HTML",
            )
            return
    else:
        bet = bet_override
    data = await state.get_data()
    game = data.get("game")
    user = await ensure_user(message)
    cfg = await get_game_cfg(game)

    if game == "ladder":
        await state.set_state(PlayFSM.ladder_step)
        await state.update_data(bet=bet)
        steps = cfg.get("steps") or []
        await message.answer(
            f"⬆ Ставка <b>{fmt(bet)} 🍬</b>. Выбери ступень ↓",
            parse_mode="HTML", reply_markup=kb.ladder_steps_kb(steps, bet),
        )
        return

    if game == "blackjack":
        try:
            await debit_bet(user.user_id, bet, {"game": "blackjack"})
        except NotEnoughFunds as e:
            await message.answer(f"❌ {e}"); return
        shoe = new_shoe(int(cfg.get("decks", 6)))
        player = [shoe.pop(), shoe.pop()]
        dealer = [shoe.pop(), shoe.pop()]
        await state.set_state(PlayFSM.bj_play)
        await state.update_data(bet=bet, player=player, dealer=dealer, shoe=shoe,
                                dealer_stand=int(cfg.get("dealer_stand", 17)))
        pv, dv = hand_value(player), hand_value(dealer)
        if pv == 21 or dv == 21:
            await _bj_finish(message, state)
            return
        await message.answer(
            f"♠ Ставка {fmt(bet)} 🍬\n\n"
            f"Дилер: {format_hand(dealer, hide_second=True)}\n"
            f"Игрок: {format_hand(player)} ({pv})",
            reply_markup=kb.bj_action_kb(),
        )
        return

    if game == "highlow":
        await state.set_state(PlayFSM.highlow_guess)
        await state.update_data(bet=bet)
        await message.answer(
            f"🔺 Ставка <b>{fmt(bet)} 🍬</b>. Больше или меньше 50?",
            parse_mode="HTML", reply_markup=kb.highlow_guess_kb(),
        )
        return

    if game == "dice":
        await state.set_state(PlayFSM.dice_pick)
        await state.update_data(bet=bet)
        await message.answer(
            f"🎲 Ставка <b>{fmt(bet)} 🍬</b>. Выбери число 1..6 ↓",
            parse_mode="HTML", reply_markup=kb.dice_pick_kb(),
        )
        return

    if game == "slots":
        try:
            await debit_bet(user.user_id, bet, {"game": "slots"})
        except NotEnoughFunds as e:
            await message.answer(f"❌ {e}"); return
        reels, coef = spin_slots(
            cfg.get("symbols", []), cfg.get("weights", []),
            cfg.get("payouts_3", {}), cfg.get("payouts_2", {}),
        )
        reel_str = "  ".join(reels)
        if coef > 0:
            payout = bet * D(str(coef))
            await credit_win(user.user_id, payout, {"game": "slots", "reels": reels})
            note = f"🎉 ×{coef} → <b>+{fmt(payout)} 🍬</b>"
        else:
            await register_loss(user.user_id, bet, {"game": "slots", "reels": reels})
            note = f"💔 Проигрыш {fmt(bet)} 🍬"
        await state.clear()
        await message.answer(
            f"🎰 [ {reel_str} ]\n\n{note}",
            parse_mode="HTML", reply_markup=kb.games_kb(),
        )
        return

    if game == "roulette":
        await state.set_state(PlayFSM.roulette_type)
        await state.update_data(bet=bet)
        await message.answer(
            f"🔴 Ставка <b>{fmt(bet)} 🍬</b>. Выбери тип ↓",
            parse_mode="HTML", reply_markup=kb.roulette_type_kb(),
        )
        return

    await state.clear()
    await message.answer("Неизвестная игра", reply_markup=kb.games_kb())


# ==================== МАЙНЕР: игровой цикл ====================
# Поля ≤ 8×8 → inline-кнопки (лимит Telegram: 8 кнопок в ряду).
# Поля > 8×8 → текстовая сетка + координаты «строка колонка».

INLINE_MAX_SIZE = 8


def _miner_mult_for(sess: MinerSession, cfg: dict, opened: int) -> float:
    return miner_multiplier(
        opened,
        growth=float(cfg.get("growth", 1.03)),
        cap=float(cfg.get("cap", 100.0)),
    )


def _miner_payout(sess: MinerSession, mult: float) -> Decimal:
    return (Decimal(str(sess.bet)) * Decimal(str(round(mult, 4)))).quantize(Decimal("0.0000000001"))


def _render_miner_grid(board: list[str], revealed: list[int], finished: bool, size: int) -> str:
    rs = set(revealed)
    lines = []
    for r in range(size):
        line = []
        for c in range(size):
            i = r * size + c
            if i in rs:
                line.append("💎")
            elif finished:
                line.append("💥" if board[i] == MINE else "▫️")
            else:
                line.append("🟦")
        lines.append("".join(line))
    return "\n".join(lines)


def _miner_status_text(sess: MinerSession, mult: float, note: str = "",
                       growth: float = 1.03, cap: float = 100.0) -> str:
    payout = _miner_payout(sess, mult)
    size = int(len(sess.board) ** 0.5)
    mines = sum(1 for c in sess.board if c == MINE)
    next_mult = min(mult * growth if sess.revealed else growth, cap)
    text = (
        f"⛏ <b>МАЙНЕР</b> {size}×{size} · 💣{mines} · ставка {fmt(sess.bet)} 🍬\n"
        f"💎 Открыто: <b>{len(sess.revealed)}</b>\n"
        f"📈 Коэффициент: <b>×{mult:.4f}</b> → следующий алмаз: <b>×{next_mult:.4f}</b>\n"
        f"💰 Можно забрать: <b>{fmt(payout)} 🍬</b>"
    )
    if note:
        text += f"\n\n{note}"
    return text


async def _send_miner(message: Message, state: FSMContext, sess: MinerSession, note: str = ""):
    size = int(len(sess.board) ** 0.5)
    cfg = await get_game_cfg("miner")
    growth = float(cfg.get("growth", 1.03))
    cap = float(cfg.get("cap", 100.0))
    mult = sess.multiplier / 10000
    finished = sess.status != "active"
    if size <= INLINE_MAX_SIZE:
        await message.answer(
            _miner_status_text(sess, mult, note, growth, cap), parse_mode="HTML",
            reply_markup=kb.miner_field_ikb(sess.id, size, list(sess.board),
                                            list(sess.revealed), finished),
        )
        if finished:
            await message.answer("Игра окончена ↓", reply_markup=kb.games_kb())
        return
    # Большое поле: текстовая сетка + координатный ввод
    grid = _render_miner_grid(list(sess.board), list(sess.revealed), finished, size)
    text = _miner_status_text(sess, mult, note, growth, cap) + "\n\n" + grid
    if not finished:
        text += "\n\nПришли координаты: <b>строка колонка</b> (например: <code>3 7</code>)"
        await state.set_state(MinerFSM.play)
        await state.update_data(sess_id=sess.id, size=size)
        await message.answer(text, parse_mode="HTML", reply_markup=kb.miner_play_kb())
    else:
        await message.answer(text, parse_mode="HTML", reply_markup=kb.games_kb())


async def _miner_do_reveal(user, sess_id: int, idx: int) -> dict:
    """Общая логика открытия клетки. Возвращает результат для рендера."""
    cfg = await get_game_cfg("miner")
    async with SessionMaker() as s:
        sess = (await s.execute(
            select(MinerSession).where(MinerSession.id == sess_id)
        )).scalar_one_or_none()
        if not sess or sess.user_id != user.user_id or sess.status != "active":
            return {"error": "Нет активной игры"}
        if idx < 0 or idx >= len(sess.board):
            return {"error": "Нет такой клетки"}
        if idx in sess.revealed:
            return {"error": "Уже открыто"}
        board = list(sess.board)
        if not sess.revealed:
            board = guarantee_first_safe(board, idx)
            sess.board = board
        revealed = list(sess.revealed) + [idx]
        size = int(len(board) ** 0.5)
        mines = sum(1 for c in board if c == MINE)

        if board[idx] == MINE:
            sess.revealed = revealed
            sess.status = "lost"
            sess.finished_at = datetime.utcnow()
            await s.commit()
            await register_loss(user.user_id, Decimal(str(sess.bet)), {"game": "miner"})
            return {"result": "lost", "sess": sess}

        mult = _miner_mult_for(sess, cfg, len(revealed))
        sess.revealed = revealed
        sess.multiplier = int(round(mult * 10000))
        safe = size * size - mines
        if len(revealed) >= safe:
            # все алмазы собраны — авто-выигрыш
            sess.status = "won"
            sess.finished_at = datetime.utcnow()
            await s.commit()
            payout = _miner_payout(sess, mult)
            await credit_win(user.user_id, payout, {"game": "miner", "auto": True})
            return {"result": "auto_won", "sess": sess, "mult": mult, "payout": payout}
        await s.commit()
        return {"result": "ok", "sess": sess, "mult": mult}


async def _miner_do_cashout(user, sess_id: int) -> dict:
    async with SessionMaker() as s:
        sess = (await s.execute(
            select(MinerSession).where(MinerSession.id == sess_id)
        )).scalar_one_or_none()
        if not sess or sess.user_id != user.user_id or sess.status != "active":
            return {"error": "Нет активной игры"}
        if not sess.revealed:
            return {"error": "Открой хотя бы одну клетку"}
        sess.status = "won"
        sess.finished_at = datetime.utcnow()
        await s.commit()
        mult = sess.multiplier / 10000
    payout = _miner_payout(sess, mult)
    await credit_win(user.user_id, payout, {"game": "miner", "cashout": True})
    return {"result": "cashout", "sess": sess, "mult": mult, "payout": payout}


# --- Inline-режим (поля ≤ 8×8) ---
@router.callback_query(F.data.startswith("mine:"))
async def cb_mine_reveal(cq: CallbackQuery):
    _, sid, idx = cq.data.split(":")
    user = await ensure_user(cq)
    r = await _miner_do_reveal(user, int(sid), int(idx))
    if "error" in r:
        await cq.answer(r["error"], show_alert=True)
        return
    sess = r["sess"]
    size = int(len(sess.board) ** 0.5)
    if r["result"] == "lost":
        await cq.message.edit_text(
            f"⛏ <b>МАЙНЕР</b>\n💥 <b>МИНА!</b> Ставка {fmt(sess.bet)} 🍬 сгорела.",
            parse_mode="HTML",
            reply_markup=kb.miner_field_ikb(sess.id, size, list(sess.board),
                                            list(sess.revealed), True),
        )
        await cq.message.answer("Выбери игру ↓", reply_markup=kb.games_kb())
    elif r["result"] == "auto_won":
        await cq.message.edit_text(
            f"⛏ <b>МАЙНЕР</b>\n🎉 Все 💎 собраны! ×{r['mult']:.4f} → <b>+{fmt(r['payout'])} 🍬</b>",
            parse_mode="HTML",
            reply_markup=kb.miner_field_ikb(sess.id, size, list(sess.board),
                                            list(sess.revealed), True),
        )
        await cq.message.answer("Выбери игру ↓", reply_markup=kb.games_kb())
    else:
        cfg = await get_game_cfg("miner")
        await cq.message.edit_text(
            _miner_status_text(sess, r["mult"], "",
                               float(cfg.get("growth", 1.03)), float(cfg.get("cap", 100.0))),
            parse_mode="HTML",
            reply_markup=kb.miner_field_ikb(sess.id, size, list(sess.board),
                                            list(sess.revealed), False),
        )
    await cq.answer()


@router.callback_query(F.data.startswith("minecash:"))
async def cb_mine_cash(cq: CallbackQuery):
    sid = int(cq.data.split(":")[1])
    user = await ensure_user(cq)
    r = await _miner_do_cashout(user, sid)
    if "error" in r:
        await cq.answer(r["error"], show_alert=True)
        return
    sess = r["sess"]
    size = int(len(sess.board) ** 0.5)
    await cq.message.edit_text(
        f"⛏ <b>МАЙНЕР</b>\n💰 Забрано: <b>+{fmt(r['payout'])} 🍬</b> (×{r['mult']:.4f})",
        parse_mode="HTML",
        reply_markup=kb.miner_field_ikb(sid, size, list(sess.board), list(sess.revealed), True),
    )
    await cq.message.answer("Выбери игру ↓", reply_markup=kb.games_kb())
    await cq.answer()


# --- Текстовый режим (поля > 8×8): координаты «строка колонка» ---
@router.message(MinerFSM.play)
async def miner_play_input(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    user = await ensure_user(message)
    data = await state.get_data()
    sess_id = int(data.get("sess_id", 0))
    size = int(data.get("size", 0))

    if text in (BTN_MENU, BTN_BACK, BTN_GAMES):
        # партия остаётся активной — можно вернуться через ⛏ Майнер
        await state.clear()
        if text == BTN_GAMES:
            await go_games(message, state)
        else:
            await go_menu(message, state)
        return

    if text == "💰 Забрать":
        r = await _miner_do_cashout(user, sess_id)
        if "error" in r:
            await message.answer(f"❌ {r['error']}")
            return
        await state.clear()
        sess = r["sess"]
        grid = _render_miner_grid(list(sess.board), list(sess.revealed), True,
                                  int(len(sess.board) ** 0.5))
        await message.answer(
            f"⛏ 💰 Забрано: <b>+{fmt(r['payout'])} 🍬</b> (×{r['mult']:.4f})\n\n{grid}",
            parse_mode="HTML", reply_markup=kb.games_kb(),
        )
        return

    m = re.match(r"^(\d+)[\s,;:]+(\d+)$", text)
    if not m:
        await message.answer(
            f"Пришли координаты: <b>строка колонка</b> (1–{size}), например <code>2 5</code>.\n"
            f"Или «💰 Забрать».",
            parse_mode="HTML",
        )
        return
    row, col = int(m.group(1)), int(m.group(2))
    if not (1 <= row <= size and 1 <= col <= size):
        await message.answer(f"Координаты от 1 до {size}.")
        return
    idx = (row - 1) * size + (col - 1)

    r = await _miner_do_reveal(user, sess_id, idx)
    if "error" in r:
        await message.answer(f"❌ {r['error']}")
        return
    sess = r["sess"]
    if r["result"] == "lost":
        await state.clear()
        grid = _render_miner_grid(list(sess.board), list(sess.revealed), True, size)
        await message.answer(
            f"💥 <b>МИНА</b> на {row}:{col}! Ставка {fmt(sess.bet)} 🍬 сгорела.\n\n{grid}",
            parse_mode="HTML", reply_markup=kb.games_kb(),
        )
    elif r["result"] == "auto_won":
        await state.clear()
        grid = _render_miner_grid(list(sess.board), list(sess.revealed), True, size)
        await message.answer(
            f"🎉 Все 💎 собраны! ×{r['mult']:.4f} → <b>+{fmt(r['payout'])} 🍬</b>\n\n{grid}",
            parse_mode="HTML", reply_markup=kb.games_kb(),
        )
    else:
        cfg = await get_game_cfg("miner")
        grid = _render_miner_grid(list(sess.board), list(sess.revealed), False, size)
        await message.answer(
            _miner_status_text(sess, r["mult"], "",
                               float(cfg.get("growth", 1.03)), float(cfg.get("cap", 100.0)))
            + "\n\n" + grid + "\n\nСледующая клетка: <b>строка колонка</b>",
            parse_mode="HTML", reply_markup=kb.miner_play_kb(),
        )


@router.callback_query(F.data == "noop")
async def cb_noop(cq: CallbackQuery):
    await cq.answer()


# ---- Лесенка ----
@router.message(PlayFSM.ladder_step)
async def on_ladder_step(message: Message, state: FSMContext):
    text = message.text or ""
    if text in (BTN_MENU, BTN_GAMES, BTN_BACK):
        await go_games(message, state) if text == BTN_GAMES else await go_menu(message, state)
        return
    m = re.match(r"^(\d+)\s*·", text)
    if not m:
        await message.answer("Выбери ступень кнопкой ↓"); return
    step = int(m.group(1))
    data = await state.get_data()
    bet = D(data["bet"])
    user = await ensure_user(message)
    cfg = await get_game_cfg("ladder")
    steps = cfg.get("steps") or []
    if step < 1 or step > len(steps):
        await message.answer("Неверная ступень"); return
    try:
        await debit_bet(user.user_id, bet, {"game": "ladder"})
    except NotEnoughFunds as e:
        await message.answer(f"❌ {e}"); await state.clear(); return
    won, failed_at, results = play_ladder(step, steps)
    ticks = "\n".join(
        f"{'✅' if ok else '❌'} Ступень {i} ×{steps[i-1]['coef']}" for i, ok in results
    )
    if won:
        coef = float(steps[step - 1]["coef"])
        payout = bet * D(str(coef))
        await credit_win(user.user_id, payout, {"game": "ladder", "step": step})
        note = f"\n🎉 <b>+{fmt(payout)} 🍬</b> (×{coef})"
    else:
        await register_loss(user.user_id, bet, {"game": "ladder", "failed_at": failed_at})
        note = f"\n💔 Провал на ступени {failed_at}. −{fmt(bet)} 🍬"
    await state.clear()
    await message.answer(
        f"⬆ <b>ЛЕСЕНКА</b> · ставка {fmt(bet)} 🍬 · цель {step}\n\n{ticks}{note}",
        parse_mode="HTML", reply_markup=kb.games_kb(),
    )


# ---- High/Low ----
@router.message(PlayFSM.highlow_guess)
async def on_hl_guess(message: Message, state: FSMContext):
    text = message.text or ""
    if text in (BTN_MENU, BTN_GAMES, BTN_BACK):
        await go_games(message, state) if text == BTN_GAMES else await go_menu(message, state)
        return
    if "Меньше" in text:
        guess = "low"
    elif "Больше" in text:
        guess = "high"
    else:
        await message.answer("Выбери кнопку ↓"); return
    data = await state.get_data()
    bet = D(data["bet"])
    user = await ensure_user(message)
    cfg = await get_game_cfg("highlow")
    try:
        await debit_bet(user.user_id, bet, {"game": "highlow"})
    except NotEnoughFunds as e:
        await message.answer(f"❌ {e}"); await state.clear(); return
    won, roll = play_highlow(guess)
    if won:
        payout = bet * D(str(cfg.get("coef", 1.9)))
        await credit_win(user.user_id, payout, {"game": "highlow", "roll": roll})
        note = f"🎉 Выпало <b>{roll}</b> · +{fmt(payout)} 🍬"
    else:
        await register_loss(user.user_id, bet, {"game": "highlow", "roll": roll})
        note = f"💔 Выпало <b>{roll}</b> · −{fmt(bet)} 🍬"
    await state.clear()
    await message.answer(f"🔺 {note}", parse_mode="HTML", reply_markup=kb.games_kb())


# ---- Blackjack ----
@router.message(PlayFSM.bj_play, F.text == "➕ Ещё")
async def bj_hit(message: Message, state: FSMContext):
    data = await state.get_data()
    player = [tuple(c) for c in data["player"]]
    shoe = [tuple(c) for c in data["shoe"]]
    player.append(shoe.pop())
    await state.update_data(player=player, shoe=shoe)
    if hand_value(player) >= 21:
        await _bj_finish(message, state)
        return
    pv = hand_value(player)
    await message.answer(
        f"♠ Ставка {fmt(data['bet'])} 🍬\n\n"
        f"Дилер: {format_hand(data['dealer'], hide_second=True)}\n"
        f"Игрок: {format_hand(player)} ({pv})",
        reply_markup=kb.bj_action_kb(),
    )


@router.message(PlayFSM.bj_play, F.text == "✋ Стоп")
async def bj_stand(message: Message, state: FSMContext):
    await _bj_finish(message, state)


@router.message(PlayFSM.bj_play)
async def bj_other(message: Message, state: FSMContext):
    if (message.text or "") in (BTN_MENU, BTN_BACK):
        await message.answer("Сначала закончи раздачу: ➕ Ещё или ✋ Стоп")
        return
    await message.answer("Нажми ➕ Ещё или ✋ Стоп")


async def _bj_finish(message: Message, state: FSMContext):
    data = await state.get_data()
    bet = D(data["bet"])
    player = [tuple(c) for c in data["player"]]
    dealer = [tuple(c) for c in data["dealer"]]
    shoe = [tuple(c) for c in data["shoe"]]
    stand = int(data.get("dealer_stand", 17))
    user = await ensure_user(message)
    while hand_value(dealer) < stand:
        dealer.append(shoe.pop())
    pv, dv = hand_value(player), hand_value(dealer)
    if pv > 21:
        outcome = "loss"
    elif dv > 21 or pv > dv:
        outcome = "win"
    elif pv == dv:
        outcome = "push"
    else:
        outcome = "loss"
    cfg_bj = await get_game_cfg("blackjack")
    if outcome == "win":
        payout = bet * D(str(cfg_bj.get("win_mult", 2.0)))
        await credit_win(user.user_id, payout, {"game": "blackjack"})
        note = f"🎉 Победа! +{fmt(payout)} 🍬"
    elif outcome == "push":
        await credit_win(user.user_id, bet, {"game": "blackjack", "push": True})
        note = f"🤝 Пуш. Возврат {fmt(bet)} 🍬"
    else:
        await register_loss(user.user_id, bet, {"game": "blackjack"})
        note = f"💔 Проигрыш {fmt(bet)} 🍬"
    await state.clear()
    await message.answer(
        f"♠ <b>БЛЭКДЖЕК</b> · {fmt(bet)} 🍬\n\n"
        f"Дилер: {format_hand(dealer)} ({dv})\n"
        f"Игрок: {format_hand(player)} ({pv})\n\n{note}",
        parse_mode="HTML", reply_markup=kb.games_kb(),
    )


# ---- Кости ----
@router.message(PlayFSM.dice_pick)
async def on_dice_pick(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text in (BTN_MENU, BTN_GAMES, BTN_BACK):
        await go_games(message, state) if text == BTN_GAMES else await go_menu(message, state)
        return
    if text not in {"1", "2", "3", "4", "5", "6"}:
        await message.answer("Выбери число 1..6"); return
    target = int(text)
    data = await state.get_data()
    bet = D(data["bet"])
    user = await ensure_user(message)
    cfg = await get_game_cfg("dice")
    try:
        await debit_bet(user.user_id, bet, {"game": "dice"})
    except NotEnoughFunds as e:
        await message.answer(f"❌ {e}"); await state.clear(); return
    roll, won, _ = play_dice(target)
    emoji = {1: "⚀", 2: "⚁", 3: "⚂", 4: "⚃", 5: "⚄", 6: "⚅"}
    coef = float(cfg.get("coef", 5.5))
    if won:
        payout = bet * D(str(coef))
        await credit_win(user.user_id, payout, {"game": "dice", "roll": roll})
        note = f"🎉 {emoji[roll]} → +{fmt(payout)} 🍬"
    else:
        await register_loss(user.user_id, bet, {"game": "dice", "roll": roll})
        note = f"💔 Выпало {emoji[roll]} · −{fmt(bet)} 🍬"
    await state.clear()
    await message.answer(f"🎲 Ставка на {target}\n{note}", reply_markup=kb.games_kb())


# ---- Рулетка ----
@router.message(PlayFSM.roulette_type)
async def on_roulette_type(message: Message, state: FSMContext):
    text = message.text or ""
    if text in (BTN_MENU, BTN_GAMES, BTN_BACK):
        await go_games(message, state) if text == BTN_GAMES else await go_menu(message, state)
        return
    mapping = {
        "🔴 Красное": "red", "⚫ Чёрное": "black",
        "Чёт": "even", "Нечёт": "odd",
        "1-18": "low", "19-36": "high",
    }
    bet_type = mapping.get(text)
    if not bet_type:
        await message.answer("Выбери тип кнопкой ↓"); return
    data = await state.get_data()
    bet = D(data["bet"])
    user = await ensure_user(message)
    cfg_r = await get_game_cfg("roulette")
    try:
        await debit_bet(user.user_id, bet, {"game": "roulette"})
    except NotEnoughFunds as e:
        await message.answer(f"❌ {e}"); await state.clear(); return
    result = play_roulette(bet_type)
    color = roulette_color(result["roll"])
    if result["won"]:
        # настраиваемый коэффициент равных шансов
        coef = float(cfg_r.get("coef", 2.0)) if result["coef"] == 2.0 else result["coef"]
        payout = bet * D(str(coef))
        await credit_win(user.user_id, payout, {"game": "roulette", "roll": result["roll"]})
        note = f"🎉 {color} {result['roll']} → +{fmt(payout)} 🍬"
    else:
        await register_loss(user.user_id, bet, {"game": "roulette", "roll": result["roll"]})
        note = f"💔 {color} {result['roll']} · −{fmt(bet)} 🍬"
    await state.clear()
    await message.answer(f"🔴 {note}", reply_markup=kb.games_kb())


@router.message(F.text == BTN_MANUAL)
async def manual_out_of_context(message: Message, state: FSMContext):
    """«✏️ Ввести вручную» вне шага настройки — не молчим, а подсказываем.

    Зарегистрирован ПОСЛЕ всех FSM-обработчиков, поэтому срабатывает только
    когда состояние не активно.
    """
    if message.chat.type != "private":
        return
    await message.answer(
        "✏️ Сначала выбери игру и дойди до шага ввода — там появится эта кнопка.\n\n"
        "🎲 Нажми <b>Игры</b>, затем выбери игру ↓",
        parse_mode="HTML", reply_markup=kb.games_kb(),
    )


# ==================== ТОП ====================
@router.message(Command("top"))
@router.message(F.text == BTN_TOP)
async def go_top(message: Message, state: FSMContext):
    await state.clear()
    await ensure_user(message)
    await _send_top(message, "balance")


@router.message(F.text.in_({"🏆 Баланс", "💰 Выигрыши", "📈 Сегодня", "📊 Неделя", "👥 По рефералам"}))
async def top_tabs(message: Message):
    mapping = {
        "🏆 Баланс": "balance", "💰 Выигрыши": "wins",
        "📈 Сегодня": "today", "📊 Неделя": "week",
        "👥 По рефералам": "referrals",
    }
    await _send_top(message, mapping[message.text])


async def _send_top(message: Message, tab: str):
    user = await ensure_user(message)
    from sqlalchemy import func
    async with SessionMaker() as s:
        unit = "🍬"
        rows: list[tuple[str, int, int]] = []
        if tab == "balance":
            data = (await s.execute(
                select(User.username, User.balance, User.user_id).order_by(desc(User.balance)).limit(10)
            )).all()
            rows = [(a, int(b), c) for a, b, c in data]
        elif tab == "wins":
            data = (await s.execute(
                select(User.username, User.total_wins, User.user_id).order_by(desc(User.total_wins)).limit(10)
            )).all()
            rows = [(a, int(b), c) for a, b, c in data]
        elif tab == "referrals":
            unit = "👥"
            q = (await s.execute(
                select(User.username, User.user_id, func.count(Referral.id).label("cnt"))
                .join(Referral, Referral.referrer_id == User.user_id, isouter=True)
                .group_by(User.user_id, User.username)
                .order_by(desc("cnt")).limit(10)
            )).all()
            rows = [(a, int(c or 0), b) for a, b, c in q]
        else:
            since = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            if tab == "week":
                since -= timedelta(days=datetime.utcnow().weekday())
            q = (await s.execute(
                select(User.username, User.user_id,
                       func.coalesce(func.sum(Transaction.amount), 0).label("v"))
                .join(Transaction,
                      (Transaction.user_id == User.user_id)
                      & (Transaction.type == "win")
                      & (Transaction.created_at >= since), isouter=True)
                .group_by(User.user_id, User.username)
                .order_by(desc("v")).limit(10)
            )).all()
            rows = [(a, int(c or 0), b) for a, b, c in q]

    medals = ["🥇", "🥈", "🥉"] + [f"{i}." for i in range(4, 11)]
    lines = [f"{medals[i]} @{u} — <b>{fmt(v)} {unit}</b>" for i, (u, v, _) in enumerate(rows)] or ["Пока пусто"]
    my_rank = next((i + 1 for i, (_, _, uid) in enumerate(rows) if uid == user.user_id), None)
    titles = {
        "balance": "БАЛАНС", "wins": "ВЫИГРЫШИ", "today": "СЕГОДНЯ",
        "week": "НЕДЕЛЯ", "referrals": "РЕФЕРАЛЫ",
    }
    text = (
        f"🏆 <b>ТОП-10 · {titles[tab]}</b>\n\n"
        + "\n".join(lines)
        + f"\n\nВаше место: <b>#{my_rank or '—'}</b>"
        + "\n\n💡 При достаточной активности добавим призы в виде ⭐ ЗВЁЗД!"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=kb.top_tabs_kb())


# ==================== БОНУС ====================
@router.message(F.text == BTN_BONUS)
async def go_bonus(message: Message, state: FSMContext):
    await state.clear()
    if message.chat.type != "private":
        return
    user = await ensure_user(message)
    buffs = active_buffs(user.active_buffs)
    amt = bonus_amount(buffs)
    remain = 0
    if user.last_bonus_at:
        elapsed = (datetime.utcnow() - user.last_bonus_at).total_seconds()
        remain = max(0, BONUS_COOLDOWN_SEC - int(elapsed))
    if remain:
        await message.answer(
            f"🎁 Бонус {fmt(amt)} 🍬\n⏳ Через {remain // 60}:{remain % 60:02d}",
            reply_markup=main_kb_for(user),
        )
        return
    async with SessionMaker() as s:
        u = (await s.execute(select(User).where(User.user_id == user.user_id))).scalar_one()
        u.balance = Decimal(str(u.balance)) + amt
        u.last_bonus_at = datetime.utcnow()
        s.add(Transaction(user_id=u.user_id, type="bonus", amount=amt))
        await s.commit()
        bal = Decimal(str(u.balance))
    await message.answer(
        f"🎁 <b>+{fmt(amt)} 🍬</b>\nБаланс: {fmt(bal)} 🍬",
        parse_mode="HTML", reply_markup=main_kb_for(user),
    )


# ==================== КЭШБЕК ====================
@router.message(Command("cashback"))
@router.message(F.text == BTN_CASHBACK)
async def go_cashback(message: Message, state: FSMContext):
    await state.clear()
    if message.chat.type != "private":
        return
    user = await ensure_user(message)
    info = await compute_today_cashback(user.user_id)
    text = (
        f"🔄 <b>КЭШБЕК</b>\n\n"
        f"💰 Проигрышей сегодня: <b>{fmt(info['gross'])} 🍬</b>\n"
        f"🔄 Процент: <b>{int(info['percent']*100)}%</b>\n"
        f"📊 Доступно: <b>{fmt(info['available'])} 🍬</b>\n\n"
        f"Лимит {fmt(CASHBACK_MAX_DAILY)}/день · мин. {CASHBACK_MIN} 🍬"
    )
    await message.answer(
        text, parse_mode="HTML",
        reply_markup=kb.cashback_kb(info["available"] >= CASHBACK_MIN),
    )


@router.message(F.text == "💰 Забрать кэшбек")
async def claim_cb(message: Message, state: FSMContext):
    user = await ensure_user(message)
    try:
        amt = await claim_cashback(user.user_id)
    except ValueError as e:
        await message.answer(f"❌ {e}", reply_markup=main_kb_for(user)); return
    await message.answer(f"✅ +{fmt(amt)} 🍬 кэшбек", reply_markup=main_kb_for(user))


# ==================== РЕФЕРАЛЫ ====================
@router.message(Command("referral"))
@router.message(F.text == BTN_REFERRAL)
async def go_referral(message: Message, state: FSMContext):
    await state.clear()
    if message.chat.type != "private":
        return
    user = await ensure_user(message)
    me = await message.bot.me()
    link = f"https://t.me/{me.username}?start={user.referral_code}"
    from sqlalchemy import func
    async with SessionMaker() as s:
        direct = (await s.execute(
            select(func.count()).select_from(User).where(User.referrer_id == user.user_id)
        )).scalar() or 0
        chain = (await s.execute(
            select(func.count()).select_from(Referral).where(Referral.referrer_id == user.user_id)
        )).scalar() or 0
        earned = (await s.execute(
            select(func.coalesce(func.sum(Transaction.amount), 0))
            .where(Transaction.user_id == user.user_id, Transaction.type == "ref_bonus")
        )).scalar() or 0
    await message.answer(
        f"👥 <b>РЕФЕРАЛЫ</b>\n\n"
        f"Ссылка:\n<code>{link}</code>\n\n"
        f"Прямые: <b>{direct}</b> · В цепочке: <b>{chain}</b>\n"
        f"Заработано: <b>{fmt(earned)} 🍬</b>\n\n"
        f"+20 000 за регистрацию · +10 000 за 1-ю игру · +5 000 при 10 000 🍬\n"
        f"<b>5%</b> со всех проигрышей всей цепочки!",
        parse_mode="HTML", reply_markup=main_kb_for(user),
    )


# ==================== ПРОМО ====================
@router.message(Command("promo"))
async def cmd_promo(message: Message, command: CommandObject, state: FSMContext):
    if message.chat.type != "private":
        await message.answer("Промокоды — только в ЛС."); return
    await ensure_user(message)
    args = (command.args or "").strip()
    if args:
        await _activate_promo(message, args)
        return
    await state.set_state(PromoFSM.waiting_code)
    await message.answer("🎫 Пришли промокод одним сообщением:", reply_markup=kb.only_menu_kb())


@router.message(F.text == BTN_PROMO)
async def btn_promo(message: Message, state: FSMContext):
    if message.chat.type != "private":
        await message.answer("Промокоды — только в ЛС."); return
    await ensure_user(message)
    await state.set_state(PromoFSM.waiting_code)
    await message.answer("🎫 Пришли промокод одним сообщением:", reply_markup=kb.only_menu_kb())


@router.message(PromoFSM.waiting_code)
async def promo_code(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if text in (BTN_MENU, BTN_BACK):
        await go_menu(message, state); return
    await state.clear()
    await _activate_promo(message, text)


async def _activate_promo(message: Message, code: str):
    user = await ensure_user(message)
    code = code.upper()
    async with SessionMaker() as s:
        p = (await s.execute(select(PromoCode).where(PromoCode.code == code))).scalar_one_or_none()
        if not p:
            await message.answer("❌ Промокод не найден.", reply_markup=main_kb_for(user)); return
        if not p.active:
            await message.answer("❌ Неактивен.", reply_markup=main_kb_for(user)); return
        if p.expires_at and p.expires_at < datetime.utcnow():
            await message.answer("❌ Истёк.", reply_markup=main_kb_for(user)); return
        if p.used_count >= p.max_uses:
            await message.answer("❌ Лимит исчерпан.", reply_markup=main_kb_for(user)); return
        used = (await s.execute(
            select(PromoUse).where(PromoUse.promo_id == p.id, PromoUse.user_id == user.user_id)
        )).scalar_one_or_none()
        if used:
            await message.answer("❌ Уже активирован.", reply_markup=main_kb_for(user)); return
        s.add(PromoUse(promo_id=p.id, user_id=user.user_id))
        p.used_count = int(p.used_count) + 1
        u = (await s.execute(select(User).where(User.user_id == user.user_id))).scalar_one()
        u.balance = Decimal(str(u.balance)) + Decimal(str(p.amount))
        s.add(Transaction(user_id=u.user_id, type="promo", amount=int(p.amount), meta={"code": p.code}))
        await s.commit()
        bal = Decimal(str(u.balance))
    await message.answer(
        f"🎉 <b>ПРОМОКОД АКТИВИРОВАН!</b>\n"
        f"🎫 <code>{code}</code>\n💰 +{fmt(p.amount)} 🍬\nБаланс: {fmt(bal)} 🍬",
        parse_mode="HTML", reply_markup=main_kb_for(user),
    )


# ==================== МАГАЗИН (Stars) ====================
@router.message(Command("shop"))
@router.message(F.text == BTN_SHOP)
async def go_shop(message: Message, state: FSMContext):
    await state.clear()
    if message.chat.type != "private":
        return
    user = await ensure_user(message)
    lines = [f"🛍 <b>МАГАЗИН БУСТОВ</b>\n⭐ Stars: <b>{user.stars}</b>\n"]
    for b in BUFFS.values():
        lines.append(f"• <b>{b['name']}</b> — {b['price']}⭐\n  <i>{b['desc']}</i>")
    lines.append("\nВыбери буст кнопкой внизу — бот выставит счёт на ⭐ Stars.")
    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=kb.shop_kb())


@router.message(F.text.regexp(r"^⭐ .+ — \d+⭐$"))
async def buy_buff(message: Message):
    if message.chat.type != "private":
        return
    text = message.text or ""
    # "⭐ x2 Кэшбек — 15⭐"
    m = re.match(r"^⭐ (.+) — (\d+)⭐$", text)
    if not m:
        return
    name, price_s = m.group(1), m.group(2)
    key = next((k for k, b in BUFFS.items() if b["name"] == name), None)
    if not key:
        await message.answer("Неизвестный буст"); return
    b = BUFFS[key]
    await message.bot.send_invoice(
        chat_id=message.chat.id,
        title=f"🛍 {b['name']}",
        description=b["desc"],
        payload=f"buff_{key}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=b["name"], amount=int(b["price"]))],
        start_parameter="shop",
    )


@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    await q.answer(ok=True)


@router.message(F.successful_payment)
async def payment_ok(message: Message):
    sp = message.successful_payment
    if not sp.invoice_payload.startswith("buff_"):
        return
    key = sp.invoice_payload.replace("buff_", "", 1)
    if key not in BUFFS:
        return
    b = BUFFS[key]
    user = await ensure_user(message)
    now = datetime.utcnow()
    expires = (now + timedelta(hours=b["hours"])).isoformat()
    async with SessionMaker() as s:
        u = (await s.execute(select(User).where(User.user_id == user.user_id))).scalar_one()
        buffs = dict(u.active_buffs or {})
        if key == "insurance":
            prev = buffs.get(key, {})
            charges = int(prev.get("charges", 0)) + 1
            buffs[key] = {"expires": expires, "charges": charges}
        else:
            buffs[key] = {"expires": expires}
        u.active_buffs = buffs
        s.add(Purchase(
            user_id=u.user_id, buff_type=key, stars_cost=b["price"],
            duration_hours=b["hours"], expires_at=now + timedelta(hours=b["hours"]),
        ))
        s.add(Transaction(user_id=u.user_id, type="purchase", amount=0,
                          meta={"buff": key, "stars": b["price"]}))
        await s.commit()
    await message.answer(f"✅ Буст активирован: <b>{b['name']}</b>", parse_mode="HTML",
                          reply_markup=main_kb_for(user))


# ==================== СТАТИСТИКА ====================
@router.message(Command("stats"))
@router.message(F.text == BTN_STATS)
async def go_stats(message: Message, state: FSMContext):
    await state.clear()
    if message.chat.type != "private":
        return
    user = await ensure_user(message)
    async with SessionMaker() as s:
        txs = (await s.execute(
            select(Transaction).where(Transaction.user_id == user.user_id)
            .order_by(desc(Transaction.created_at)).limit(10)
        )).scalars().all()
    lines = [
        "📊 <b>СТАТИСТИКА</b>\n",
        f"💰 {fmt(user.balance)} 🍬 · ⭐ {user.stars}",
        f"🎮 {user.games_played} игр · 🏆 {fmt(user.total_wins)} · 💔 {fmt(user.total_losses)}",
        "\n<b>Последние 10:</b>",
    ]
    for t in txs:
        sign = "+" if t.amount > 0 else ""
        lines.append(f"{t.created_at.strftime('%d.%m %H:%M')} · {t.type} · <b>{sign}{fmt(t.amount)}</b>")
    if not txs:
        lines.append("нет транзакций")
    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=main_kb_for(user))


# ==================== АДМИН ====================
@router.message(Command("secretadmin"))
@router.message(F.text == BTN_ADMIN)
async def go_admin(message: Message, state: FSMContext):
    if not is_admin(message.from_user.username):
        return  # молчим
    await state.clear()
    await ensure_user(message)
    await message.answer(await _admin_text(), parse_mode="HTML", reply_markup=kb.admin_kb())


async def _admin_text() -> str:
    from sqlalchemy import func
    async with SessionMaker() as s:
        total = (await s.execute(select(func.count()).select_from(User))).scalar() or 0
        start = datetime.utcnow() - timedelta(hours=24)
        active24 = (await s.execute(
            select(func.count(func.distinct(Transaction.user_id)))
            .where(Transaction.created_at >= start)
        )).scalar() or 0
        games = (await s.execute(select(func.coalesce(func.sum(User.games_played), 0)))).scalar() or 0
        bal = (await s.execute(select(func.coalesce(func.sum(User.balance), 0)))).scalar() or 0
    return (
        f"🔐 <b>АДМИН-ПАНЕЛЬ</b>\n\n"
        f"👥 Пользователей: <b>{total}</b>\n"
        f"📈 Активных 24ч: <b>{active24}</b>\n"
        f"🎮 Игр: <b>{fmt(games)}</b>\n"
        f"💰 Фантиков: <b>{fmt(bal)} 🍬</b>\n\n"
        f"Выбери действие кнопками внизу ↓"
    )


@router.message(F.text == "📊 Админ-статистика")
async def adm_stats(message: Message):
    if not is_admin(message.from_user.username):
        return
    await message.answer(await _admin_text(), parse_mode="HTML", reply_markup=kb.admin_kb())


# ---- Промо ----
@router.message(F.text == "🎫 Создать промокод")
async def adm_promo_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.username):
        return
    await state.set_state(AdminFSM.promo_amount)
    await message.answer("Сумма Фантиков (число):", reply_markup=kb.only_menu_kb())


@router.message(AdminFSM.promo_amount)
async def adm_promo_amount(message: Message, state: FSMContext):
    if not is_admin(message.from_user.username):
        await state.clear(); return
    if (message.text or "") == BTN_MENU:
        await go_menu(message, state); return
    try:
        amt = int((message.text or "").replace(" ", "")); assert amt > 0
    except Exception:
        await message.answer("Введи положительное число:"); return
    await state.update_data(amount=amt)
    await state.set_state(AdminFSM.promo_uses)
    await message.answer("Количество активаций:")


@router.message(AdminFSM.promo_uses)
async def adm_promo_uses(message: Message, state: FSMContext):
    if not is_admin(message.from_user.username):
        await state.clear(); return
    try:
        n = int((message.text or "").replace(" ", "")); assert n > 0
    except Exception:
        await message.answer("Введи положительное число:"); return
    await state.update_data(uses=n)
    await state.set_state(AdminFSM.promo_desc)
    await message.answer("Описание (или «-»):")


@router.message(AdminFSM.promo_desc)
async def adm_promo_desc(message: Message, state: FSMContext):
    if not is_admin(message.from_user.username):
        await state.clear(); return
    data = await state.get_data()
    await state.clear()
    desc = None if (message.text or "").strip() == "-" else (message.text or "").strip()
    code = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(10))
    async with SessionMaker() as s:
        s.add(PromoCode(code=code, amount=data["amount"], max_uses=data["uses"], description=desc))
        await s.commit()
    await message.answer(
        f"✅ Промокод: <code>{code}</code>\n"
        f"Сумма: {fmt(data['amount'])} 🍬 · Активаций: {data['uses']}"
        + (f"\n{desc}" if desc else ""),
        parse_mode="HTML", reply_markup=kb.admin_kb(),
    )


# ---- Выдать Фантики ----
@router.message(F.text == "💰 Выдать Фантики")
async def adm_give_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.username):
        return
    await state.set_state(AdminFSM.give_username)
    await message.answer("Юзернейм получателя (без @):", reply_markup=kb.only_menu_kb())


@router.message(AdminFSM.give_username)
async def adm_give_uname(message: Message, state: FSMContext):
    if (message.text or "") == BTN_MENU:
        await go_menu(message, state); return
    await state.update_data(uname=(message.text or "").strip().lstrip("@"))
    await state.set_state(AdminFSM.give_amount)
    await message.answer("Сумма 🍬 (можно отрицательную):")


@router.message(AdminFSM.give_amount)
async def adm_give_amount(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    try:
        amount = int((message.text or "").replace(" ", ""))
    except Exception:
        await message.answer("Не число.", reply_markup=kb.admin_kb()); return
    target = await get_user_by_username(data["uname"])
    if not target:
        await message.answer(f"❌ @{data['uname']} не найден", reply_markup=kb.admin_kb()); return
    async with SessionMaker() as s:
        u = (await s.execute(select(User).where(User.user_id == target.user_id))).scalar_one()
        u.balance = Decimal(str(u.balance)) + amount
        s.add(Transaction(user_id=u.user_id, type="admin_give", amount=amount,
                          meta={"by": message.from_user.username}))
        await s.commit()
    await message.answer(
        f"✅ @{data['uname']}: {'+' if amount >= 0 else ''}{fmt(amount)} 🍬",
        reply_markup=kb.admin_kb(),
    )


# ---- Выдать Stars ----
@router.message(F.text == "⭐ Выдать Stars")
async def adm_stars_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.username):
        return
    await state.set_state(AdminFSM.stars_username)
    await message.answer("Юзернейм получателя (без @):", reply_markup=kb.only_menu_kb())


@router.message(AdminFSM.stars_username)
async def adm_stars_uname(message: Message, state: FSMContext):
    if (message.text or "") == BTN_MENU:
        await go_menu(message, state); return
    await state.update_data(uname=(message.text or "").strip().lstrip("@"))
    await state.set_state(AdminFSM.stars_amount)
    await message.answer("Кол-во ⭐ (целое):")


@router.message(AdminFSM.stars_amount)
async def adm_stars_amount(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    try:
        n = int((message.text or "").replace(" ", ""))
    except Exception:
        await message.answer("Не число.", reply_markup=kb.admin_kb()); return
    target = await get_user_by_username(data["uname"])
    if not target:
        await message.answer(f"❌ @{data['uname']} не найден", reply_markup=kb.admin_kb()); return
    async with SessionMaker() as s:
        u = (await s.execute(select(User).where(User.user_id == target.user_id))).scalar_one()
        u.stars = max(0, int(u.stars) + n)
        await s.commit()
    await message.answer(f"✅ @{data['uname']}: {'+' if n >= 0 else ''}{n} ⭐", reply_markup=kb.admin_kb())


@router.message(Command("give"))
async def cmd_give(message: Message, command: CommandObject):
    if not is_admin(message.from_user.username):
        return
    args = (command.args or "").split()
    if len(args) != 2:
        await message.answer("Использование: /give username amount"); return
    uname, amount_s = args
    try:
        amount = int(amount_s)
    except ValueError:
        await message.answer("Некорректная сумма"); return
    target = await get_user_by_username(uname)
    if not target:
        await message.answer("Не найден"); return
    async with SessionMaker() as s:
        u = (await s.execute(select(User).where(User.user_id == target.user_id))).scalar_one()
        u.balance = Decimal(str(u.balance)) + amount
        s.add(Transaction(user_id=u.user_id, type="admin_give", amount=amount,
                          meta={"by": message.from_user.username}))
        await s.commit()
    await message.answer(f"✅ @{uname}: {'+' if amount >= 0 else ''}{fmt(amount)} 🍬")


# ==================== НАСТРОЙКИ МИНИ-ИГР ====================
GAME_LABELS = {
    "miner": "⛏ Майнер",
    "ladder": "⬆ Лесенка",
    "blackjack": "♠ Блэкджек",
    "highlow": "🔺 Больше/Меньше",
    "dice": "🎲 Кости",
    "slots": "🎰 Слоты",
    "roulette": "🔴 Рулетка",
}
LABEL_TO_GAME = {v: k for k, v in GAME_LABELS.items()}


@router.message(F.text == "⚙️ Настройки игр")
async def adm_games(message: Message, state: FSMContext):
    if not is_admin(message.from_user.username):
        return
    await state.clear()
    settings = await get_game_settings()
    lines = ["⚙️ <b>НАСТРОЙКИ МИНИ-ИГР</b>\n"]
    for key, label in GAME_LABELS.items():
        conf = settings.get(key, {})
        mark = "✅" if conf.get("enabled", True) else "❌"
        extra = ""
        if key == "miner":
            extra = f" · growth ×{conf.get('growth', 1.03)}/алмаз · cap ×{conf.get('cap', 100)}"
        elif key in ("highlow", "dice"):
            extra = f" · ×{conf.get('coef', '?')}"
        elif key == "roulette":
            extra = f" · ×{conf.get('coef', 2.0)}"
        elif key == "blackjack":
            extra = f" · выплата ×{conf.get('win_mult', 2.0)}"
        elif key == "ladder":
            extra = f" · {len(conf.get('steps') or [])} ступеней"
        bets = conf.get("bet_options") or []
        bets_s = ", ".join(fmt(b) for b in bets[:5])
        lines.append(f"{mark} <b>{label}</b>{extra}\n   ставки: {bets_s or '—'}")
    lines.append("\nВыбери игру кнопкой ↓")
    await message.answer("\n".join(lines), parse_mode="HTML", reply_markup=kb.admin_games_kb(settings))


@router.message(F.text.regexp(r"^[✅❌] .+$"))
async def adm_pick_game(message: Message, state: FSMContext):
    if not is_admin(message.from_user.username):
        return
    text = message.text or ""
    # "✅ ⛏ Майнер" / "❌ ⛏ Майнер"
    label = text[2:].strip()  # drop mark + space
    game = LABEL_TO_GAME.get(label)
    if not game:
        return
    await state.set_state(AdminFSM.cfg_game)
    await state.update_data(game=game)
    conf = await get_game_cfg(game)
    if game == "miner":
        # Размер/мины/ставку ВВОДИТ игрок. Админ задаёт рост коэффициента.
        g = float(conf.get("growth", 1.03))
        text_out = (
            f"⛏ <b>НАСТРОЙКИ МАЙНЕРА</b>\n\n"
            f"Размер поля, мины и ставку игрок ВВОДИТ сам в игре.\n\n"
            f"Рост коэффициента: <b>каждый алмаз × growth</b>\n"
            f"• growth: <b>×{g}</b> за алмаз\n"
            f"   (1 алмаз ×{g**1:.4f} · 2 ×{g**2:.4f} · 5 ×{g**5:.4f} · 10 ×{g**10:.4f})\n"
            f"• cap (потолок): <b>×{conf.get('cap', 100)}</b>\n"
            f"Статус: {'✅ вкл' if conf.get('enabled', True) else '❌ выкл'}\n\n"
            f"Изменить — пришли:\n"
            f"<code>growth 1.05</code> · <code>cap 100</code>"
        )
        await message.answer(text_out, parse_mode="HTML",
                              reply_markup=kb.admin_toggle_kb(label))
        return

    text_out = (
        f"<b>{label}</b>\n\n"
        f"Статус: {'✅ вкл' if conf.get('enabled', True) else '❌ выкл'}\n"
        f"Ставки: {', '.join(fmt(b) for b in conf.get('bet_options', []))}\n"
    )
    if game in ("highlow", "dice"):
        text_out += (f"Коэффициент: ×{conf.get('coef')}\n"
                     f"Изменить — пришли число: <code>1.9</code>\n")
    if game == "roulette":
        text_out += (f"Коэф. равных шансов: ×{conf.get('coef', 2.0)}\n"
                     f"Изменить — пришли число: <code>2.0</code>\n")
    if game == "blackjack":
        text_out += (f"Выплата при победе: ×{conf.get('win_mult', 2.0)}\n"
                     f"Изменить — пришли число: <code>2.0</code>\n")
    if game == "slots":
        p3 = conf.get("payouts_3", {})
        text_out += ("Выплаты (3 в ряд): " +
                     ", ".join(f"{s}×{v}" for s, v in p3.items()) + "\n" +
                     'Изменить — пришли JSON: <code>{"🍒":3,"🍋":5,"💎":100,"7️⃣":500}</code>\n')
    if game == "ladder":
        steps = conf.get("steps") or []
        text_out += f"Ступеней: {len(steps)}\n"
        text_out += "\n".join(f"  {i}. ×{s['coef']} · {s['chance']}%" for i, s in enumerate(steps, 1))
        text_out += "\n\nЧтобы изменить ступени — пришли JSON-массив вида:\n"
        text_out += '<code>[{"coef":1.1,"chance":92}, ...]</code>\n'
    text_out += "\nКнопки: вкл/выкл, ставки."
    await message.answer(text_out, parse_mode="HTML", reply_markup=kb.admin_toggle_kb(label))


@router.message(AdminFSM.cfg_game, F.text == "✅ Включить")
async def adm_enable(message: Message, state: FSMContext):
    if not is_admin(message.from_user.username):
        return
    data = await state.get_data()
    game = data.get("game")
    if not game:
        return
    await update_game_cfg(game, {"enabled": True})
    await message.answer(f"✅ {GAME_LABELS.get(game, game)} включена", reply_markup=kb.admin_kb())
    await state.clear()


@router.message(AdminFSM.cfg_game, F.text == "❌ Выключить")
async def adm_disable(message: Message, state: FSMContext):
    if not is_admin(message.from_user.username):
        return
    data = await state.get_data()
    game = data.get("game")
    if not game:
        return
    await update_game_cfg(game, {"enabled": False})
    await message.answer(f"❌ {GAME_LABELS.get(game, game)} выключена", reply_markup=kb.admin_kb())
    await state.clear()


@router.message(AdminFSM.cfg_game, F.text == "✏️ Ставки")
async def adm_bets_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.username):
        return
    data = await state.get_data()
    game = data.get("game")
    if not game:
        return
    if game == "miner":
        await message.answer(
            "У Майнера свободная ставка — игрок вводит любое число сам (до 1e21).",
        )
        return
    conf = await get_game_cfg(game)
    await state.set_state(AdminFSM.cfg_bets)
    await state.update_data(game=game)
    await message.answer(
        f"Текущие ставки: {', '.join(fmt(b) for b in conf.get('bet_options', []))}\n\n"
        f"Пришли новый список через запятую, например:\n"
        f"<code>50, 100, 500, 1000, 5000</code>",
        parse_mode="HTML", reply_markup=kb.only_menu_kb(),
    )


@router.message(AdminFSM.cfg_bets)
async def adm_bets_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.username):
        await state.clear(); return
    if (message.text or "") == BTN_MENU:
        await go_menu(message, state); return
    data = await state.get_data()
    game = data.get("game")
    try:
        parts = re.split(r"[,;\s]+", (message.text or "").strip())
        bets = sorted({int(p) for p in parts if p and int(p) > 0})
        if not bets:
            raise ValueError("empty")
    except Exception:
        await message.answer("Не удалось разобрать. Пример: 100, 500, 1000"); return
    await update_game_cfg(game, {"bet_options": bets})
    await state.clear()
    await message.answer(
        f"✅ Ставки {GAME_LABELS.get(game, game)}: {', '.join(fmt(b) for b in bets)}",
        reply_markup=kb.admin_kb(),
    )


@router.message(AdminFSM.cfg_game)
async def adm_cfg_free_text(message: Message, state: FSMContext):
    """Свободный ввод: JSON ступеней лесенки / коэф. highlow|dice / кривая майнера (base/target/cap)."""
    if not is_admin(message.from_user.username):
        return
    text = (message.text or "").strip()
    if text in (BTN_MENU, BTN_ADMIN, "⚙️ Настройки игр"):
        if text == "⚙️ Настройки игр":
            await adm_games(message, state)
        elif text == BTN_ADMIN:
            await go_admin(message, state)
        else:
            await go_menu(message, state)
        return
    data = await state.get_data()
    game = data.get("game")
    if not game:
        return

    # JSON steps for ladder
    if game == "ladder" and text.startswith("["):
        import json
        try:
            steps = json.loads(text)
            assert isinstance(steps, list) and steps
            for s in steps:
                assert "coef" in s and "chance" in s
        except Exception:
            await message.answer("Неверный JSON. Пример: [{\"coef\":1.1,\"chance\":92}]"); return
        await update_game_cfg("ladder", {"steps": steps, "max_step": len(steps)})
        await state.clear()
        await message.answer(f"✅ Лесенка: {len(steps)} ступеней", reply_markup=kb.admin_kb())
        return

    # Настраиваемый коэффициент: highlow / dice / roulette / blackjack
    if game in ("highlow", "dice", "roulette", "blackjack"):
        try:
            coef = float(text.replace(",", ".").lstrip("×xX"))
            assert coef > 1
        except Exception:
            await message.answer("Пришли коэффициент числом, например 1.9"); return
        key = "win_mult" if game == "blackjack" else "coef"
        await update_game_cfg(game, {key: coef})
        await state.clear()
        await message.answer(f"✅ {GAME_LABELS[game]}: ×{coef}", reply_markup=kb.admin_kb())
        return

    # JSON выплат слотов
    if game == "slots" and text.startswith("{"):
        import json
        try:
            payouts = json.loads(text)
            assert isinstance(payouts, dict) and payouts
            for k, v in payouts.items():
                assert isinstance(k, str) and float(v) > 0
        except Exception:
            await message.answer('Неверный JSON. Пример: {"🍒":3,"💎":100}'); return
        conf = await get_game_cfg("slots")
        merged = dict(conf.get("payouts_3", {}))
        merged.update({k: float(v) for k, v in payouts.items()})
        await update_game_cfg("slots", {"payouts_3": merged})
        await state.clear()
        await message.answer(
            "✅ Слоты: " + ", ".join(f"{s}×{v}" for s, v in merged.items()),
            reply_markup=kb.admin_kb(),
        )
        return

    # Рост Майнера: "growth 1.05" / "cap 100"
    if game == "miner":
        m = re.match(r"^(growth|cap)\s+([\d.,]+)$", text, re.IGNORECASE)
        if not m:
            await message.answer(
                "Формат: <code>growth 1.05</code> (множитель за алмаз) · "
                "<code>cap 100</code> (потолок)",
                parse_mode="HTML",
            )
            return
        key, val_s = m.group(1).lower(), m.group(2).replace(",", ".")
        try:
            val = float(val_s)
        except ValueError:
            await message.answer("Не число."); return
        if key == "growth":
            if not (1.001 <= val <= 1.5):
                await message.answer("growth: 1.001–1.5 (рекомендуется 1.01–1.05)"); return
            await update_game_cfg("miner", {"growth": val})
        else:  # cap
            if not (1.1 <= val <= 100000):
                await message.answer("cap: 1.1–100000"); return
            await update_game_cfg("miner", {"cap": val})
        conf = await get_game_cfg("miner")
        g = float(conf.get("growth", 1.03))
        await message.answer(
            f"✅ Майнер: growth ×<b>{g}</b>/алмаз · cap ×<b>{conf.get('cap')}</b>\n"
            f"(1 алмаз ×{g**1:.4f} · 5 ×{g**5:.4f} · 10 ×{g**10:.4f} · 20 ×{g**20:.4f})",
            parse_mode="HTML", reply_markup=kb.admin_kb(),
        )
        await state.clear()
        return

    await message.answer("Не понял. Используй кнопки.")


# ==================== ГРУППЫ ====================
@router.my_chat_member()
async def on_added(event: ChatMemberUpdated):
    if event.chat.type in ("group", "supergroup"):
        if event.new_chat_member.status in ("member", "administrator"):
            me = await event.bot.me()
            await event.bot.send_message(
                event.chat.id,
                f"👋 Я — <b>🎰 Казино на Фантики</b>.\n"
                f"В группе: /top · /balance\n"
                f"Играть в ЛС: https://t.me/{me.username}",
                parse_mode="HTML",
            )
