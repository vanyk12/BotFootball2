
"""
Football Match Organizer — FastAPI + aiogram 3.x (single asyncio loop)
Telegram Bot + Mini App
"""
import os
import hmac
import hashlib
import json
import asyncio
import logging
import math
import urllib.parse
from datetime import datetime, timezone, timedelta
from typing import Any, Optional
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo,
    Update, Message, CallbackQuery
)
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramConflictError

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
BOT_TOKEN    = os.getenv("BOT_TOKEN", "")
WEBHOOK_URL  = os.getenv("WEBHOOK_URL", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")
ADMIN_IDS    = set(int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip())

BOT_USERNAME = os.getenv("BOT_USERNAME", "football_organizer_bot")
MINI_APP_URL = os.getenv("MINI_APP_URL", WEBHOOK_URL)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN env var is required")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL env var is required")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("fmo")

# ─────────────────────────────────────────────
# DB POOL
# ─────────────────────────────────────────────
pool: Optional[asyncpg.Pool] = None

async def init_db():
    global pool
    pool = await asyncpg.create_pool(
        DATABASE_URL,
        statement_cache_size=0,
        min_size=1,
        max_size=10,
    )
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path, "r") as f:
        schema_sql = f.read()
    async with pool.acquire() as conn:
        await conn.execute(schema_sql)
    migration_path = os.path.join(os.path.dirname(__file__), "migration.sql")
    if os.path.exists(migration_path):
        with open(migration_path, "r") as f:
            migration_sql = f.read()
        async with pool.acquire() as conn:
            await conn.execute(migration_sql)
    log.info("DB initialized & migrated")

# ─────────────────────────────────────────────
# TELEGRAM INIT-DATA VALIDATION
# ─────────────────────────────────────────────
def validate_init_data(init_data: str) -> dict:
    """Validate Telegram WebApp initData via HMAC-SHA256."""
    if not init_data:
        raise ValueError("empty init_data")
    parsed = urllib.parse.parse_qs(init_data)
    hash_str = parsed.pop("hash", [None])[0]
    if not hash_str:
        raise ValueError("no hash in init_data")

    data_check_string = "\n".join(
        f"{k}={v[0]}" for k, v in sorted(parsed.items())
    )
    secret_key = hmac.new(
        b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256
    ).digest()
    computed = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(computed, hash_str):
        raise ValueError("invalid init_data signature")

    user_raw = parsed.get("user", [None])[0]
    if not user_raw:
        raise ValueError("no user in init_data")
    user = json.loads(user_raw)
    return user

async def get_or_create_user_from_initdata(init_data: str) -> dict:
    """Validate init_data, return user info merged with DB record."""
    tg_user = validate_init_data(init_data)
    uid = tg_user["id"]
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE id=$1", uid)
        if row:
            return dict(row)
    
    # Generate name fallback: first_name + last_name, else username, else ID
    name = (tg_user.get("first_name","") + " " + tg_user.get("last_name","")).strip()
    if not name:
        name = tg_user.get("username", "") or str(uid)
        
    return {
        "id": uid,
        "username": tg_user.get("username", ""),
        "name": name,
        "photo_url": tg_user.get("photo_url", ""),
        "position": "unknown",
        "skill_level": 50.0,
        "goals": 0, "assists": 0,
        "wins": 0, "losses": 0, "draws": 0,
        "matches_played": 0, "mvp_count": 0,
        "created_at": None,
    }

async def get_user_from_request(request: Request) -> dict:
    """
    Extract init_data flexibly:
    - From 'Authorization: Bearer <initData>' header
    - From '?init_data=<...>' query parameter (critical for Telegram Desktop)
    - Fallback to 'X-Telegram-Init-Data' header
    """
    init_data = ""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        init_data = auth_header[7:]
    if not init_data:
        init_data = request.query_params.get("init_data", "")
    if not init_data:
        init_data = request.headers.get("X-Telegram-Init-Data", "")
    
    if not init_data:
        raise HTTPException(401, "missing init_data")
    try:
        return await get_or_create_user_from_initdata(init_data)
    except ValueError as e:
        raise HTTPException(403, f"invalid init_data: {e}")

