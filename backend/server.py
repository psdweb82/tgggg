import os
import re
import json
import hmac
import time
import uuid
import asyncio
import hashlib
import logging
from pathlib import Path
from urllib.parse import parse_qsl
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any, Tuple

import httpx
import jwt
from bson import ObjectId
from bson.binary import Binary
from dotenv import load_dotenv
from fastapi import FastAPI, APIRouter, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import StreamingResponse, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from key_manager import GeminiKeyManager, classify_error, parse_retry_after

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("ai_workspace")

# ------------------------------------------------------------------ config
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]

# GEMINI_API_KEY может быть одним ключом или несколькими через запятую:
#   GEMINI_API_KEY="AQ.key1,AQ.key2,AQ.key3"
# Сервер ротирует их по кругу и при 429 автоматически переключается на следующий.
_GEMINI_RAW = os.environ.get("GEMINI_API_KEY", "")
GEMINI_API_KEYS: List[str] = [k.strip() for k in _GEMINI_RAW.split(",") if k.strip()]
GEMINI_API_KEY = GEMINI_API_KEYS[0] if GEMINI_API_KEYS else ""  # оставлено для совместимости

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_BOT_USERNAME = os.environ.get("TELEGRAM_BOT_USERNAME", "")
JWT_SECRET = os.environ.get("JWT_SECRET", "change-me-in-prod")
JWT_ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_HOURS = int(os.environ.get("JWT_EXPIRE_HOURS", "168"))
DEV_LOGIN_SECRET = os.environ.get("DEV_LOGIN_SECRET", "")
ENVIRONMENT = os.environ.get("ENVIRONMENT", "development")

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# ---------------- Gemini call-safety knobs ----------------
# Индивидуальный лимит RPM на КАЖДЫЙ ключ (free-tier ≈ 15 RPM). Глобального троттлинга нет —
# нагрузка распределяется round-robin между всеми ключами через KeyManager.
GEMINI_RPM_PER_KEY = int(os.environ.get("GEMINI_RPM_PER_KEY", "15"))
# Максимум секунд, которые сервер готов сам подождать доступности ключа перед retry.
GEMINI_MAX_WAIT = float(os.environ.get("GEMINI_MAX_WAIT", "8.0"))
# Максимум попыток (перебор ключей + короткие ожидания) на один запрос.
GEMINI_MAX_ATTEMPTS = int(os.environ.get("GEMINI_MAX_ATTEMPTS", "8"))
# Минимальный кулдаун ключа при 429 без Retry-After (сек).
GEMINI_MIN_COOLDOWN = float(os.environ.get("GEMINI_MIN_COOLDOWN", "30.0"))

# Единый менеджер жизненного цикла ключей. Никакая другая часть кода не трогает
# состояние ключей напрямую — только через методы key_manager.
key_manager = GeminiKeyManager(
    GEMINI_API_KEYS,
    rpm_per_key=GEMINI_RPM_PER_KEY,
    min_cooldown=GEMINI_MIN_COOLDOWN,
)


class NoHealthyKeys(Exception):
    """Нет доступных рабочих ключей / невосстановимая ошибка запроса Gemini."""

# Only Flash-Lite is currently available on the free tier — expose it as "Gemini".
AVAILABLE_MODELS = [
    {"id": "gemini-2.5-flash-lite", "name": "Gemini", "desc": "Быстрая универсальная модель.", "badge": "BETA"},
]
MODEL_IDS = {m["id"] for m in AVAILABLE_MODELS}
DEFAULT_MODEL = AVAILABLE_MODELS[0]["id"]

SYSTEM_PROMPT = ("You are AI Workspace, a helpful, precise multilingual assistant. "
                 "Answer in the user's language. Use Markdown and fenced code blocks when helpful. "
                 "If images are attached, analyze them and answer questions about them.")

# ------------------------------------------------------------------ storage-optimisation constants
MAX_IMAGES_PER_MESSAGE = 3
MAX_IMAGE_BYTES = 20 * 1024 * 1024                  # 20 MB per file
ALLOWED_IMAGE_MIMES = {
    "image/png", "image/jpeg", "image/jpg", "image/webp",
    "image/gif", "image/heic", "image/heif",
}
IMAGE_TTL_SECONDS = 12 * 60 * 60                    # 12 hours
CONV_INACTIVE_TTL_SECONDS = 7 * 24 * 60 * 60        # 7 days
CHAT_HEAVY_THRESHOLD_BYTES = 150 * 1024 * 1024      # 150 MB
NORMAL_MAX_CHATS = 5
# Summarise old messages once a chat exceeds this many messages, keeping the tail short.
MESSAGES_SUMMARY_TRIGGER = 40
MESSAGES_KEEP_AFTER_SUMMARY = 30
COOLDOWN_SECONDS = 3
LAST_REQUEST: Dict[int, float] = {}

# ---------------- Anti-abuse (защита от спама и dodos) ----------------
# Максимальная длина одного пользовательского сообщения (символов).
MAX_INPUT_CHARS = int(os.environ.get("MAX_INPUT_CHARS", "500"))
# Пер-юзер лимиты (скользящее окно 60 мин / 24 ч по tg_id, хранятся в Mongo).
FREE_HOURLY_LIMIT = int(os.environ.get("FREE_HOURLY_LIMIT", "20"))
FREE_DAILY_LIMIT = int(os.environ.get("FREE_DAILY_LIMIT", "80"))
LUXURY_HOURLY_LIMIT = int(os.environ.get("LUXURY_HOURLY_LIMIT", "60"))
LUXURY_DAILY_LIMIT = int(os.environ.get("LUXURY_DAILY_LIMIT", "500"))
WINDOW_HOUR_SEC = 3600
WINDOW_DAY_SEC = 86400
# Не более одного активного стрима на юзера одновременно (защита от «10 вкладок»).
_active_generations: set[int] = set()
# Флаг мягкого завершения: при shutdown/деплое новые генерации прерываются аккуратно.
_shutting_down: bool = False

# ---------------- Admins & Premium ----------------
# Админы задаются в .env одним или двумя списками (через запятую).
# ADMIN_TG_USERNAMES: удобно, но юзер может сменить @username → менее безопасно.
# ADMIN_TG_IDS: строго по числовому tg_id → безопаснее, рекомендуется.
ADMIN_TG_USERNAMES = {
    u.strip().lstrip("@").lower()
    for u in os.environ.get("ADMIN_TG_USERNAMES", "").split(",") if u.strip()
}
ADMIN_TG_IDS = {
    int(x.strip()) for x in os.environ.get("ADMIN_TG_IDS", "").split(",") if x.strip().isdigit()
}


def _is_admin(user_doc: Optional[Dict[str, Any]]) -> bool:
    if not user_doc:
        return False
    if user_doc.get("tg_id") in ADMIN_TG_IDS:
        return True
    uname = (user_doc.get("username") or "").lstrip("@").lower()
    return bool(uname and uname in ADMIN_TG_USERNAMES)


