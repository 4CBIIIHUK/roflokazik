"""Reply-клавиатуры (кнопки внизу чата) + Inline для игровых полей."""
from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove,
)

from config import (
    BTN_ADMIN, BTN_BACK, BTN_BALANCE, BTN_BJ, BTN_BONUS, BTN_CASHBACK,
    BTN_DICE, BTN_GAMES, BTN_HL, BTN_LADDER, BTN_MANUAL, BTN_MENU, BTN_MINER,
    BTN_MINER_RESET, BTN_PROMO, BTN_REFERRAL, BTN_ROULETTE, BTN_SHOP,
    BTN_SLOTS, BTN_STATS, BTN_TOP, BUFFS,
)


def main_menu_kb(is_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=BTN_GAMES), KeyboardButton(text=BTN_TOP)],
        [KeyboardButton(text=BTN_BONUS), KeyboardButton(text=BTN_CASHBACK)],
        [KeyboardButton(text=BTN_REFERRAL), KeyboardButton(text=BTN_SHOP)],
        [KeyboardButton(text=BTN_PROMO), KeyboardButton(text=BTN_STATS)],
        [KeyboardButton(text=BTN_BALANCE)],
    ]
    if is_admin:
        rows.append([KeyboardButton(text=BTN_ADMIN)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, is_persistent=True)


def games_kb(enabled: dict | None = None) -> ReplyKeyboardMarkup:
    """enabled: {game_key: bool}."""
    en = enabled or {}
    games = [
        (BTN_MINER, "miner"), (BTN_LADDER, "ladder"),
        (BTN_BJ, "blackjack"), (BTN_HL, "highlow"),
        (BTN_DICE, "dice"), (BTN_SLOTS, "slots"),
        (BTN_ROULETTE, "roulette"),
    ]
    rows: list[list[KeyboardButton]] = []
    row: list[KeyboardButton] = []
    for label, key in games:
        if en.get(key, True):
            row.append(KeyboardButton(text=label))
            if len(row) == 2:
                rows.append(row)
                row = []
    if row:
        rows.append(row)
    rows.append([KeyboardButton(text=BTN_MENU)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def back_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_MENU), KeyboardButton(text=BTN_BACK)]],
        resize_keyboard=True,
    )


def only_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_MENU)]],
        resize_keyboard=True,
    )


def bet_options_kb(options: list[int], back_to: str = BTN_GAMES) -> ReplyKeyboardMarkup:
    """Пресеты ставки для обычных игр + кнопка ручного ввода."""
    rows: list[list[KeyboardButton]] = []
    row: list[KeyboardButton] = []
    for v in options:
        row.append(KeyboardButton(text=f"🍬 {v:,}".replace(",", " ")))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([KeyboardButton(text=BTN_MANUAL)])
    rows.append([KeyboardButton(text=back_to), KeyboardButton(text=BTN_MENU)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def parse_bet_button(text: str) -> int | None:
    """Разбирает '🍬 1 000' → 1000."""
    t = text.strip()
    if not t.startswith("🍬"):
        return None
    digits = "".join(ch for ch in t if ch.isdigit())
    return int(digits) if digits else None


def ladder_steps_kb(steps: list[dict], bet: int) -> ReplyKeyboardMarkup:
    rows: list[list[KeyboardButton]] = []
    row: list[KeyboardButton] = []
    for i, st in enumerate(steps, start=1):
        row.append(KeyboardButton(text=f"{i} · ×{st['coef']}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([KeyboardButton(text=BTN_GAMES), KeyboardButton(text=BTN_MENU)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def highlow_guess_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔻 Меньше 50"), KeyboardButton(text="🔺 Больше 50")],
            [KeyboardButton(text=BTN_GAMES), KeyboardButton(text=BTN_MENU)],
        ],
        resize_keyboard=True,
    )


def bj_action_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Ещё"), KeyboardButton(text="✋ Стоп")],
            [KeyboardButton(text=BTN_MENU)],
        ],
        resize_keyboard=True,
    )


def dice_pick_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=str(i)) for i in range(1, 4)],
            [KeyboardButton(text=str(i)) for i in range(4, 7)],
            [KeyboardButton(text=BTN_GAMES), KeyboardButton(text=BTN_MENU)],
        ],
        resize_keyboard=True,
    )


def roulette_type_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔴 Красное"), KeyboardButton(text="⚫ Чёрное")],
            [KeyboardButton(text="Чёт"), KeyboardButton(text="Нечёт")],
            [KeyboardButton(text="1-18"), KeyboardButton(text="19-36")],
            [KeyboardButton(text=BTN_GAMES), KeyboardButton(text=BTN_MENU)],
        ],
        resize_keyboard=True,
    )


def top_tabs_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🏆 Баланс"), KeyboardButton(text="💰 Выигрыши")],
            [KeyboardButton(text="📈 Сегодня"), KeyboardButton(text="📊 Неделя")],
            [KeyboardButton(text="👥 По рефералам")],
            [KeyboardButton(text=BTN_MENU)],
        ],
        resize_keyboard=True,
    )