# ─────────────────────────────────────────────
# ELO BALANCING & RECALCULATION
# ─────────────────────────────────────────────
def balance_teams(players: list[dict]) -> tuple[list[dict], list[dict]]:
    sorted_players = sorted(players, key=lambda p: p.get("skill_level", 50.0), reverse=True)
    team_a: list[dict] = []
    team_b: list[dict] = []
    for i, p in enumerate(sorted_players):
        entry = {
            "id": str(p["id"]),
            "name": p.get("name") or p.get("username") or str(p["id"]),
            "username": p.get("username",""),
            "position": p.get("position","unknown"),
            "skill_level": float(p.get("skill_level", 50.0)),
            "goals_in_match": 0,
        }
        if i % 2 == 0:
            team_a.append(entry)
        else:
            team_b.append(entry)
    return team_a, team_b

def team_skill(team: list[dict]) -> float:
    if not team: return 50.0
    return sum(p["skill_level"] for p in team) / len(team)

def recalc_elo(player_skill: float, team_skill: float, opp_skill: float, result: float, k: float = 32.0) -> float:
    expected = 1.0 / (1.0 + math.pow(10, (opp_skill - team_skill) / 20.0))
    new_skill = player_skill + k * (result - expected)
    return max(0.0, min(100.0, new_skill))

# ─────────────────────────────────────────────
# ACHIEVEMENTS
# ─────────────────────────────────────────────
ACHIEVEMENTS = {
    "hat_trick":   {"emoji": "🎩", "name": "Hat-trick",     "desc": "3+ голов за матч"},
    "veteran":     {"emoji": "🎖️", "name": "Veteran",       "desc": "10+ сыгранных матчей"},
    "match_hero":  {"emoji": "🏆", "name": "Match Hero",    "desc": "Стал MVP матча"},
    "striker":     {"emoji": "⚡", "name": "Striker",        "desc": "10+ голов всего"},
    "playmaker":   {"emoji": "🎯", "name": "Playmaker",     "desc": "10+ ассистов всего"},
    "wall":        {"emoji": "🧱", "name": "The Wall",       "desc": "Вратарь с 5 победами"},
    "legend":      {"emoji": "👑", "name": "Legend",        "desc": "ELO 80+"},
}

async def check_and_award_achievements(conn, user_id: int):
    user = await conn.fetchrow("SELECT * FROM users WHERE id=$1", user_id)
    if not user: return
    codes = []
    if user["goals"] >= 10: codes.append("striker")
    if user["assists"] >= 10: codes.append("playmaker")
    if user["matches_played"] >= 10: codes.append("veteran")
    if user["skill_level"] >= 80: codes.append("legend")
    if user["position"] == "Вратарь" and user["wins"] >= 5: codes.append("wall")
    for c in codes:
        await conn.execute(
            """INSERT INTO user_achievements (user_id, achievement_code)
               VALUES ($1,$2) ON CONFLICT DO NOTHING""",
            user_id, c
        )

# ─────────────────────────────────────────────
# BOT INIT
# ─────────────────────────────────────────────
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()
router = Router()
dp.include_router(router)

def mini_app_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="⚽ Открыть Football Organizer",
            web_app=WebAppInfo(url=MINI_APP_URL)
        )
    ]])
    return kb

