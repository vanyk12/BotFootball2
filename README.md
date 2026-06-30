
# ⚽ Football Match Organizer

Telegram-бот + Mini App для организации футбольных матчей с ELO-балансировкой команд, расширенной статистикой и ачивками.

## 🚀 Деплой на Railway

### 1. Подготовка переменных окружения

Создай проект на [Railway](https://railway.app) и задай переменные:

| Переменная | Описание |
|---|---|
| `BOT_TOKEN` | Токен бота от [@BotFather](https://t.me/BotFather) |
| `WEBHOOK_URL` | Публичный URL Railway-приложения (HTTPS) |
| `MINI_APP_URL` | Тот же URL (Mini App хостится на том же домене) |
| `DATABASE_URL` | Connection string PostgreSQL (Supabase / Railway DB) |
| `ADMIN_IDS` | Telegram ID админов через запятую |
| `REMINDER_CHAT_ID` | ID чата для напоминаний о матчах |

### 2. База данных (Supabase)

1. Создай проект на [Supabase](https://supabase.com)
2. Скопируй **Connection string** (пулер, порт `6543`) в `DATABASE_URL`
3. Схема `schema.sql` применится автоматически при первом запуске

### 3. Деплой

```bash
# Установка Railway CLI
npm i -g @railway/cli

# Логин
railway login

# Создать проект и привязать
railway init
railway link

# Задай переменные в панели Railway или через CLI:
railway variables set BOT_TOKEN=your_token
railway variables set DATABASE_URL=your_db_url
railway variables set WEBHOOK_URL=https://your-app.up.railway.app
railway variables set MINI_APP_URL=https://your-app.up.railway.app
railway variables set ADMIN_IDS=123456789

# Деплой
railway up
```

### 4. Настройка Mini App в BotFather

После деплоя открой [@BotFather](https://t.me/BotFather):
1. `/newapp` → выбери бота
2. Name: `Football Organizer`
3. Description: `Организация матчей и статистика`
4. Image: загрузи иконку
5. Web App URL: `https://your-app.up.railway.app`

## 🏗️ Архитектура

```
┌─────────────────────────────────────────┐
│         Railway (Docker контейнер)       │
│                                         │
│   ┌─────────────┐  ┌────────────────┐   │
│   │   FastAPI   │  │   aiogram Bot  │   │
│   │  (uvicorn)  │──│   (webhook)    │   │
│   │             │  │                │   │
│   │  /api/*     │  │  /start /top   │   │
│   │  /static/*  │  │  /schedule     │   │
│   │  /webhook   │  │  /history      │   │
│   └──────┬──────┘  └───────┬────────┘   │
│          │                 │            │
│          └────────┬────────┘            │
│                   │                     │
│          ┌────────▼────────┐            │
│          │  asyncpg pool   │            │
│          │ (statement_     │            │
│          │  cache_size=0)  │            │
│          └────────┬────────┘            │
└───────────────────┼─────────────────────┘
                    │
          ┌─────────▼─────────┐
          │  PostgreSQL       │
          │  (Supabase)       │
          │                  │
          │  users            │
          │  matches          │
          │  registrations    │
          │  mvp_votes        │
          │  achievements     │
          └───────────────────┘
```

## 📋 Локальный запуск

```bash
# Установка зависимостей
pip install -r requirements.txt

# Задай переменные окружения
export BOT_TOKEN=your_token
export DATABASE_URL=postgresql://...
export WEBHOOK_URL=https://your-tunnel.ngrok.io
export ADMIN_IDS=your_id

# Запуск
python main.py
```

## ⚙️ Стек

- **Backend:** Python 3.12, FastAPI, aiogram 3.x
- **DB:** PostgreSQL (asyncpg, pgbouncer-совместимый пул)
- **Frontend:** Tailwind CSS (CDN), Vanilla JS, Telegram WebApp API
- **Deploy:** Docker, Railway
