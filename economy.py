"""Экономика: ставки, выигрыши, проигрыши, кэшбек, реф-цепочка.

Все суммы — Decimal (NUMERIC в БД): дробные ставки 0.0001 и гиганты 1e21.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from config import (
    CASHBACK_BASE, CASHBACK_MAX_DAILY, CASHBACK_MIN,
    REF_BONUS_FIRST_GAME, REF_BONUS_REACH_10K,
)
from database import (
    Cashback, Referral, ReferralBonus, SessionMaker, Transaction, User,
)

Q = Decimal("0.0000000001")  # квант 10 знаков
REF_CHAIN_PERCENT = Decimal("0.05")


def D(x: Any) -> Decimal:
    """Безопасное преобразование в Decimal (float → через str)."""
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x))


class NotEnoughFunds(Exception):
    pass


def active_buffs(raw: Any) -> dict:
    if not isinstance(raw, dict):
        return {}
    now = datetime.utcnow()
    out: dict = {}
    for k, v in raw.items():
        if not isinstance(v, dict):
            continue
        try:
            exp = datetime.fromisoformat(str(v.get("expires", "")).replace("Z", ""))
        except Exception:
            continue
        if exp < now:
            continue
        if int(v.get("charges", 1)) <= 0:
            continue
        out[k] = v
    return out


def cashback_percent(buffs: dict) -> float:
    if "x3_cashback" in buffs:
        return 0.30
    if "x2_cashback" in buffs:
        return 0.20
    return CASHBACK_BASE


def coef_multiplier(buffs: dict) -> Decimal:
    return Decimal("1.5") if "x15_coef" in buffs else Decimal("1")


def bonus_amount(buffs: dict) -> int:
    return 2_000 if "x2_bonus" in buffs else 1_000


async def debit_bet(user_id: int, amount, meta: dict | None = None) -> Decimal:
    amount = D(amount)
    async with SessionMaker() as s:
        u = (await s.execute(select(User).where(User.user_id == user_id))).scalar_one()
        if D(u.balance) < amount:
            raise NotEnoughFunds("Недостаточно Фантиков")
        u.balance = D(u.balance) - amount
        u.games_played = int(u.games_played) + 1
        s.add(Transaction(user_id=user_id, type="bet", amount=-amount, meta=meta or {}))
        first_game_was_none = u.first_game_at is None
        if first_game_was_none:
            u.first_game_at = datetime.utcnow()
        await s.commit()
        new_balance = D(u.balance)
        referrer_id = u.referrer_id

    if first_game_was_none and referrer_id:
        async with SessionMaker() as s:
            ref = (await s.execute(select(User).where(User.user_id == referrer_id))).scalar_one_or_none()
            if ref:
                ref.balance = D(ref.balance) + REF_BONUS_FIRST_GAME
                s.add(Transaction(
                    user_id=ref.user_id, type="ref_bonus",
                    amount=REF_BONUS_FIRST_GAME,
                    meta={"reason": "first_game", "referred": user_id},
                ))
                await s.commit()
    return new_balance


async def credit_win(user_id: int, base_amount, meta: dict | None = None) -> Decimal:
    base_amount = D(base_amount)
    async with SessionMaker() as s:
        u = (await s.execute(select(User).where(User.user_id == user_id))).scalar_one()
        buffs = active_buffs(u.active_buffs)
        mult = coef_multiplier(buffs)
        amount = (base_amount * mult).quantize(Q)
        u.balance = D(u.balance) + amount
        u.total_wins = D(u.total_wins) + amount
        s.add(Transaction(
            user_id=user_id, type="win", amount=amount,
            meta={**(meta or {}), "mult": str(mult)},
        ))
        milestone = False
        referrer_id = None
        if not u.reached_10k_bonus_given and D(u.balance) >= 10_000 and u.referrer_id:
            u.reached_10k_bonus_given = True
            milestone = True
            referrer_id = u.referrer_id
        await s.commit()

    if milestone and referrer_id:
        async with SessionMaker() as s:
            ref = (await s.execute(select(User).where(User.user_id == referrer_id))).scalar_one_or_none()
            if ref:
                ref.balance = D(ref.balance) + REF_BONUS_REACH_10K
                s.add(Transaction(
                    user_id=ref.user_id, type="ref_bonus",
                    amount=REF_BONUS_REACH_10K,
                    meta={"reason": "reach_10k", "referred": user_id},
                ))
                await s.commit()
    return amount


async def register_loss(user_id: int, loss_amount, meta: dict | None = None) -> dict:
    loss_amount = D(loss_amount)
    refunded = Decimal(0)
    async with SessionMaker() as s:
        u = (await s.execute(select(User).where(User.user_id == user_id))).scalar_one()
        buffs = dict(u.active_buffs or {})
        ins = buffs.get("insurance")
        if ins and int(ins.get("charges", 0)) > 0:
            refunded = (loss_amount / 2).quantize(Q)
            u.balance = D(u.balance) + refunded
            ins["charges"] = int(ins["charges"]) - 1
            if ins["charges"] <= 0:
                buffs.pop("insurance", None)
            else:
                buffs["insurance"] = ins
            u.active_buffs = buffs
            s.add(Transaction(
                user_id=user_id, type="insurance", amount=refunded, meta=meta or {},
            ))

        net_loss = max(Decimal(0), loss_amount - refunded)
        u.total_losses = D(u.total_losses) + net_loss
        s.add(Transaction(user_id=user_id, type="loss", amount=-net_loss, meta=meta or {}))

        if net_loss > 0:
            today = date.today()
            existing = (await s.execute(
                select(Cashback).where(Cashback.user_id == user_id, Cashback.day == today)
            )).scalar_one_or_none()
            if existing:
                existing.total_losses = D(existing.total_losses) + net_loss
            else:
                s.add(Cashback(user_id=user_id, day=today, total_losses=net_loss))
        await s.commit()

    if net_loss > 0:
        async with SessionMaker() as s:
            links = (await s.execute(
                select(Referral).where(Referral.referred_id == user_id)
            )).scalars().all()
            for link in links:
                bonus = (net_loss * REF_CHAIN_PERCENT).quantize(Q)
                if bonus <= 0:
                    continue
                ref_user = (await s.execute(
                    select(User).where(User.user_id == link.referrer_id)
                )).scalar_one_or_none()
                if not ref_user:
                    continue
                ref_user.balance = D(ref_user.balance) + bonus
                s.add(ReferralBonus(
                    user_id=ref_user.user_id, source_user_id=user_id,
                    amount=bonus, loss_amount=net_loss, level=link.level,
                ))
                s.add(Transaction(
                    user_id=ref_user.user_id, type="ref_bonus", amount=bonus,
                    meta={"reason": "chain_loss", "source": user_id, "level": link.level},
                ))
            await s.commit()
    return {"refunded": refunded, "net_loss": net_loss}


async def compute_today_cashback(user_id: int) -> dict:
    async with SessionMaker() as s:
        u = (await s.execute(select(User).where(User.user_id == user_id))).scalar_one()
        row = (await s.execute(
            select(Cashback).where(Cashback.user_id == user_id, Cashback.day == date.today())
        )).scalar_one_or_none()
    buffs = active_buffs(u.active_buffs)
    percent = cashback_percent(buffs)
    gross = D(row.total_losses) if row else Decimal(0)
    already = D(row.cashback_amount) if row else Decimal(0)
    claimed = bool(row.claimed) if row else False
    potential = min((gross * D(percent)).quantize(Q), Decimal(CASHBACK_MAX_DAILY))
    available = Decimal(0) if claimed else max(Decimal(0), potential - already)
    return {"gross": gross, "percent": percent, "available": available, "claimed": claimed}


async def claim_cashback(user_id: int) -> Decimal:
    info = await compute_today_cashback(user_id)
    if info["available"] < CASHBACK_MIN:
        raise ValueError(f"Минимальный кэшбек — {CASHBACK_MIN} 🍬")
    amount = info["available"]
    async with SessionMaker() as s:
        u = (await s.execute(select(User).where(User.user_id == user_id))).scalar_one()
        u.balance = D(u.balance) + amount
        row = (await s.execute(
            select(Cashback).where(Cashback.user_id == user_id, Cashback.day == date.today())
        )).scalar_one_or_none()
        if row:
            row.cashback_amount = D(row.cashback_amount or 0) + amount
            row.claimed = True
        s.add(Transaction(
            user_id=user_id, type="cashback", amount=amount,
            meta={"percent": info["percent"], "gross": str(info["gross"])},
        ))
        await s.commit()
    return amount