@router.message(F.text == "/start")
async def cmd_start(message: Message):
    uid = message.from_user.id
    name = message.from_user.full_name or message.from_user.username or str(uid)
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO users (id, username, name)
               VALUES ($1,$2,$3)
               ON CONFLICT (id) DO UPDATE SET
                 username=COALESCE($2, users.username),
                 name=COALESCE($3, users.name)""",
            uid,
            message.from_user.username,
            name
        )
    txt = (
        "⚽ <b>Football Match Organizer</b>\n\n"
        "Добро пожаловать! Здесь ты можешь:\n"
        "• Записаться на ближайший матч\n"
        "• Следить за своей статистикой и ELO-рейтингом\n"
        "• Голосовать за MVP и получать ачивки\n\n"
        "Нажми кнопку ниже, чтобы открыть мини-приложение 🏟️"
    )
    await message.answer(txt, reply_markup=mini_app_keyboard())

@router.message(F.text == "/top")
async def cmd_top(message: Message):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT name, username, skill_level, goals, assists, mvp_count, matches_played
               FROM users ORDER BY skill_level DESC, mvp_count DESC LIMIT 10"""
        )
    if not rows:
        await message.answer("📭 Пока нет зарегистрированных игроков.")
        return
    lines = ["🏅 <b>Топ-10 игроков</b>\n"]
    for i, r in enumerate(rows, 1):
        nm = r["name"] or r["username"] or str(r["skill_level"])
        lines.append(
            f"{i}. {nm} — <b>{r['skill_level']:.1f}</b> ELO "
            f"⚽{r['goals']} 🅰️{r['assists']} 🏆{r['mvp_count']} 🎮{r['matches_played']}"
        )
    await message.answer("\n".join(lines))

@router.message(F.text == "/schedule")
async def cmd_schedule(message: Message):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, scheduled_at, location, (SELECT count(*) FROM match_registrations WHERE match_id=m.id) AS regs
               FROM matches m
               WHERE status='scheduled' AND scheduled_at IS NOT NULL
               ORDER BY scheduled_at ASC LIMIT 10"""
        )
    if not rows:
        await message.answer("📅 Запланированных матчей пока нет.")
        return
    lines = ["📅 <b>Ближайшие матчи</b>\n"]
    for r in rows:
        dt = r["scheduled_at"].strftime("%d.%m %H:%M") if r["scheduled_at"] else "—"
        loc = r["location"] or "не указано"
        lines.append(f"🗓 {dt} | 📍 {loc} | 👥 {r['regs']} записались")
    await message.answer("\n".join(lines))

@router.message(F.text == "/history")
async def cmd_history(message: Message):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT finished_at, score_a, score_b, team_a, team_b
               FROM matches WHERE status='finished'
               ORDER BY finished_at DESC LIMIT 5"""
        )
    if not rows:
        await message.answer("📜 История матчей пуста.")
        return
    lines = ["📜 <b>Последние 5 матчей</b>\n"]
    for r in rows:
        dt = r["finished_at"].strftime("%d.%m %H:%M") if r["finished_at"] else "—"
        sa, sb = r["score_a"], r["score_b"]
        if sa > sb: res = "🟢 Команда А"
        elif sb > sa: res = "🔵 Команда Б"
        else: res = "🤝 Ничья"
        lines.append(f"{dt} | <b>{sa}:{sb}</b> | {res}")
    await message.answer("\n".join(lines))

