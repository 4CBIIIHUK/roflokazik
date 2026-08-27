"""PostgreSQL FSM storage для aiogram.

MemoryStorage теряет шаг диалога при каждом рестарте Railway. Это хранилище
держит state/data в таблице bot_fsm и делает ручной ввод надёжным.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Optional

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey
from sqlalchemy import select

from database import BotFSM, SessionMaker


def _key(k: StorageKey) -> str:
    return ":".join(str(v) for v in (
        k.bot_id,
        k.chat_id,
        k.user_id,
        k.thread_id or 0,
        k.business_connection_id or "-",
        k.destiny,
    ))


def _json_safe(value: Any) -> Any:
    """FSM data → JSONB: Decimal/tuple/datetime сериализуем явно."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_json_safe(x) for x in value]
    if isinstance(value, list):
        return [_json_safe(x) for x in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    return value


class PostgreSQLStorage(BaseStorage):
    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        state_value = state.state if isinstance(state, State) else state
        db_key = _key(key)
        async with SessionMaker() as s:
            row = (await s.execute(select(BotFSM).where(BotFSM.key == db_key))).scalar_one_or_none()
            if row:
                row.state = state_value
                row.updated_at = datetime.utcnow()
            else:
                s.add(BotFSM(key=db_key, state=state_value, data={}))
            await s.commit()

    async def get_state(self, key: StorageKey) -> Optional[str]:
        async with SessionMaker() as s:
            row = (await s.execute(select(BotFSM).where(BotFSM.key == _key(key)))).scalar_one_or_none()
            return row.state if row else None

    async def set_data(self, key: StorageKey, data: Dict[str, Any]) -> None:
        db_key = _key(key)
        safe = _json_safe(data)
        async with SessionMaker() as s:
            row = (await s.execute(select(BotFSM).where(BotFSM.key == db_key))).scalar_one_or_none()
            if row:
                row.data = safe
                row.updated_at = datetime.utcnow()
            else:
                s.add(BotFSM(key=db_key, state=None, data=safe))
            await s.commit()

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        async with SessionMaker() as s:
            row = (await s.execute(select(BotFSM).where(BotFSM.key == _key(key)))).scalar_one_or_none()
            return dict(row.data or {}) if row else {}

    async def close(self) -> None:
        # engine закрывается процессом; отдельного соединения storage не держит
        return None
