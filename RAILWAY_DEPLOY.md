# Railway: обязательный redeploy Miner v7

## 1. Проверь Root Directory

Если GitHub имеет структуру:

```
repo/
  bot/
    bot.py
    railway.json
    requirements.txt
```

то Railway → сервис бота → **Settings → Source → Root Directory = `/bot`**.

Если `bot.py` находится прямо в корне GitHub-репозитория — Root Directory пустой.

## 2. Variables

В сервисе БОТА (не только в Postgres) должны быть:

```env
BOT_TOKEN=токен_от_BotFather
DATABASE_URL=${{Postgres.DATABASE_URL}}
ADMIN_USERNAMES=f0nt1ew,slash_zzzz
BOT_VERSION=miner-v7-postgres-fsm-2026-08-27
```

## 3. Deployment

* Start Command: `python -u bot.py`
* Replicas: **1**
* Удали/останови второй сервис этого же бота
* Останови локальный `python bot.py`
* Deployments → Redeploy → при возможности **Clear build cache**

## 4. Правильные Railway Logs

```
START version=miner-v7-postgres-fsm-2026-08-27 commit=...
Database schema verified: {'ok': True, ..., 'fsm': True}
READY @your_bot ... version=miner-v7-postgres-fsm-2026-08-27
```

Если строк `START ... v7` и `READY ... v7` нет — Railway запускает старый код/не тот root.

## 5. Проверка из Telegram

Отправь по порядку:

```
/version
/health
/start
/miner_debug
```

Ожидается:

* `/version`: `miner-v7-postgres-fsm-2026-08-27`
* `/health`: `✅ OK`, `PostgreSQL FSM: OK`
* `/miner_debug`: версия v7, состояние FSM и активная партия

Далее:

```
🎲 Игры → ⛏ Майнер
```

На шаге размера, мин и ставки обязательно есть кнопка:

```
✏️ Ввести вручную
```

Она переводит FSM в PostgreSQL-состояния:

* `MinerFSM:size_manual`
* `MinerFSM:mines_manual`
* `MinerFSM:bet_manual`

После перезапуска Railway состояние не теряется.

## 6. Если ошибка осталась

1. Отправь `/miner_debug`.
2. Нажми Miner и повтори ошибку.
3. Бот пришлёт код вида `Ошибка a1b2c3d4`.
4. Railway → Deployments → View Logs → найди `a1b2c3d4` — рядом полный traceback.

Без результата `/version`, `/health`, `/miner_debug` невозможно отличить ошибку кода от запуска старого deploy.