@router.message(F.text.startswith("/stats"))
async def cmd_stats(message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        uid = message.from_user.id
    else:
        uname = parts[1].lstrip("@").lower()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT id FROM users WHERE lower(username)=$1", uname)
            if not row:
                await message.answer(f"❌ Игрок @{uname} не найден.")
                return
            uid = row["id"]
    async with pool.acquire() as conn:
        u = await conn.fetchrow("SELECT * FROM users WHERE id=$1", uid)
        achs = await conn.fetch("SELECT achievement_code FROM user_achievements WHERE user_id=$1", uid)
    if not u:
        await message.answer("❌ Игрок не найден.")
        return
    total = u["wins"] + u["losses"] + u["draws"]
    winrate = (u["wins"] / total * 100) if total else 0
    ach_str = " ".join(ACHIEVEMENTS.get(a["achievement_code"],{}).get("emoji","⭐") for a in achs) or "—"
    txt = (
        f"📊 <b>Статистика: {u['name'] or u['username'] or uid}</b>\n\n"
        f"🏆 ELO: <b>{u['skill_level']:.1f}</b>\n"
        f"📍 Позиция: {u['position']}\n"
        f"🎮 Матчей: {u['matches_played']}\n"
        f"🟢 Побед: {u['wins']} | 🔴 Поражений: {u['losses']} | 🤝 Ничьих: {u['draws']}\n"
        f"📈 Winrate: <b>{winrate:.1f}%</b>\n"
        f"⚽ Голов: {u['goals']} | 🅰️ Ассистов: {u['assists']}\n"
        f"🏅 MVP: {u['mvp_count']}\n"
        f"🎖️ Ачивки: {ach_str}"
    )
    await message.answer(txt)

# ─────────────────────────────────────────────
# BACKGROUND TASK: REMINDERS
# ─────────────────────────────────────────────
REMINDER_CHAT_ID = os.getenv("REMINDER_CHAT_ID", "")
async def reminders_loop():
    notified_2h: set[str] = set()
    notified_30: set[str] = set()
    while True:
        try:
            now = datetime.now(timezone.utc)
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT id, scheduled_at, location FROM matches
                       WHERE status='scheduled' AND scheduled_at IS NOT NULL"""
                )
            for r in rows:
                mid = str(r["id"])
                sched = r["scheduled_at"]
                if sched.tzinfo is None:
                    sched = sched.replace(tzinfo=timezone.utc)
                delta = (sched - now).total_seconds()
                key_2h = mid + "_2h"
                key_30 = mid + "_30"
                if 0 < delta <= 7200 and key_2h not in notified_2h:
                    notified_2h.add(key_2h)
                    if REMINDER_CHAT_ID:
                        await bot.send_message(
                            REMINDER_CHAT_ID,
                            f"⏰ <b>Матч через ~2 часа!</b>\n🕒 {sched.strftime('%d.%m %H:%M')} UTC\n📍 {r['location'] or '—'}\n\nГотовьтесь! ⚽"
                        )
                if 0 < delta <= 1800 and key_30 not in notified_30:
                    notified_30.add(key_30)
                    if REMINDER_CHAT_ID:
                        await bot.send_message(
                            REMINDER_CHAT_ID,
                            f"🔥 <b>Матч через 30 минут!</b>\nЖдём всех на площадке! 🏃💨"
                        )
        except Exception as e:
            log.error(f"reminders_loop error: {e}")
        await asyncio.sleep(60)

# ─────────────────────────────────────────────
# FASTAPI APP & LIFESPAN
# ─────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    if WEBHOOK_URL:
        wh_url = WEBHOOK_URL.rstrip("/") + "/webhook"
        secret_token_clean = BOT_TOKEN[:32].replace(":", "_")
        try:
            await bot.set_webhook(wh_url, secret_token=secret_token_clean)
            log.info(f"Webhook set: {wh_url}")
        except TelegramConflictError:
            log.warning("Webhook conflict (already set elsewhere)")
        except Exception as e:
            log.error(f"set_webhook error: {e}")
    task = asyncio.create_task(reminders_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await bot.session.close()

app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")

# ─────────────────────────────────────────────
# PYDANTIC MODELS
# ─────────────────────────────────────────────
class RegisterReq(BaseModel):
    name: str = ""
    position: str = "unknown"
    photo_url: str = ""

class MatchCreateReq(BaseModel):
    player_ids: list[int] = []
    scheduled_at: Optional[str] = None
    location: str = ""

class ScoreReq(BaseModel):
    match_id: str
    team: str
    delta: int

class FinishReq(BaseModel):
    match_id: str

class VoteMVPReq(BaseModel):
    match_id: str
    candidate_id: int

class RegisterIntentReq(BaseModel):
    match_id: str

class GoalReq(BaseModel):
    match_id: str
    team: str
    player_id: str
    delta: int = 1

class CloseVotingReq(BaseModel):
    match_id: str

class ScheduleReq(BaseModel):
    scheduled_at: str
    location: str = ""

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def is_admin(user: dict) -> bool:
    return user["id"] in ADMIN_IDS

# ─────────────────────────────────────────────
# SERVE MINI APP FRONTEND (Multi-route)
# ─────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
@app.get("/mini-app", response_class=HTMLResponse)
@app.get("/miniapp", response_class=HTMLResponse)
async def serve_mini_app():
    html_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())

# ─────────────────────────────────────────────
# TELEGRAM WEBHOOK & HEALTH
# ─────────────────────────────────────────────
@app.post("/webhook")
async def telegram_webhook(request: Request):
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if secret != BOT_TOKEN[:32].replace(":", "_"):
        raise HTTPException(403, "bad secret")
    data = await request.json()
    update = Update.model_validate(data, context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"ok": True}

@app.get("/health")
async def health():
    return {"status": "ok"}

# ─────────────────────────────────────────────
# API ROUTES
# ─────────────────────────────────────────────
@app.get("/api/me")
async def api_me(request: Request):
    user = await get_user_from_request(request)
    async with pool.acquire() as conn:
        achs = await conn.fetch(
            "SELECT achievement_code FROM user_achievements WHERE user_id=$1",
            user["id"]
        )
    user["achievements"] = [a["achievement_code"] for a in achs]
    user["all_achievements"] = ACHIEVEMENTS
    return user

@app.post("/api/register")
async def api_register(req: RegisterReq, request: Request):
    tg_user = await get_user_from_request(request)
    uid = tg_user["id"]
    valid_positions = ["Вратарь","Защитник","Нападающий","unknown"]
    pos = req.position if req.position in valid_positions else "unknown"
    
    # Fallback for empty name: use tg_user's name, then username, then ID
    name = req.name.strip() or tg_user.get("name","") or tg_user.get("username","") or str(uid)
    
    photo = req.photo_url or tg_user.get("photo_url","")
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO users (id, username, name, photo_url, position)
               VALUES ($1,$2,$3,$4,$5)
               ON CONFLICT (id) DO UPDATE SET
                 name=EXCLUDED.name,
                 photo_url=EXCLUDED.photo_url,
                 position=EXCLUDED.position""",
            uid, tg_user.get("username",""), name, photo, pos
        )
        row = await conn.fetchrow("SELECT * FROM users WHERE id=$1", uid)
    return dict(row)

