"""Ежедневный кэшбек в 00:01 UTC."""
from __future__ import annotations

import logging
from datetime import date, timedelta

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from config import CASHBACK_MAX_DAILY, CASHBACK_MIN
from database import Cashback, SessionMaker, Transaction, User
from economy import active_buffs, cashback_percent

log = logging.getLogger(__name__)


async def process_daily_cashback(bot: Bot | None = None) -> None:
    yesterday = date.today() - timedelta(days=1)
    log.info("Daily cashback for %s", yesterday)
    async with SessionMaker() as s:
        rows = (await s.execute(
            select(Cashback).where(Cashback.day == yesterday, Cashback.claimed == False)  # noqa: E712
        )).scalars().all()
        for row in rows:
            u = (await s.execute(select(User).where(User.user_id == row.user_id))).scalar_one_or_none()
            if not u:
                continue
            buffs = active_buffs(u.active_buffs)
            percent = cashback_percent(buffs)
            potential = min(int(int(row.total_losses) * percent), CASHBACK_MAX_DAILY)
            available = max(0, potential - int(row.cashback_amount or 0))
            if available < CASHBACK_MIN:
                continue
            u.balance = int(u.balance) + available
            row.cashback_amount = int(row.cashback_amount or 0) + available
            row.claimed = True
            s.add(Transaction(
                user_id=u.user_id, type="cashback", amount=available,
                meta={"auto_daily": True, "percent": percent, "gross": int(row.total_losses)},
            ))
            if bot and u.tg_id:
                try:
                    await bot.send_message(
                        u.tg_id,
                        f"🔄 <b>Кэшбек за вчера: +{available:,} 🍬</b> "
                        f"({int(percent*100)}%)".replace(",", " "),
                        parse_mode="HTML",
                    )
                except Exception as e:
                    log.warning("Notify %s: %s", u.tg_id, e)
        await s.commit()


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        process_daily_cashback,
        CronTrigger(hour=0, minute=1),
        kwargs={"bot": bot},
        id="daily_cashback",
        replace_existing=True,
    )
    scheduler.start()
    job = scheduler.get_job("daily_cashback")
    log.info("Scheduler started. Next run: %s", job.next_run_time if job else "?")
    return scheduler