def _premium_active(user_doc: Optional[Dict[str, Any]]) -> bool:
    """Действующая платная подписка (Luxury), НЕ учитывая админов."""
    if not user_doc or not user_doc.get("is_premium"):
        return False
    until = user_doc.get("premium_until")
    if until is None:
        return True  # бессрочный
    if isinstance(until, str):
        try:
            until = datetime.fromisoformat(until)
        except ValueError:
            return False
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return until > now_utc()


def _is_premium(user_doc: Optional[Dict[str, Any]]) -> bool:
    """Luxury-подписка ИЛИ админ (создатель). Оставлено для отображения бейджа."""
    if not user_doc:
        return False
    return _is_admin(user_doc) or _premium_active(user_doc)


def _user_tier(user_doc: Optional[Dict[str, Any]]) -> str:
    """creator (админ, без лимитов) | luxury (60/ч) | free (20/ч)."""
    if _is_admin(user_doc):
        return "creator"
    if _premium_active(user_doc):
        return "luxury"
    return "free"


def _tier_limits(user_doc: Optional[Dict[str, Any]]) -> Tuple[str, Optional[int], Optional[int]]:
    """(tier, hourly_limit, daily_limit). None лимит = без ограничений (создатель)."""
    tier = _user_tier(user_doc)
    if tier == "creator":
        return tier, None, None
    if tier == "luxury":
        return tier, LUXURY_HOURLY_LIMIT, LUXURY_DAILY_LIMIT
    return tier, FREE_HOURLY_LIMIT, FREE_DAILY_LIMIT

client = AsyncIOMotorClient(MONGO_URL)
db = client[DB_NAME]

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="AI Workspace API")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

api = APIRouter(prefix="/api")
bearer = HTTPBearer(auto_error=True)


# ------------------------------------------------------------------ helpers
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _human_mb(n: int) -> str:
    return f"{n / (1024 * 1024):.1f} МБ"