@app.get("/api/users")
async def api_users(request: Request):
    user = await get_user_from_request(request)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, name, username, photo_url, position, skill_level,
                      goals, assists, matches_played, mvp_count, wins, losses, draws
               FROM users ORDER BY skill_level DESC"""
        )
    return {
        "is_admin": is_admin(user),
        "users": [dict(r) for r in rows]
    }

@app.post("/api/match/create")
async def api_match_create(req: MatchCreateReq, request: Request):
    user = await get_user_from_request(request)
    if not is_admin(user):
        raise HTTPException(403, "admin only")
    if len(req.player_ids) < 2:
        raise HTTPException(400, "need at least 2 players")
    async with pool.acquire() as conn:
        await conn.execute("UPDATE matches SET status='finished' WHERE status IN ('active','scheduled')")
        rows = await conn.fetch(
            "SELECT * FROM users WHERE id = ANY($1::bigint[])",
            req.player_ids
        )
        players = [dict(r) for r in rows]
        team_a, team_b = balance_teams(players)
        sched = None
        if req.scheduled_at:
            try:
                sched = datetime.fromisoformat(req.scheduled_at)
            except:
                sched = None
        row = await conn.fetchrow(
            """INSERT INTO matches (status, scheduled_at, location, team_a, team_b)
               VALUES ('active', $1, $2, $3, $4) RETURNING id""",
            sched, req.location or None,
            json.dumps(team_a), json.dumps(team_b)
        )
        mid = str(row["id"])
        await conn.execute("UPDATE matches SET started_at=now() WHERE id=$1", row["id"])
    return {"match_id": mid, "team_a": team_a, "team_b": team_b}

@app.get("/api/match/active")
async def api_match_active(request: Request):
    user = await get_user_from_request(request)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT * FROM matches WHERE status='active' ORDER BY started_at DESC LIMIT 1"""
        )
        if not row:
            return {"active": False}
        regs = await conn.fetch(
            "SELECT user_id FROM match_registrations WHERE match_id=$1", row["id"]
        )
    return {
        "active": True,
        "match_id": str(row["id"]),
        "status": row["status"],
        "started_at": row["started_at"].isoformat() if row["started_at"] else None,
        "scheduled_at": row["scheduled_at"].isoformat() if row["scheduled_at"] else None,
        "location": row["location"],
        "team_a": row["team_a"],
        "team_b": row["team_b"],
        "score_a": row["score_a"],
        "score_b": row["score_b"],
        "registrations": [str(r["user_id"]) for r in regs],
    }