def cashback_kb(can_claim: bool) -> ReplyKeyboardMarkup:
    rows = []
    if can_claim:
        rows.append([KeyboardButton(text="💰 Забрать кэшбек")])
    rows.append([KeyboardButton(text=BTN_MENU)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def shop_kb() -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text=f"⭐ {b['name']} — {b['price']}⭐")] for b in BUFFS.values()]
    rows.append([KeyboardButton(text=BTN_MENU)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def admin_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎫 Создать промокод")],
            [KeyboardButton(text="💰 Выдать Фантики"), KeyboardButton(text="⭐ Выдать Stars")],
            [KeyboardButton(text="⚙️ Настройки игр")],
            [KeyboardButton(text="📊 Админ-статистика")],
            [KeyboardButton(text=BTN_MENU)],
        ],
        resize_keyboard=True,
    )


def admin_games_kb(settings: dict) -> ReplyKeyboardMarkup:
    labels = {
        "miner": "⛏ Майнер",
        "ladder": "⬆ Лесенка",
        "blackjack": "♠ Блэкджек",
        "highlow": "🔺 Больше/Меньше",
        "dice": "🎲 Кости",
        "slots": "🎰 Слоты",
        "roulette": "🔴 Рулетка",
    }
    rows: list[list[KeyboardButton]] = []
    row: list[KeyboardButton] = []
    for key, label in labels.items():
        conf = settings.get(key, {})
        mark = "✅" if conf.get("enabled", True) else "❌"
        row.append(KeyboardButton(text=f"{mark} {label}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([KeyboardButton(text=BTN_ADMIN), KeyboardButton(text=BTN_MENU)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def manual_input_kb(placeholder: str = "Введи число…") -> ReplyKeyboardMarkup:
    """Клавиатура для ручного ввода: поле ввода + кнопки выхода."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_GAMES), KeyboardButton(text=BTN_MENU)]],
        resize_keyboard=True,
        input_field_placeholder=placeholder,
    )


def miner_size_kb() -> ReplyKeyboardMarkup:
    """Пресеты размера поля + кнопка ручного ввода."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="3×3"), KeyboardButton(text="5×5"),
             KeyboardButton(text="8×8")],
            [KeyboardButton(text="10×10"), KeyboardButton(text="15×15"),
             KeyboardButton(text="30×30")],
            [KeyboardButton(text=BTN_MANUAL)],
            [KeyboardButton(text=BTN_GAMES), KeyboardButton(text=BTN_MENU)],
        ],
        resize_keyboard=True,
    )


def miner_mines_kb(total: int) -> ReplyKeyboardMarkup:
    """Пресеты мин + кнопка ручного ввода."""
    opts = sorted({m for m in [1, 2, 3, 5, total // 8, total // 4, total // 2, total - 2]
                   if 1 <= m <= total - 2})
    rows: list[list[KeyboardButton]] = []
    row: list[KeyboardButton] = []
    for m in opts:
        row.append(KeyboardButton(text=f"💣 {m}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([KeyboardButton(text=BTN_MANUAL)])
    rows.append([KeyboardButton(text=BTN_GAMES), KeyboardButton(text=BTN_MENU)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def miner_bet_kb(balance=None) -> ReplyKeyboardMarkup:
    """Пресеты ставки + кнопка ручного ввода."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="10"), KeyboardButton(text="100"),
             KeyboardButton(text="1000")],
            [KeyboardButton(text="0.5"), KeyboardButton(text="1e6"),
             KeyboardButton(text="1e9")],
            [KeyboardButton(text=BTN_MANUAL)],
            [KeyboardButton(text=BTN_GAMES), KeyboardButton(text=BTN_MENU)],
        ],
        resize_keyboard=True,
    )


def miner_play_kb() -> ReplyKeyboardMarkup:
    """Для больших полей (текстовая сетка): кэшаут + выходы."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💰 Забрать")],
            [KeyboardButton(text=BTN_MINER_RESET)],
            [KeyboardButton(text=BTN_GAMES), KeyboardButton(text=BTN_MENU)],
        ],
        resize_keyboard=True,
    )


def miner_resume_kb() -> ReplyKeyboardMarkup:
    """Показана, когда у игрока уже есть незавершённая партия."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_MINER_RESET)],
            [KeyboardButton(text=BTN_GAMES), KeyboardButton(text=BTN_MENU)],
        ],
        resize_keyboard=True,
    )


def admin_toggle_kb(game_label: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Включить"), KeyboardButton(text="❌ Выключить")],
            [KeyboardButton(text="✏️ Ставки")],
            [KeyboardButton(text="⚙️ Настройки игр"), KeyboardButton(text=BTN_MENU)],
        ],
        resize_keyboard=True,
    )


# ---- Inline-поле Майнера (для полей ≤ 8×8; лимит Telegram — 8 кнопок в ряду) ----
def miner_field_ikb(session_id: int, size: int, board: list[str],
                    revealed: list[int], finished: bool) -> InlineKeyboardMarkup:
    revealed_set = set(revealed)
    rows = []
    for r in range(size):
        row = []
        for c in range(size):
            idx = r * size + c
            if idx in revealed_set:
                row.append(InlineKeyboardButton(text="💎", callback_data="noop"))
            elif finished:
                cell = board[idx]
                row.append(InlineKeyboardButton(
                    text="💥" if cell == "mine" else "▫️", callback_data="noop",
                ))
            else:
                row.append(InlineKeyboardButton(text="🟦", callback_data=f"mine:{session_id}:{idx}"))
        rows.append(row)
    if not finished:
        rows.append([InlineKeyboardButton(text="💰 Забрать", callback_data=f"minecash:{session_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def remove_kb() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()