def create_token(tg_id: int) -> str:
    payload = {
        "sub": str(tg_id), "tg_id": tg_id,
        "iat": int(now_utc().timestamp()),
        "exp": int((now_utc() + timedelta(hours=JWT_EXPIRE_HOURS)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


async def get_current_tg_id(creds: HTTPAuthorizationCredentials = Depends(bearer)) -> int:
    try:
        payload = jwt.decode(creds.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Сессия истекла, войдите снова.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Недействительный токен.")
    tg_id = payload.get("tg_id")
    if not isinstance(tg_id, int):
        raise HTTPException(status_code=401, detail="Некорректный токен.")
    return tg_id


async def require_admin(tg_id: int = Depends(get_current_tg_id)) -> Dict[str, Any]:
    """Dep для админ-эндпоинтов. Возвращает user doc или 403."""
    user = await db.users.find_one({"tg_id": tg_id})
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Доступ запрещён.")
    return user


def verify_telegram_hash(data: Dict[str, Any]) -> bool:
    received = data.get("hash")
    if not received or not TELEGRAM_BOT_TOKEN:
        return False
    pairs = [f"{k}={v}" for k, v in sorted(data.items()) if k != "hash" and v is not None]
    check_string = "\n".join(pairs)
    secret = hashlib.sha256(TELEGRAM_BOT_TOKEN.encode()).digest()
    calc = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(calc, received)


def verify_webapp_init_data(init_data: str) -> Optional[Dict[str, Any]]:
    if not init_data or not TELEGRAM_BOT_TOKEN:
        return None
    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    except Exception:
        return None
    received = parsed.pop("hash", None)
    if not received:
        return None
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret = hmac.new(b"WebAppData", TELEGRAM_BOT_TOKEN.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received):
        return None
    try:
        if now_utc().timestamp() - int(parsed.get("auth_date", "0")) > 86400:
            return None
    except ValueError:
        return None
    user_raw = parsed.get("user")
    if not user_raw:
        return None
    try:
        return json.loads(user_raw)
    except json.JSONDecodeError:
        return None


async def upsert_user(tg_id: int, first_name: str, last_name: Optional[str],
                      username: Optional[str], photo_url: Optional[str]) -> Dict[str, Any]:
    now = now_utc()
    profile = {"first_name": first_name, "last_name": last_name,
               "username": username, "photo_url": photo_url, "last_login_at": now}
    await db.users.update_one(
        {"tg_id": tg_id},
        {"$set": profile, "$setOnInsert": {"tg_id": tg_id, "created_at": now}},
        upsert=True,
    )
    return {"tg_id": tg_id, **profile}


# ------------------------------------------------------------------ schemas
class TelegramAuth(BaseModel):
    id: int
    first_name: str
    last_name: Optional[str] = None
    username: Optional[str] = None
    photo_url: Optional[str] = None
    auth_date: int
    hash: str


class WebAppAuth(BaseModel):
    init_data: str = Field(..., min_length=1, max_length=8000)


class DevLogin(BaseModel):
    tg_id: int = Field(..., gt=0)
    first_name: str = Field("Тестовый пользователь", max_length=64)
    username: Optional[str] = Field(None, max_length=64)
    dev_secret: str


class RenameConversation(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)


class ImageRef(BaseModel):
    id: str
    mime: Optional[str] = None


class ChatRequest(BaseModel):
    conversation_id: Optional[str] = None
    model: str = DEFAULT_MODEL
    content: str = Field("", max_length=16000)
    image_ids: List[str] = Field(default_factory=list, max_length=MAX_IMAGES_PER_MESSAGE)

    @field_validator("model")
    @classmethod
    def _model_ok(cls, v):
        if v not in MODEL_IDS:
            raise ValueError("Неизвестная модель")
        return v


def user_out(u: Dict[str, Any]) -> Dict[str, Any]:
    premium_until = u.get("premium_until")
    if isinstance(premium_until, datetime):
        premium_until = premium_until.isoformat()
    return {"tg_id": u["tg_id"], "first_name": u.get("first_name"),
            "last_name": u.get("last_name"), "username": u.get("username"),
            "photo_url": u.get("photo_url"),
            "is_admin": _is_admin(u),
            "is_premium": _is_premium(u),
            "tier": _user_tier(u),
            "premium_until": premium_until}


def iso(v: Any) -> Optional[str]:
    if isinstance(v, datetime):
        return v.isoformat()
    return v  # already str or None


def conv_out(c: Dict[str, Any]) -> Dict[str, Any]:
    size = int(c.get("size_bytes", 0))
    return {
        "id": str(c["_id"]),
        "title": c.get("title", "Новый чат"),
        "model": c.get("model"),
        "size_bytes": size,
        "message_count": int(c.get("message_count", 0)),
        "locked": size >= CHAT_HEAVY_THRESHOLD_BYTES,
        "has_summary": bool(c.get("last_summary")),
        "created_at": iso(c.get("created_at")),
        "updated_at": iso(c.get("updated_at")),
    }


def _msg_out(m: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "role": m.get("role"),
        "content": m.get("content", ""),
        "images": [{"id": img["id"], "mime": img.get("mime", "image/png"),
                    "url": f"/api/images/{img['id']}"} for img in m.get("images", [])],
        "model": m.get("model"),
        "created_at": iso(m.get("created_at")),
    }


# ------------------------------------------------------------------ auth routes
@api.post("/auth/telegram")
@limiter.limit("20/minute")
async def telegram_login(payload: TelegramAuth, request: Request):
    data = payload.model_dump(exclude_none=True)
    if not verify_telegram_hash(data):
        raise HTTPException(status_code=401, detail="Проверка подписи Telegram не прошла.")
    if now_utc().timestamp() - payload.auth_date > 86400:
        raise HTTPException(status_code=401, detail="Данные Telegram устарели, войдите снова.")
    user = await upsert_user(payload.id, payload.first_name, payload.last_name,
                             payload.username, payload.photo_url)
    return {"access_token": create_token(payload.id), "user": user_out(user)}


@api.post("/auth/telegram-webapp")
@limiter.limit("30/minute")
async def telegram_webapp_login(payload: WebAppAuth, request: Request):
    u = verify_webapp_init_data(payload.init_data)
    if not u or "id" not in u:
        raise HTTPException(status_code=401, detail="Проверка Telegram Mini App не прошла.")
    user = await upsert_user(int(u["id"]), u.get("first_name", "User"), u.get("last_name"),
                             u.get("username"), u.get("photo_url"))
    return {"access_token": create_token(int(u["id"])), "user": user_out(user)}


@api.post("/auth/dev-login")
@limiter.limit("20/minute")
async def dev_login(payload: DevLogin, request: Request):
    if ENVIRONMENT == "production":
        raise HTTPException(status_code=403, detail="Тестовый вход отключён в production.")
    if not DEV_LOGIN_SECRET or payload.dev_secret != DEV_LOGIN_SECRET:
        raise HTTPException(status_code=401, detail="Неверный секрет тестового входа.")
    user = await upsert_user(payload.tg_id, payload.first_name, None, payload.username, None)
    return {"access_token": create_token(payload.tg_id), "user": user_out(user)}


@api.get("/auth/me")
async def me(tg_id: int = Depends(get_current_tg_id)):
    u = await db.users.find_one({"tg_id": tg_id})
    if not u:
        raise HTTPException(status_code=404, detail="Пользователь не найден.")
    return user_out(u)


@api.get("/config")
async def public_config():
    return {"telegram_bot_username": TELEGRAM_BOT_USERNAME,
            "dev_login_enabled": ENVIRONMENT != "production" and bool(DEV_LOGIN_SECRET),
            "limits": {
                "max_images_per_message": MAX_IMAGES_PER_MESSAGE,
                "max_image_mb": MAX_IMAGE_BYTES // (1024 * 1024),
                "max_chats": NORMAL_MAX_CHATS,
                "heavy_chat_mb": CHAT_HEAVY_THRESHOLD_BYTES // (1024 * 1024),
                "image_ttl_hours": IMAGE_TTL_SECONDS // 3600,
                "chat_inactive_ttl_days": CONV_INACTIVE_TTL_SECONDS // 86400,
                "max_input_chars": MAX_INPUT_CHARS,
                "free_hourly": FREE_HOURLY_LIMIT,
                "free_daily": FREE_DAILY_LIMIT,
                "luxury_hourly": LUXURY_HOURLY_LIMIT,
                "luxury_daily": LUXURY_DAILY_LIMIT,
            }}


@api.get("/models")
async def models():
    return {"models": AVAILABLE_MODELS}


# ------------------------------------------------------------------ image storage (TTL 12h)
@api.post("/images")
@limiter.limit("30/minute")
async def upload_image(request: Request, file: UploadFile = File(...),
                       tg_id: int = Depends(get_current_tg_id)):
    mime = (file.content_type or "").lower()
    if not mime.startswith("image/") or mime not in ALLOWED_IMAGE_MIMES:
        raise HTTPException(status_code=415, detail="Можно загружать только изображения (png / jpg / webp / gif / heic).")
    data = await file.read()
    size = len(data)
    if size == 0:
        raise HTTPException(status_code=422, detail="Пустой файл.")
    if size > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail=f"Файл слишком большой ({_human_mb(size)}). Максимум {MAX_IMAGE_BYTES // (1024*1024)} МБ.")
    image_id = uuid.uuid4().hex
    now = now_utc()
    await db.chat_images.insert_one({
        "_id": image_id,
        "tg_id": tg_id,
        "mime": mime,
        "size": size,
        "data": Binary(data),
        "created_at": now,
        "expire_at": now + timedelta(seconds=IMAGE_TTL_SECONDS),
    })
    return {"id": image_id, "mime": mime, "size": size, "url": f"/api/images/{image_id}",
            "expires_in_seconds": IMAGE_TTL_SECONDS}


@api.get("/images/{image_id}")
async def get_image(image_id: str):
    if not re.fullmatch(r"[0-9a-f]{32}", image_id):
        raise HTTPException(status_code=400, detail="Некорректный id.")
    doc = await db.chat_images.find_one({"_id": image_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Изображение уже удалено (срок хранения 12 часов).")
    return Response(content=bytes(doc["data"]), media_type=doc.get("mime", "application/octet-stream"),
                    headers={"Cache-Control": "public, max-age=3600"})


# ------------------------------------------------------------------ conversations
async def _owned_conversation(cid: str, tg_id: int) -> Dict[str, Any]:
    try:
        oid = ObjectId(cid)
    except Exception:
        raise HTTPException(status_code=400, detail="Некорректный id чата.")
    conv = await db.conversations.find_one({"_id": oid, "tg_id": tg_id})
    if not conv:
        raise HTTPException(status_code=404, detail="Чат не найден.")
    return conv


async def _check_can_create_chat(tg_id: int) -> None:
    """Enforce: max 5 chats normally; if any chat >= 150MB the user is locked to that 1 heavy chat."""
    heavy = await db.conversations.find_one(
        {"tg_id": tg_id, "size_bytes": {"$gte": CHAT_HEAVY_THRESHOLD_BYTES}},
        projection={"title": 1, "size_bytes": 1},
    )
    if heavy:
        raise HTTPException(
            status_code=413,
            detail=(f"Чат «{heavy.get('title', 'без названия')}» занимает "
                    f"{_human_mb(int(heavy.get('size_bytes', 0)))} — это больше лимита в 150 МБ. "
                    "Скопируйте всё нужное и удалите этот чат, чтобы можно было создать новый. "
                    "После удаления лимит вернётся к 5 чатам."),
        )
    count = await db.conversations.count_documents({"tg_id": tg_id})
    if count >= NORMAL_MAX_CHATS:
        raise HTTPException(
            status_code=429,
            detail=f"Достигнут лимит в {NORMAL_MAX_CHATS} чатов. Удалите один, чтобы создать новый.",
        )


@api.get("/conversations")
async def list_conversations(tg_id: int = Depends(get_current_tg_id)):
    cur = db.conversations.find({"tg_id": tg_id},
                                projection={"messages": 0}).sort("updated_at", -1).limit(200)
    return {"conversations": [conv_out(c) async for c in cur]}


@api.get("/conversations/{cid}")
async def get_conversation(cid: str, tg_id: int = Depends(get_current_tg_id)):
    conv = await _owned_conversation(cid, tg_id)
    return {
        "conversation": conv_out(conv),
        "messages": [_msg_out(m) for m in conv.get("messages", [])],
    }


@api.patch("/conversations/{cid}")
async def rename_conversation(cid: str, body: RenameConversation, tg_id: int = Depends(get_current_tg_id)):
    await _owned_conversation(cid, tg_id)
    await db.conversations.update_one({"_id": ObjectId(cid)},
                                      {"$set": {"title": body.title, "updated_at": now_utc()}})
    return {"ok": True}


@api.delete("/conversations/{cid}")
async def delete_conversation(cid: str, tg_id: int = Depends(get_current_tg_id)):
    conv = await _owned_conversation(cid, tg_id)
    # cascade-delete referenced images
    image_ids = [img["id"] for m in conv.get("messages", []) for img in m.get("images", [])]
    if image_ids:
        await db.chat_images.delete_many({"_id": {"$in": image_ids}, "tg_id": tg_id})
    await db.conversations.delete_one({"_id": ObjectId(cid)})
    return {"ok": True}


# ------------------------------------------------------------------ gemini streaming
# Дружелюбные сообщения для ПОЛЬЗОВАТЕЛЯ — без упоминания Gemini, квот, ключей, кодов.
# Настоящая причина сохраняется в логах, KeyManager.last_reason и админ-статистике.
_USER_MSG = {
    "cooldown": "Сервис временно перегружен. Попробуйте ещё раз через несколько секунд.",
    "daily_limit": "Сервис временно недоступен. Повторите попытку позже.",
    "invalid": "Произошла временная ошибка. Попробуйте снова.",
    "forbidden": "Произошла временная ошибка. Попробуйте снова.",
    "broken": "Произошла временная ошибка. Попробуйте снова.",
    "request": "Не удалось обработать запрос. Переформулируйте и попробуйте снова.",
    "overloaded": "Сервис временно перегружен. Попробуйте ещё раз через несколько секунд.",
    "network": "Произошла временная ошибка. Попробуйте снова.",
}


def _user_message(kind: str) -> str:
    """Возвращает безопасное сообщение для пользователя по типу ошибки."""
    return _USER_MSG.get(kind, _USER_MSG["broken"])


def _real_reason(status_code: int, detail: str) -> str:
    """Полная техническая причина для админа/логов (HTTP-код + текст Gemini, обрезанный)."""
    snippet = " ".join((detail or "").split())[:200]
    return f"HTTP {status_code}: {snippet}" if snippet else f"HTTP {status_code}"


def _msg_parts_for_gemini(m: Dict[str, Any],
                          image_blobs: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build 'parts' array for a stored message when talking to Gemini."""
    import base64 as _b64
    parts: List[Dict[str, Any]] = []
    for img in m.get("images", []):
        blob = image_blobs.get(img["id"])
        if blob:
            parts.append({"inline_data": {
                "mime_type": blob["mime"],
                "data": _b64.b64encode(bytes(blob["data"])).decode("ascii"),
            }})
    if m.get("content"):
        parts.append({"text": m["content"]})
    return parts or [{"text": ""}]


async def gemini_stream(model: str, history: List[Dict[str, Any]]):
    """
    Стримит ответ Gemini через KeyManager:
      • Round-Robin выбор ключа с индивидуальным per-key RPM (без глобального троттлинга);
      • Failover: при отказе ключа автоматически берётся следующий рабочий;
      • Корректная классификация ошибок (429/RESOURCE_EXHAUSTED/401/403/5xx);
      • Если все ключи заняты ненадолго — короткое ожидание; иначе — NoHealthyKeys (→503/error).
    """
    if not GEMINI_API_KEYS:
        raise NoHealthyKeys("GEMINI_API_KEY не настроен на сервере.")

    url = f"{GEMINI_BASE}/{model}:streamGenerateContent?alt=sse"
    body = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": history,
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 8192},
    }

    last_err: Optional[str] = None
    used_key_count = 0

    for attempt in range(1, GEMINI_MAX_ATTEMPTS + 1):
        key, key_idx, wait = key_manager.acquire()

        if key is None:
            # Нет доступных ключей прямо сейчас.
            if wait is not None and wait <= GEMINI_MAX_WAIT and attempt < GEMINI_MAX_ATTEMPTS:
                logger.info("No key available, waiting %.1fs (attempt %s)", wait, attempt)
                await asyncio.sleep(wait + 0.2)
                continue
            key_manager.record_503()
            raise NoHealthyKeys(_user_message("overloaded"))

        # Считаем переключение между ключами (failover) в рамках одного запроса.
        used_key_count += 1
        if used_key_count > 1:
            key_manager.record_switch()

        headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(180.0)) as hc:
                async with hc.stream("POST", url, headers=headers, json=body) as resp:
                    if resp.status_code == 200:
                        key_manager.report_success(key_idx)
                        first_token = True
                        async for line in resp.aiter_lines():
                            if not line or not line.startswith("data:"):
                                continue
                            chunk = line[5:].strip()
                            if chunk == "[DONE]":
                                break
                            try:
                                obj = json.loads(chunk)
                            except json.JSONDecodeError:
                                continue
                            for cand in obj.get("candidates", []):
                                for part in cand.get("content", {}).get("parts", []):
                                    if part.get("text"):
                                        if first_token:
                                            # Время до первого токена — репрезентативная латентность Gemini.
                                            key_manager.record_success_request(time.monotonic() - t0)
                                            first_token = False
                                        yield part["text"]
                        if first_token:  # успех без текста — всё равно считаем запрос
                            key_manager.record_success_request(time.monotonic() - t0)
                        return  # успех

                    # не-200: читаем тело и решаем через KeyManager
                    detail = (await resp.aread()).decode("utf-8", "ignore")[:800]
                    kind = classify_error(resp.status_code, detail)
                    last_err = _user_message(kind)                # для ПОЛЬЗОВАТЕЛЯ — дружелюбно
                    reason = _real_reason(resp.status_code, detail)  # для АДМИНА/логов — реальная причина
                    logger.error("Gemini error %s (key #%s, kind=%s, attempt %s/%s): %s",
                                 resp.status_code, key_idx, kind, attempt, GEMINI_MAX_ATTEMPTS, detail[:300])

                    if kind == "daily_limit":
                        key_manager.report_daily_limit(key_idx, reason)
                        continue  # failover на следующий ключ
                    if kind == "cooldown":
                        key_manager.report_rate_limit(
                            key_idx, parse_retry_after(detail, dict(resp.headers)), reason)
                        continue
                    if kind == "invalid":
                        key_manager.report_invalid(key_idx, reason)
                        continue
                    if kind == "forbidden":
                        key_manager.report_forbidden(key_idx, reason)
                        continue
                    if kind == "broken":
                        key_manager.report_server_error(key_idx, attempt, reason)
                        continue
                    # прочие 4xx (safety / валидация) — не проблема ключа, не ретраим
                    raise NoHealthyKeys(last_err)
        except httpx.HTTPError as e:
            logger.warning("Gemini network error (key #%s, attempt %s): %s", key_idx, attempt, e)
            key_manager.report_network_error(key_idx, f"network error: {e}")
            last_err = _user_message("network")
            continue

    key_manager.record_503()
    raise NoHealthyKeys(last_err or _user_message("broken"))


async def _probe_keys_on_startup() -> None:
    """Проверяет каждый ключ сразу после старта (в фоне), чтобы битые ключи
    были видны в логах немедленно, а не спустя часы."""
    if not GEMINI_API_KEYS:
        logger.warning("No Gemini keys configured — nothing to probe.")
        return
    logger.info("Probing %s Gemini key(s) on startup...", len(GEMINI_API_KEYS))

    async def _probe(i: int, k: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=15.0) as hc:
                r = await hc.get(GEMINI_BASE, headers={"x-goog-api-key": k},
                                 params={"pageSize": 1})
            if r.status_code == 200:
                key_manager.report_success(i)
                return
            kind = classify_error(r.status_code, r.text)
            if kind == "invalid":
                key_manager.report_invalid(i, "invalid at startup probe (401)")
            elif kind == "forbidden":
                key_manager.report_forbidden(i, "forbidden at startup probe (403)")
            elif kind == "daily_limit":
                key_manager.report_daily_limit(i, "daily quota at startup")
            elif kind == "cooldown":
                key_manager.report_rate_limit(i, parse_retry_after(r.text, dict(r.headers)),
                                              "rate-limited at startup")
            elif kind == "broken":
                key_manager.report_server_error(i, reason="5xx at startup probe")
        except Exception as e:  # noqa: BLE001
            logger.warning("Key #%s startup probe network error: %s", i, e)

    await asyncio.gather(*(_probe(i, k) for i, k in enumerate(GEMINI_API_KEYS)))
    h = key_manager.health()
    logger.info("Loaded keys: %s | Healthy: %s | Cooldown: %s | Daily Limit: %s | "
                "Invalid: %s | Forbidden: %s | Broken: %s",
                h["total_keys"], h["healthy"], h["cooldown"], h["daily_limit"],
                h["invalid"], h["forbidden"], h["broken"])


# ------------------------------------------------------------------ per-user quotas / anti-abuse
def _aware(dt: datetime) -> datetime:
    """Гарантирует tz-aware UTC datetime (Mongo возвращает naive)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


async def _usage_snapshot(tg_id: int, user_doc: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Считает использование по СКОЛЬЗЯЩИМ окнам (60 мин / 24 ч) на основе абсолютных
    timestamps в Mongo. Возвращает состояние для UI и проверок лимитов.
    Всё считается по времени → устойчиво к перезапуску/усыплению backend.
    """
    tier, hourly, daily = _tier_limits(user_doc)
    if hourly is None:  # создатель — без ограничений
        return {"tier": tier, "unlimited": True,
                "hourly_limit": None, "hourly_used": 0, "hourly_remaining": None,
                "daily_limit": None, "daily_used": 0, "daily_remaining": None,
                "blocked": False, "retry_at": None, "reason": None}

    now = now_utc()
    hour_ago = now - timedelta(seconds=WINDOW_HOUR_SEC)
    day_ago = now - timedelta(seconds=WINDOW_DAY_SEC)
    coll = db.user_requests
    hour_count = await coll.count_documents({"tg_id": tg_id, "ts": {"$gte": hour_ago}})
    day_count = await coll.count_documents({"tg_id": tg_id, "ts": {"$gte": day_ago}})

    blocked = False
    retry_at: Optional[datetime] = None
    reason: Optional[str] = None

    if hour_count >= hourly:
        oldest = await coll.find_one({"tg_id": tg_id, "ts": {"$gte": hour_ago}}, sort=[("ts", 1)])
        if oldest:
            retry_at = _aware(oldest["ts"]) + timedelta(seconds=WINDOW_HOUR_SEC)
            blocked, reason = True, "hour"
    if day_count >= daily:
        oldest = await coll.find_one({"tg_id": tg_id, "ts": {"$gte": day_ago}}, sort=[("ts", 1)])
        day_retry = (_aware(oldest["ts"]) + timedelta(seconds=WINDOW_DAY_SEC)) if oldest else now
        if not retry_at or day_retry > retry_at:
            retry_at = day_retry
        blocked = True
        reason = reason or "day"

    return {
        "tier": tier, "unlimited": False,
        "hourly_limit": hourly, "hourly_used": hour_count,
        "hourly_remaining": max(0, hourly - hour_count),
        "daily_limit": daily, "daily_used": day_count,
        "daily_remaining": max(0, daily - day_count),
        "blocked": blocked,
        "retry_at": retry_at.isoformat() if retry_at else None,
        "reason": reason,
    }


async def _check_and_record_usage(tg_id: int, user_doc: Optional[Dict[str, Any]]) -> None:
    """
    Проверяет скользящие лимиты и, если ок, записывает timestamp запроса.
    Создатель (админ) — без ограничений. Free: 20/60мин, Luxury: 60/60мин (+ дневные).
    При превышении бросает 429 с retry_at (абсолютное время следующего слота).
    """
    tier, hourly, daily = _tier_limits(user_doc)
    if hourly is None:
        return  # создатель — безлимит

    snap = await _usage_snapshot(tg_id, user_doc)
    if snap["blocked"]:
        if snap["reason"] == "day":
            msg = f"Дневной лимит ({daily} сообщений) исчерпан. Попробуйте позже."
        else:
            msg = f"Лимит {hourly} сообщений за 60 минут исчерпан. Подождите таймер."
        raise HTTPException(status_code=429, detail=msg)

    await db.user_requests.insert_one({"tg_id": tg_id, "ts": now_utc()})


async def _maybe_summarise(cid: str) -> None:
    """When a chat exceeds MESSAGES_SUMMARY_TRIGGER messages, ask the model to
    compress everything except the last MESSAGES_KEEP_AFTER_SUMMARY into
    `last_summary`, then drop the older tail. Saves storage & keeps context small."""
    conv = await db.conversations.find_one({"_id": ObjectId(cid)})
    if not conv:
        return
    msgs = conv.get("messages", [])
    if len(msgs) < MESSAGES_SUMMARY_TRIGGER:
        return
    to_summarise = msgs[:-MESSAGES_KEEP_AFTER_SUMMARY]
    tail = msgs[-MESSAGES_KEEP_AFTER_SUMMARY:]
    if not to_summarise:
        return
    previous_summary = conv.get("last_summary") or ""
    body = "\n\n".join(f"{'USER' if m.get('role') == 'user' else 'ASSISTANT'}: {m.get('content', '')}"
                       for m in to_summarise if m.get("content"))
    prompt = ("Ниже — начало длинного диалога, который нужно сжать в короткое резюме "
              "на русском (5-10 предложений), сохранив имена, факты, решения и цели. "
              "Верни только текст резюме без вводных фраз.\n\n"
              + (f"[Предыдущее резюме]\n{previous_summary}\n\n" if previous_summary else "")
              + f"[Новые сообщения]\n{body}")
    request = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 1024},
    }
    summary_text = previous_summary
    try:
        key, key_idx, wait = key_manager.acquire()
        if not key:
            # все ключи заняты — просто пропускаем фоновую суммаризацию
            raise RuntimeError("no healthy gemini key for summary")
        async with httpx.AsyncClient(timeout=60.0) as hc:
            r = await hc.post(f"{GEMINI_BASE}/{DEFAULT_MODEL}:generateContent",
                              headers={"x-goog-api-key": key,
                                       "Content-Type": "application/json"},
                              json=request)
            if r.status_code == 200:
                key_manager.report_success(key_idx)
                data = r.json()
                parts = ((data.get("candidates") or [{}])[0]
                         .get("content", {}).get("parts", []))
                summary_text = "".join(p.get("text", "") for p in parts).strip() or previous_summary
            else:
                kind = classify_error(r.status_code, r.text)
                if kind == "daily_limit":
                    key_manager.report_daily_limit(key_idx)
                elif kind == "cooldown":
                    key_manager.report_rate_limit(key_idx, parse_retry_after(r.text, dict(r.headers)))
                elif kind == "invalid":
                    key_manager.report_invalid(key_idx)
                elif kind == "forbidden":
                    key_manager.report_forbidden(key_idx)
                elif kind == "broken":
                    key_manager.report_server_error(key_idx)
                logger.warning("summary call failed: %s %s", r.status_code, r.text[:200])
    except Exception as e:  # noqa: BLE001
        logger.warning("summary error: %s", e)

    # Recompute size_bytes for the trimmed doc
    dropped_image_ids = [img["id"] for m in to_summarise for img in m.get("images", [])]
    new_size = len(summary_text.encode("utf-8")) + sum(
        len(m.get("content", "").encode("utf-8"))
        + sum(0 for _ in m.get("images", []))
        for m in tail
    )
    # Add current image sizes from tail (they may already have expired but that's fine)
    tail_image_ids = [img["id"] for m in tail for img in m.get("images", [])]
    if tail_image_ids:
        cur = db.chat_images.find({"_id": {"$in": tail_image_ids}}, projection={"size": 1})
        async for d in cur:
            new_size += int(d.get("size", 0))
    await db.conversations.update_one(
        {"_id": ObjectId(cid)},
        {"$set": {"messages": tail, "last_summary": summary_text,
                  "size_bytes": new_size, "message_count": len(tail),
                  "updated_at": now_utc()}},
    )
    # Free storage: drop image blobs that were only referenced by the summarised tail.
    if dropped_image_ids:
        await db.chat_images.delete_many({"_id": {"$in": dropped_image_ids}})


@api.post("/chat/stream")
@limiter.limit("30/minute")
async def chat_stream(body: ChatRequest, request: Request, tg_id: int = Depends(get_current_tg_id)):
    if not GEMINI_API_KEYS:
        raise HTTPException(status_code=503, detail="GEMINI_API_KEY не настроен на сервере.")
    if not body.content.strip() and not body.image_ids:
        raise HTTPException(status_code=422, detail="Пустой запрос.")
    if len(body.image_ids) > MAX_IMAGES_PER_MESSAGE:
        raise HTTPException(status_code=422, detail=f"Можно прикрепить максимум {MAX_IMAGES_PER_MESSAGE} изображения.")

    # Anti-abuse: ограничение длины одного сообщения (защита от «мегабайт текста» атак).
    if len(body.content) > MAX_INPUT_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"Сообщение слишком длинное ({len(body.content)} симв.). Максимум {MAX_INPUT_CHARS}.",
        )

    # Anti-abuse: один активный стрим на юзера. Открыл 10 вкладок — работает одна.
    if tg_id in _active_generations:
        raise HTTPException(
            status_code=429,
            detail="Уже обрабатывается предыдущее сообщение. Дождитесь ответа.",
        )

    # Anti-spam cooldown (server-detected) — короткий кулдаун между сообщениями.
    last = LAST_REQUEST.get(tg_id, 0.0)
    wait = COOLDOWN_SECONDS - (now_utc().timestamp() - last)
    if wait > 0:
        raise HTTPException(status_code=429, detail=f"Слишком часто. Подождите {int(wait) + 1} сек.")

    # Предполётная проверка ключей: если рабочих нет и ждать долго — не жжём лимит юзера.
    available_now, key_wait, recoverable = key_manager.peek()
    if not available_now and (not recoverable or key_wait is None or key_wait > GEMINI_MAX_WAIT):
        key_manager.record_503()
        raise HTTPException(status_code=503, detail="Сервис временно перегружен. Попробуйте через минуту.")

    # Per-user скользящие лимиты (Free 20/60мин, Luxury 60/60мин, создатель — без лимита).
    user_doc = await db.users.find_one({"tg_id": tg_id})
    await _check_and_record_usage(tg_id, user_doc)

    LAST_REQUEST[tg_id] = now_utc().timestamp()
    _active_generations.add(tg_id)

    # validate referenced images (must belong to caller and still be alive)
    image_records: List[Dict[str, Any]] = []
    if body.image_ids:
        cur = db.chat_images.find({"_id": {"$in": body.image_ids}, "tg_id": tg_id})
        found = {d["_id"]: d async for d in cur}
        if len(found) != len(body.image_ids):
            raise HTTPException(status_code=410,
                                detail="Одно из изображений уже удалено (срок хранения 12 часов). Загрузите ещё раз.")
        image_records = [found[i] for i in body.image_ids]

    # resolve or create conversation (gating check on create)
    now = now_utc()
    if body.conversation_id:
        conv = await _owned_conversation(body.conversation_id, tg_id)
        cid = body.conversation_id
        # Heavy chats are read/copy/delete-only — cannot receive new messages.
        if int(conv.get("size_bytes", 0)) >= CHAT_HEAVY_THRESHOLD_BYTES:
            raise HTTPException(
                status_code=423,
                detail=(f"Чат «{conv.get('title', 'без названия')}» занимает больше 150 МБ и заблокирован. "
                        "Скопируйте нужное и удалите его, чтобы освободить место."),
            )
    else:
        await _check_can_create_chat(tg_id)
        base = body.content.strip() or "Изображение"
        title = base[:48] + ("…" if len(base) > 48 else "")
        conv_doc = {"tg_id": tg_id, "title": title, "model": body.model,
                    "created_at": now, "updated_at": now,
                    "size_bytes": 0, "message_count": 0,
                    "messages": [], "last_summary": ""}
        res = await db.conversations.insert_one(conv_doc)
        cid = str(res.inserted_id)
        conv = {**conv_doc, "_id": res.inserted_id}

    # push user message
    user_msg = {
        "role": "user",
        "content": body.content,
        "images": [{"id": r["_id"], "mime": r["mime"]} for r in image_records],
        "model": body.model,
        "created_at": now,
    }
    user_size = len(body.content.encode("utf-8")) + sum(int(r.get("size", 0)) for r in image_records)
    await db.conversations.update_one(
        {"_id": ObjectId(cid)},
        {"$push": {"messages": user_msg},
         "$inc": {"size_bytes": user_size, "message_count": 1},
         "$set": {"updated_at": now, "model": body.model}},
    )

    # build history for Gemini (need actual bytes of any user images referenced in-history)
    conv_full = await db.conversations.find_one({"_id": ObjectId(cid)})
    all_msg_image_ids = [img["id"] for m in conv_full.get("messages", []) for img in m.get("images", [])]
    image_blobs: Dict[str, Dict[str, Any]] = {}
    if all_msg_image_ids:
        cur = db.chat_images.find({"_id": {"$in": all_msg_image_ids}})
        image_blobs = {d["_id"]: d async for d in cur}
    history: List[Dict[str, Any]] = []
    if conv_full.get("last_summary"):
        history.append({"role": "user",
                        "parts": [{"text": f"[Краткое резюме предыдущей части разговора]\n{conv_full['last_summary']}"}]})
        history.append({"role": "model", "parts": [{"text": "Принято, продолжаю с учётом этого."}]})
    history.extend({"role": "model" if m["role"] == "assistant" else "user",
                    "parts": _msg_parts_for_gemini(m, image_blobs)}
                   for m in conv_full.get("messages", []))

    async def event_gen():
        try:
            yield f"data: {json.dumps({'type': 'meta', 'conversation_id': cid, 'title': conv.get('title')})}\n\n"
            collected: List[str] = []
            try:
                async for delta in gemini_stream(body.model, history):
                    if _shutting_down:
                        # Мягкое завершение при деплое/рестарте: аккуратно останавливаем поток.
                        yield f"data: {json.dumps({'type': 'error', 'message': 'Сервер перезапускается, повторите запрос через несколько секунд.'})}\n\n"
                        break
                    collected.append(delta)
                    yield f"data: {json.dumps({'type': 'delta', 'text': delta})}\n\n"
            except (NoHealthyKeys, RuntimeError) as e:
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
            except Exception:  # noqa: BLE001
                logger.exception("stream failed")
                yield f"data: {json.dumps({'type': 'error', 'message': 'Ошибка генерации ответа.'})}\n\n"
            full = "".join(collected)
            if full:
                done_at = now_utc()
                asst_msg = {"role": "assistant", "content": full, "images": [],
                            "model": body.model, "created_at": done_at}
                await db.conversations.update_one(
                    {"_id": ObjectId(cid)},
                    {"$push": {"messages": asst_msg},
                     "$inc": {"size_bytes": len(full.encode("utf-8")), "message_count": 1},
                     "$set": {"updated_at": done_at, "model": body.model}},
                )
                # Storage-optimisation: compress old history into a summary once the chat gets long.
                await _maybe_summarise(cid)
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        finally:
            # Всегда освобождаем «слот» юзера, даже если клиент отвалился.
            _active_generations.discard(tg_id)

    return StreamingResponse(event_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})


@api.get("/")
async def root():
    return {"message": "AI Workspace API", "status": "ok"}


def _health_payload() -> Dict[str, Any]:
    h = key_manager.health()
    return {"status": "degraded" if _shutting_down else "ok",
            "shutting_down": _shutting_down,
            "keys": h}


@api.get("/health")
async def api_health():
    """Мониторинг: статус backend + количество ключей по статусам."""
    return _health_payload()


@api.get("/gemini/keys/status")
async def gemini_keys_status(_admin: Dict[str, Any] = Depends(require_admin)):
    """Подробное состояние всех ключей (без раскрытия самих ключей). Только для админа."""
    return key_manager.summary()


@api.get("/usage/status")
async def usage_status(tg_id: int = Depends(get_current_tg_id)):
    """Текущее использование лимитов пользователя (скользящее окно) + время разблокировки."""
    user = await db.users.find_one({"tg_id": tg_id})
    return await _usage_snapshot(tg_id, user)


# ------------------------------------------------------------------ Admin panel
class PremiumGrant(BaseModel):
    tg_id: Optional[int] = None
    username: Optional[str] = Field(None, max_length=64)
    days: Optional[int] = Field(None, ge=1, le=3650)  # None = бессрочно


class PremiumRevoke(BaseModel):
    tg_id: Optional[int] = None
    username: Optional[str] = Field(None, max_length=64)


async def _find_user_by_ref(tg_id: Optional[int], username: Optional[str]) -> Optional[Dict[str, Any]]:
    if tg_id:
        u = await db.users.find_one({"tg_id": tg_id})
        if u:
            return u
    if username:
        uname = username.strip().lstrip("@").lower()
        if uname:
            # username в БД хранится как есть — сравниваем без учёта регистра через regex
            return await db.users.find_one({"username": {"$regex": f"^{re.escape(uname)}$", "$options": "i"}})
    return None


def _admin_user_out(u: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "tg_id": u["tg_id"],
        "first_name": u.get("first_name"),
        "last_name": u.get("last_name"),
        "username": u.get("username"),
        "photo_url": u.get("photo_url"),
        "is_admin": _is_admin(u),
        "is_premium": _is_premium(u),
        "tier": _user_tier(u),
        "premium_until": u["premium_until"].isoformat() if isinstance(u.get("premium_until"), datetime) else u.get("premium_until"),
        "created_at": u["created_at"].isoformat() if isinstance(u.get("created_at"), datetime) else u.get("created_at"),
        "last_login_at": u["last_login_at"].isoformat() if isinstance(u.get("last_login_at"), datetime) else u.get("last_login_at"),
    }


@api.get("/admin/users")
async def admin_users_search(
    query: str = "",
    limit: int = 30,
    _admin: Dict[str, Any] = Depends(require_admin),
):
    """Поиск юзеров: по @username (contains, case-insensitive) или по tg_id (точно)."""
    limit = max(1, min(int(limit or 30), 100))
    q = (query or "").strip().lstrip("@")
    filt: Dict[str, Any] = {}
    if q:
        if q.isdigit():
            filt = {"$or": [{"tg_id": int(q)},
                            {"username": {"$regex": re.escape(q), "$options": "i"}}]}
        else:
            filt = {"$or": [{"username": {"$regex": re.escape(q), "$options": "i"}},
                            {"first_name": {"$regex": re.escape(q), "$options": "i"}}]}
    cur = db.users.find(filt).sort("last_login_at", -1).limit(limit)
    return {"users": [_admin_user_out(u) async for u in cur]}


@api.post("/admin/premium")
async def admin_grant_premium(body: PremiumGrant,
                              _admin: Dict[str, Any] = Depends(require_admin)):
    """Выдать премиум: `days=None` = бессрочно, иначе `now + days`.
    Идентификация юзера по tg_id или @username (регистронезависимо)."""
    if not body.tg_id and not body.username:
        raise HTTPException(status_code=422, detail="Укажите tg_id или username.")
    user = await _find_user_by_ref(body.tg_id, body.username)
    if not user:
        raise HTTPException(status_code=404,
                            detail="Пользователь не найден. Он должен хотя бы раз войти в приложение.")
    until = None if body.days is None else now_utc() + timedelta(days=body.days)
    await db.users.update_one(
        {"tg_id": user["tg_id"]},
        {"$set": {"is_premium": True, "premium_until": until}},
    )
    # Сбрасываем окно использования: после выдачи Luxury доступны все 60 запросов.
    await db.user_requests.delete_many({"tg_id": user["tg_id"]})
    updated = await db.users.find_one({"tg_id": user["tg_id"]})
    logger.info("Admin %s granted premium (days=%s) to tg_id=%s @%s (usage window reset)",
                _admin.get("tg_id"), body.days, user["tg_id"], user.get("username"))
    return _admin_user_out(updated)


@api.delete("/admin/premium")
async def admin_revoke_premium(body: PremiumRevoke,
                               _admin: Dict[str, Any] = Depends(require_admin)):
    """Снять премиум."""
    if not body.tg_id and not body.username:
        raise HTTPException(status_code=422, detail="Укажите tg_id или username.")
    user = await _find_user_by_ref(body.tg_id, body.username)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден.")
    await db.users.update_one(
        {"tg_id": user["tg_id"]},
        {"$set": {"is_premium": False, "premium_until": None}},
    )
    updated = await db.users.find_one({"tg_id": user["tg_id"]})
    logger.info("Admin %s revoked premium from tg_id=%s @%s",
                _admin.get("tg_id"), user["tg_id"], user.get("username"))
    return _admin_user_out(updated)


@api.get("/admin/stats")
async def admin_stats(_admin: Dict[str, Any] = Depends(require_admin)):
    """Быстрая сводка для дашборда."""
    total = await db.users.count_documents({})
    premium = await db.users.count_documents({"is_premium": True})
    now = now_utc()
    active_24h = await db.users.count_documents({"last_login_at": {"$gte": now - timedelta(hours=24)}})
    active_7d = await db.users.count_documents({"last_login_at": {"$gte": now - timedelta(days=7)}})
    keys = key_manager.summary()
    return {
        "total_users": total,
        "premium_users": premium,
        "active_24h": active_24h,
        "active_7d": active_7d,
        "gemini_keys_total": keys["total"],
        "gemini_keys_healthy": keys["healthy"],
        "gemini_keys": keys,
    }


app.include_router(api)


@app.get("/health")
async def health():
    """Root-level health-check для мониторинга (Render и т.п.)."""
    return _health_payload()


# ------------------------------------------------------------------ security middleware
class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        resp.headers["X-XSS-Protection"] = "1; mode=block"
        resp.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        return resp


app.add_middleware(SecurityHeaders)
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup():
    # Users
    await db.users.create_index("tg_id", unique=True)

    # Conversations: listing index + TTL on inactivity (7 days sliding via updated_at)
    await db.conversations.create_index([("tg_id", 1), ("updated_at", -1)])
    await db.conversations.create_index("updated_at", expireAfterSeconds=CONV_INACTIVE_TTL_SECONDS)

    # Images: TTL on absolute expire_at (Mongo removes when now >= expire_at)
    await db.chat_images.create_index("expire_at", expireAfterSeconds=0)
    await db.chat_images.create_index("tg_id")

    # Per-user request timestamps (скользящее окно лимитов): индекс + TTL 25ч.
    await db.user_requests.create_index([("tg_id", 1), ("ts", 1)])
    await db.user_requests.create_index("ts", expireAfterSeconds=25 * 60 * 60)

    # One-time cleanup: legacy split-messages collection is no longer used.
    try:
        await db.messages.drop()
        logger.info("Dropped legacy 'messages' collection")
    except Exception:  # noqa: BLE001
        pass

    # Telegram menu button
    web_app_url = os.environ.get("APP_URL") or os.environ.get("WEB_APP_URL")
    if TELEGRAM_BOT_TOKEN and web_app_url:
        try:
            async with httpx.AsyncClient(timeout=10.0) as hc:
                r = await hc.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setChatMenuButton",
                    json={"menu_button": {"type": "web_app", "text": "Открыть",
                                          "web_app": {"url": web_app_url}}},
                )
                if r.status_code == 200 and r.json().get("ok"):
                    logger.info("Telegram menu button set to 'Открыть' -> %s", web_app_url)
                else:
                    logger.warning("Failed to set Telegram menu button: %s", r.text[:200])
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not set Telegram menu button: %s", e)

    logger.info("AI Workspace API started (env=%s)", ENVIRONMENT)

    # Фоновая проверка всех ключей сразу после старта (не блокирует запуск).
    asyncio.create_task(_probe_keys_on_startup())


@app.on_event("shutdown")
async def _shutdown():
    # Graceful shutdown: перестаём принимать новые генерации и даём активным завершиться.
    global _shutting_down
    _shutting_down = True
    logger.info("Shutdown initiated — waiting for %s active generation(s) to drain...",
                len(_active_generations))
    for _ in range(50):  # максимум ~25 сек ожидания
        if not _active_generations:
            break
        await asyncio.sleep(0.5)
    client.close()
    logger.info("Shutdown complete.")