@app.post("/api/match/score")
async def api_match_score(req: ScoreReq, request: Request):
    user = await get_user_from_request(request)
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM matches WHERE id=$1", req.match_id)
        if not row or row["status"] != "active":
            raise HTTPException(400, "no active match")
        if req.team == "a":
            new_score = max(0, row["score_a"] + req.delta)
            await conn.execute("UPDATE matches SET score_a=$1 WHERE id=$2", new_score, req.match_id)
        else:
            new_score = max(0, row["score_b"] + req.delta)
            await conn.execute("UPDATE matches SET score_b=$1 WHERE id=$2", new_score, req.match_id)
    return {"ok": True, "new_score": new_score}

@app.post("/api/match/goal")
async def api_match_goal(req: GoalReq, request: Request):
    user = await get_user_from_request(request)
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM matches WHERE id=$1", req.match_id)
        if not row or row["status"] != "active":
            raise HTTPException(400, "no active match")
        col = "team_a" if req.team == "a" else "team_b"
        score_col = "score_a" if req.team == "a" else "score_b"
        team = row[col] if isinstance(row[col], list) else json.loads(row[col])
        found = False
        for p in team:
            if str(p["id"]) == str(req.player_id):
                p["goals_in_match"] = max(0, p.get("goals_in_match",0) + req.delta)
                found = True
                break
        if not found:
            raise HTTPException(400, "player not in team")
        new_score = max(0, row[score_col] + req.delta)
        await conn.execute(
            f"UPDATE matches SET {col}=$1, {score_col}=$2 WHERE id=$3",
            json.dumps(team), new_score, req.match_id
        )
    return {"ok": True, "team": req.team, "new_score": new_score, "team_players": team}

