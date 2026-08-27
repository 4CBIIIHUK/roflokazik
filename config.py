"""Конфигурация Telegram-бота «Казино на Фантики».

Все секреты — из окружения (Railway Variables).
"""
from __future__ import annotations

import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
# /version покажет это значение. Можно переопределить BOT_VERSION в Railway.
BOT_VERSION: str = os.getenv(
    "BOT_VERSION",
    "miner-v7-postgres-fsm-2026-08-27",
)
RAILWAY_COMMIT: str = os.getenv("RAILWAY_GIT_COMMIT_SHA", "local")[:8]
DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/app_db",
)
# Railway даёт postgresql:// — asyncpg хочет postgresql+asyncpg://
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)

ADMIN_USERNAMES = {
    u.strip().lower().lstrip("@")
    for u in os.getenv("ADMIN_USERNAMES", "f0nt1ew,slash_zzzz").split(",")
    if u.strip()
}

# ---- Экономика ----
START_BALANCE = 1_000
START_STARS = 0

REF_BONUS_REGISTER = 20_000
REF_BONUS_FIRST_GAME = 10_000
REF_BONUS_REACH_10K = 5_000
REF_CHAIN_LOSS_PERCENT = 0.05

CASHBACK_BASE = 0.10
CASHBACK_MAX_DAILY = 50_000
CASHBACK_MIN = 100

BONUS_COOLDOWN_SEC = 5 * 60
BONUS_AMOUNT_BASE = 1_000

# ---- Магазин бустов (Telegram Stars) ----
BUFFS = {
    "x2_cashback": {
        "name": "x2 Кэшбек",
        "price": 15,
        "hours": 24,
        "desc": "Кэшбек 20% вместо 10% на 24 часа",
    },
    "x15_coef": {
        "name": "x1.5 Коэффициенты",
        "price": 35,
        "hours": 24,
        "desc": "Все выигрыши ×1.5 на 24 часа",
    },
    "x3_cashback": {
        "name": "x3 Кэшбек",
        "price": 40,
        "hours": 24,
        "desc": "Кэшбек 30% на 24 часа",
    },
    "insurance": {
        "name": "Страховка",
        "price": 50,
        "hours": 24 * 365,
        "desc": "При проигрыше возврат 50% ставки (1 раз)",
    },
    "x2_bonus": {
        "name": "x2 Бонус",
        "price": 20,
        "hours": 12,
        "desc": "Бонус 2 000 🍬 каждые 5 минут (12 ч)",
    },
}

# ---- Дефолтные настройки мини-игр (переопределяются из БД) ----
DEFAULT_GAME_SETTINGS = {
    "miner": {
        # «Сапёр наоборот». Размер поля, мины и ставку ВВОДИТ игрок.
        # Рост коэффициента детерминированный: mult(n) = growth^n.
        "growth": 1.03,     # множитель за КАЖДЫЙ открытый алмаз (1.01..1.5)
        "cap": 100.0,       # абсолютный потолок коэффициента
        "min_size": 3,      # мин. размер поля
        "max_size": 30,     # макс. размер поля
        "max_bet": 1e21,    # ставка — любое число с плавающей точкой до 1e21
        "enabled": True,
    },
    "ladder": {
        "bet_options": [100, 500, 1000, 5000, 10000],
        "max_step": 12,
        "enabled": True,
        # steps: list of {coef, chance}
        "steps": [
            {"coef": 1.1, "chance": 92},
            {"coef": 1.3, "chance": 89},
            {"coef": 1.6, "chance": 85},
            {"coef": 2.0, "chance": 80},
            {"coef": 2.5, "chance": 74},
            {"coef": 3.5, "chance": 66},
            {"coef": 5.0, "chance": 55},
            {"coef": 8.0, "chance": 42},
            {"coef": 14.0, "chance": 28},
            {"coef": 25.0, "chance": 16},
            {"coef": 50.0, "chance": 8},
            {"coef": 101.0, "chance": 3},
        ],
    },
    "blackjack": {
        "min_bet": 50,
        "max_bet": 100_000,
        "bet_options": [50, 100, 500, 1000, 5000, 20000],
        "decks": 6,
        "dealer_stand": 17,
        "win_mult": 2.0,    # выплата при победе (настраивается админом)
        "enabled": True,
    },
    "highlow": {
        "coef": 1.9,
        "min_bet": 10,
        "max_bet": 50_000,
        "bet_options": [50, 100, 500, 1000, 5000],
        "enabled": True,
    },
    "dice": {
        "coef": 5.5,
        "min_bet": 10,
        "max_bet": 20_000,
        "bet_options": [50, 100, 500, 1000],
        "enabled": True,
    },
    "slots": {
        "min_bet": 50,
        "max_bet": 10_000,
        "bet_options": [50, 100, 500, 1000],
        "symbols": ["🍒", "🍋", "🍇", "🔔", "⭐", "💎", "7️⃣"],
        "weights": [30, 25, 20, 15, 8, 4, 2],
        "payouts_3": {"🍒": 3, "🍋": 5, "🍇": 8, "🔔": 15, "⭐": 30, "💎": 100, "7️⃣": 500},
        "payouts_2": {"🍒": 1.5, "🍋": 2, "🍇": 3},
        "enabled": True,
    },
    "roulette": {
        "min_bet": 10,
        "max_bet": 30_000,
        "bet_options": [50, 100, 500, 1000, 5000],
        "coef": 2.0,        # выплата равных шансов (красное/чёрное/чёт/нечёт/1-18/19-36)
        "enabled": True,
    },
}

ROULETTE_RED = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
ROULETTE_BLACK = {2, 4, 6, 8, 10, 11, 13, 15, 17, 20, 22, 24, 26, 28, 29, 31, 33, 35}

# ---- Тексты кнопок Reply-клавиатуры (главное меню) ----
BTN_GAMES = "🎲 Игры"
BTN_TOP = "🏆 Топ"
BTN_BONUS = "🎁 Бонус"
BTN_CASHBACK = "🔄 Кэшбек"
BTN_REFERRAL = "👥 Рефералы"
BTN_SHOP = "🛍 Магазин"
BTN_PROMO = "🎫 Промокод"
BTN_STATS = "📊 Статистика"
BTN_BALANCE = "💰 Баланс"
BTN_BACK = "🔙 Назад"
BTN_MENU = "🏠 Меню"
BTN_ADMIN = "🔐 Админ"

# Кнопки игр
BTN_MINER = "⛏ Майнер"
BTN_LADDER = "⬆ Лесенка"
BTN_BJ = "♠ Блэкджек"
BTN_HL = "🔺 Больше/Меньше"
BTN_DICE = "🎲 Кости"
BTN_SLOTS = "🎰 Слоты"
BTN_ROULETTE = "🔴 Рулетка"

# Кнопка ручного ввода — есть на КАЖДОМ шаге настройки (ставка, мины, размер…)
BTN_MANUAL = "✏️ Ввести вручную"
# Отказ от начатой партии Майнера (ставка возвращается, если не открыто ни одной клетки)
BTN_MINER_RESET = "🚫 Сбросить партию"
