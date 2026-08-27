"""Точка входа Telegram-бота «Казино на Фантики».

Railway start command: python -u bot.py
"""
from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from config import BOT_TOKEN, BOT_VERSION, RAILWAY_COMMIT
from database import init_db
from diagnostics import global_error_handler, router as diagnostics_router
from handlers import router as handlers_router
from scheduler import setup_scheduler
from storage import PostgreSQLStorage


COMMANDS = [
    BotCommand(command="start", description="Регистрация / главное меню"),
    BotCommand(command="menu", description="Главное меню"),
    BotCommand(command="games", description="Мини-игры"),
    BotCommand(command="balance", description="Баланс"),
    BotCommand(command="top", description="Таблица лидеров"),
    BotCommand(command="cashback", description="Кэшбек"),
    BotCommand(command="referral", description="Реферальная система"),
    BotCommand(command="shop", description="Магазин бустов"),
    BotCommand(command="promo", description="Активировать промокод"),
    BotCommand(command="stats", description="Статистика"),
    BotCommand(command="version", description="Версия запущенного бота"),
    BotCommand(command="health", description="Проверка бота и БД"),
]


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
    )
    log = logging.getLogger("startup")
    log.info("START version=%s commit=%s", BOT_VERSION, RAILWAY_COMMIT)

    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN не задан. Railway → service → Variables → BOT_TOKEN."
        )

    log.info("Инициализация и строгая проверка PostgreSQL…")
    health = await init_db()
    log.info("БД готова: %s", health)

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    # Если раньше был webhook, getUpdates/polling вернёт Conflict. Удаляем его.
    await bot.delete_webhook(drop_pending_updates=False)
    await bot.set_my_commands(COMMANDS)

    # ВАЖНО: не MemoryStorage. Шаги ручного ввода хранятся в PostgreSQL и
    # переживают рестарты Railway / новые деплои.
    storage = PostgreSQLStorage()
    dp = Dispatcher(storage=storage)
    # Диагностические команды должны срабатывать даже внутри FSM-состояния.
    dp.include_router(diagnostics_router)
    dp.include_router(handlers_router)
    dp.errors.register(global_error_handler)

    setup_scheduler(bot)

    me = await bot.get_me()
    log.info("READY @%s id=%s version=%s commit=%s", me.username, me.id,
             BOT_VERSION, RAILWAY_COMMIT)
    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
            close_bot_session=False,
        )
    finally:
        await storage.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