@app.post("/api/match/finish")
async def api_match_finish(req: FinishReq, request: Request):
    user = await get_user_from_request(request)
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM matches WHERE id=$1", req.match_id)
        if not row or row["status"] != "active":
            raise HTTPException(400, "no active match to finish")
        sa, sb = row["score_a"], row["score_b"]
        team_a = row["team_a"] if isinstance(row["team_a"], list) else json.loads(row["team_a"])
        team_b = row["team_b"] if isinstance(row["team_b"], list) else json.loads(row["team_b"])
        skill_a = team_skill(team_a)
        skill_b = team_skill(team_b)
        result_a = 1.0 if sa > sb else (0.0 if sa < sb else 0.5)
        result_b = 1.0 - result_a
        await conn.execute(
            "UPDATE matches SET status='finished', finished_at=now() WHERE id=$1",
            req.match_id
        )
        award_codes: list[tuple[int,str]] = []
        async with conn.transaction():
            for p in team_a:
                pid = int(p["id"])
                goals_in = p.get("goals_in_match",0)
                u = await conn.fetchrow("SELECT * FROM users WHERE id=$1", pid)
                if not u: continue
                new_elo = recalc_elo(u["skill_level"], skill_a, skill_b, result_a)
                wins = u["wins"] + (1 if result_a==1.0 else 0)
                losses = u["losses"] + (1 if result_a==0.0 else 0)
                draws = u["draws"] + (1 if result_a==0.5 else 0)
                await conn.execute(
                    """UPDATE users SET skill_level=$1, goals=goals+$2, wins=$3, losses=$4, draws=$5,
                       matches_played=matches_played+1 WHERE id=$6""",
                    round(new_elo,2), goals_in, wins, losses, draws, pid
                )
                if goals_in >= 3:
                    award_codes.append((pid,"hat_trick"))
            for p in team_b:
                pid = int(p["id"])
                goals_in = p.get("goals_in_match",0)
                u = await conn.fetchrow("SELECT * FROM users WHERE id=$1", pid)
                if not u: continue
                new_elo = recalc_elo(u["skill_level"], skill_b, skill_a, result_b)
                wins = u["wins"] + (1 if result_b==1.0 else 0)
                losses = u["losses"] + (1 if result_b==0.0 else 0)
                draws = u["draws"] + (1 if result_b==0.5 else 0)
                await conn.execute(
                    """UPDATE users SET skill_level=$1, goals=goals+$2, wins=$3, losses=$4, draws=$5,
                       matches_played=matches_played+1 WHERE id=$6""",
                    round(new_elo,2), goals_in, wins, losses, draws, pid
                )
                if goals_in >= 3:
                    award_codes.append((pid,"hat_trick"))
            for pid, code in award_codes:
                await conn.execute(
                    """INSERT INTO user_achievements (user_id, achievement_code)
                       VALUES ($1,$2) ON CONFLICT DO NOTHING""",
                    pid, code
                )
            for p in team_a + team_b:
                await check_and_award_achievements(conn, int(p["id"]))
    return {"ok": True, "score_a": sa, "score_b": sb}

@app.get("/api/matches/history")
async def api_matches_history(request: Request):
    user = await get_user_from_request(request)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, finished_at, started_at, score_a, score_b, team_a, team_b, location
               FROM matches WHERE status='finished'
               ORDER BY finished_at DESC LIMIT 10"""
        )
    all_ids: set[int] = set()
    for r in rows:
        for p in (r["team_a"] if isinstance(r["team_a"],list) else json.loads(r["team_a"])):
            all_ids.add(int(p["id"]))
        for p in (r["team_b"] if isinstance(r["team_b"],list) else json.loads(r["team_b"])):
            all_ids.add(int(p["id"]))
    name_map = {}
    if all_ids:
        async with pool.acquire() as conn:
            users = await conn.fetch("SELECT id, name, username, photo_url FROM users WHERE id = ANY($1::bigint[])", list(all_ids))
        for u in users:
            name_map[str(u["id"])] = {
                "name": u["name"] or u["username"] or str(u["id"]),
                "username": u["username"] or "",
                "photo_url": u["photo_url"] or "",
            }
    result = []
    for r in rows:
        ta = r["team_a"] if isinstance(r["team_a"],list) else json.loads(r["team_a"])
        tb = r["team_b"] if isinstance(r["team_b"],list) else json.loads(r["team_b"])
        result.append({
            "match_id": str(r["id"]),
            "finished_at": r["finished_at"].isoformat() if r["finished_at"] else None,
            "started_at": r["started_at"].isoformat() if r["started_at"] else None,
            "score_a": r["score_a"],
            "score_b": r["score_b"],
            "team_a": ta,
            "team_b": tb,
            "location": r["location"],
            "name_map": name_map,
        })
    return result

@app.post("/api/match/vote_mvp")
async def api_vote_mvp(req: VoteMVPReq, request: Request):
    user = await get_user_from_request(request)
    voter_id = user["id"]
    if req.candidate_id == voter_id:
        raise HTTPException(400, "cannot vote for yourself")
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM matches WHERE id=$1", req.match_id)
        if not row:
            raise HTTPException(404, "match not found")
        if row["status"] != "finished":
            raise HTTPException(400, "match not finished yet")
        all_players = []
        for col in ("team_a","team_b"):
            t = row[col] if isinstance(row[col],list) else json.loads(row[col])
            all_players.extend(t)
        if not any(str(p["id"])==str(req.candidate_id) for p in all_players):
            raise HTTPException(400, "candidate not in match")
        await conn.execute(
            """INSERT INTO mvp_votes (match_id, voter_id, candidate_id)
               VALUES ($1,$2,$3) ON CONFLICT DO UPDATE SET candidate_id=$3""",
            req.match_id, voter_id, req.candidate_id
        )
    return {"ok": True}

@app.post("/api/match/close_voting")
async def api_close_voting(req: CloseVotingReq, request: Request):
    user = await get_user_from_request(request)
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM matches WHERE id=$1", req.match_id)
        if not row:
            raise HTTPException(404, "match not found")
        votes = await conn.fetch(
            """SELECT candidate_id, count(*) as cnt FROM mvp_votes
               WHERE match_id=$1 GROUP BY candidate_id ORDER BY cnt DESC""",
            req.match_id
        )
        if not votes:
            return {"ok": True, "mvp": None}
        winner_id = votes[0]["candidate_id"]
        await conn.execute(
            "UPDATE users SET mvp_count=mvp_count+1, skill_level=LEAST(100, skill_level+3.0) WHERE id=$1",
            winner_id
        )
        await conn.execute(
            """INSERT INTO user_achievements (user_id, achievement_code)
               VALUES ($1,'match_hero') ON CONFLICT DO NOTHING""",
            winner_id
        )
        await check_and_award_achievements(conn, winner_id)
        w = await conn.fetchrow("SELECT name, username FROM users WHERE id=$1", winner_id)
    return {
        "ok": True,
        "mvp_id": str(winner_id),
        "mvp_name": (w["name"] or w["username"] or str(winner_id)) if w else str(winner_id),
        "votes": [{"candidate_id": str(v["candidate_id"]), "count": v["cnt"]} for v in votes]
    }

@app.post("/api/match/schedule")
async def api_match_schedule(req: ScheduleReq, request: Request):
    user = await get_user_from_request(request)
    if not is_admin(user):
        raise HTTPException(403, "admin only")
    try:
        sched = datetime.fromisoformat(req.scheduled_at)
    except:
        raise HTTPException(400, "bad datetime")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO matches (status, scheduled_at, location)
               VALUES ('scheduled', $1, $2) RETURNING id""",
            sched, req.location or None
        )
    return {"match_id": str(row["id"])}

@app.get("/api/match/scheduled")
async def api_match_scheduled(request: Request):
    user = await get_user_from_request(request)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT m.id, m.scheduled_at, m.location,
                      (SELECT count(*) FROM match_registrations WHERE match_id=m.id) as reg_count,
                      array_agg(r.user_id) FILTER (WHERE r.user_id IS NOT NULL) as reg_ids
               FROM matches m
               LEFT JOIN match_registrations r ON r.match_id = m.id
               WHERE m.status='scheduled'
               GROUP BY m.id, m.scheduled_at, m.location
               ORDER BY m.scheduled_at ASC"""
        )
    return [
        {
            "match_id": str(r["id"]),
            "scheduled_at": r["scheduled_at"].isoformat() if r["scheduled_at"] else None,
            "location": r["location"],
            "reg_count": r["reg_count"],
            "reg_ids": [str(x) for x in (r["reg_ids"] or [])],
        }
        for r in rows
    ]

@app.post("/api/match/register_intent")
async def api_register_intent(req: RegisterIntentReq, request: Request):
    user = await get_user_from_request(request)
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM matches WHERE id=$1", req.match_id)
        if not row:
            raise HTTPException(404, "match not found")
        if row["status"] != "scheduled":
            raise HTTPException(400, "match not in scheduled state")
        await conn.execute(
            """INSERT INTO match_registrations (match_id, user_id)
               VALUES ($1,$2) ON CONFLICT DO NOTHING""",
            req.match_id, user["id"]
        )
    return {"ok": True}

@app.get("/api/achievements")
async def api_achievements(request: Request):
    user = await get_user_from_request(request)
    return {"achievements": ACHIEVEMENTS}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
