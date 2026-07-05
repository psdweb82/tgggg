"""Backend API tests for AI Workspace — storage-optimised iteration.

Covers the review scope:
- GET /api/models (single model gemini-2.5-flash-lite)
- GET /api/config (limits object)
- POST /api/auth/dev-login (dev_secret='wiggas-dev-2026')
- POST /api/images (multipart) — happy path + 415 / 422 / 413 rejections
- GET /api/images/{id} — bytes + correct content-type + 400/404 errors
- POST /api/chat/stream — cross-user image leak (410), >3 images (422),
  first-conversation creation (SSE), 5-chat cap (429), heavy-chat (413/423),
  cooldown 3s (429)
- DELETE /api/conversations/{id} cascades chat_images
- Mongo TTL indexes existence
- GET /api/conversations shape (locked/size_bytes, updated_at desc)
- GET /api/conversations/{id} → images use {id, mime, url}
"""

import json
import os
import struct
import time
import zlib
import requests
import pytest
from datetime import datetime, timezone
from pymongo import MongoClient
from bson import ObjectId

BASE = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
API = f"{BASE}/api"
DEV_SECRET = "wiggas-dev-2026"
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "ai_workspace")

# Unique tg_ids per test scenario to prevent cross-test pollution
TG_BASIC = 1001
TG_CROSS_A = 1002
TG_CROSS_B = 1003
TG_LIMIT = 1004
TG_HEAVY = 1005
TG_COOLDOWN = 1006
TG_CONV_SHAPE = 1007

_mongo = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
_db = _mongo[DB_NAME]


# ---------- helpers ----------
def _h(t):
    return {"Authorization": f"Bearer {t}"}


def _sleep_cooldown():
    time.sleep(3.4)


def _dev_login(tg_id: int, first_name: str = "QA"):
    r = requests.post(
        f"{API}/auth/dev-login",
        json={"tg_id": tg_id, "first_name": first_name, "dev_secret": DEV_SECRET},
        timeout=30,
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _tiny_png_bytes() -> bytes:
    def _chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
    raw = b"\x00" + b"\x00\x00\x00\x00"
    idat = _chunk(b"IDAT", zlib.compress(raw))
    iend = _chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


def _sse_collect(resp, max_seconds: float = 90.0):
    events = []
    start = time.time()
    for raw in resp.iter_lines(decode_unicode=True):
        if time.time() - start > max_seconds:
            break
        if not raw or not raw.startswith("data:"):
            continue
        try:
            events.append(json.loads(raw[5:].strip()))
        except json.JSONDecodeError:
            continue
        if events and events[-1].get("type") == "done":
            break
    return events


def _cleanup_tg(tg_id: int):
    _db.conversations.delete_many({"tg_id": tg_id})
    _db.chat_images.delete_many({"tg_id": tg_id})


@pytest.fixture(scope="session", autouse=True)
def _wipe_test_users():
    ids = [TG_BASIC, TG_CROSS_A, TG_CROSS_B, TG_LIMIT, TG_HEAVY, TG_COOLDOWN, TG_CONV_SHAPE]
    for tg in ids:
        _cleanup_tg(tg)
    yield
    for tg in ids:
        _cleanup_tg(tg)


# =============================== 1. models
def test_models_single_gemini_flash_lite():
    r = requests.get(f"{API}/models", timeout=30)
    assert r.status_code == 200
    models = r.json()["models"]
    assert isinstance(models, list) and len(models) == 1
    m = models[0]
    assert m["id"] == "gemini-2.5-flash-lite"
    assert m["name"] == "Gemini"
    assert m["badge"] == "BETA"


# =============================== 2. config
def test_config_limits():
    r = requests.get(f"{API}/config", timeout=30)
    assert r.status_code == 200
    limits = r.json()["limits"]
    assert limits["max_images_per_message"] == 3
    assert limits["max_image_mb"] == 20
    assert limits["max_chats"] == 5
    assert limits["heavy_chat_mb"] == 150
    assert limits["image_ttl_hours"] == 12
    assert limits["chat_inactive_ttl_days"] == 7


# =============================== 3. dev-login
def test_dev_login_success_any_tgid():
    r = requests.post(
        f"{API}/auth/dev-login",
        json={"tg_id": TG_BASIC, "first_name": "QA-basic", "dev_secret": DEV_SECRET},
        timeout=30,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert isinstance(data.get("access_token"), str) and len(data["access_token"]) > 20
    assert data["user"]["tg_id"] == TG_BASIC


def test_dev_login_wrong_secret():
    r = requests.post(
        f"{API}/auth/dev-login",
        json={"tg_id": TG_BASIC, "first_name": "QA", "dev_secret": "wrong"},
        timeout=30,
    )
    assert r.status_code == 401


# =============================== 4. image upload
def test_upload_valid_png_returns_metadata():
    token = _dev_login(TG_BASIC)
    png = _tiny_png_bytes()
    r = requests.post(
        f"{API}/images",
        headers=_h(token),
        files={"file": ("t.png", png, "image/png")},
        timeout=30,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert isinstance(data["id"], str) and len(data["id"]) == 32
    assert data["mime"] == "image/png"
    assert data["size"] == len(png)
    assert data["url"] == f"/api/images/{data['id']}"
    assert data["expires_in_seconds"] == 43200
    pytest.image_id = data["id"]


def test_upload_rejects_text_plain_415():
    token = _dev_login(TG_BASIC)
    r = requests.post(
        f"{API}/images",
        headers=_h(token),
        files={"file": ("t.txt", b"hello", "text/plain")},
        timeout=30,
    )
    assert r.status_code == 415, r.text


def test_upload_rejects_zero_byte_422():
    token = _dev_login(TG_BASIC)
    r = requests.post(
        f"{API}/images",
        headers=_h(token),
        files={"file": ("empty.png", b"", "image/png")},
        timeout=30,
    )
    assert r.status_code == 422, r.text


def test_upload_rejects_oversize_413():
    token = _dev_login(TG_BASIC)
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (21 * 1024 * 1024)
    r = requests.post(
        f"{API}/images",
        headers=_h(token),
        files={"file": ("big.png", big, "image/png")},
        timeout=180,
    )
    assert r.status_code == 413, r.text
    detail = r.json().get("detail", "")
    assert "20" in detail or "МБ" in detail


def test_upload_requires_auth():
    r = requests.post(
        f"{API}/images",
        files={"file": ("t.png", _tiny_png_bytes(), "image/png")},
        timeout=30,
    )
    assert r.status_code in (401, 403)


# =============================== 5. image download
def test_get_image_returns_bytes():
    img_id = getattr(pytest, "image_id", None)
    assert img_id, "prerequisite upload test did not run"
    r = requests.get(f"{API}/images/{img_id}", timeout=30)
    assert r.status_code == 200
    assert r.headers.get("content-type", "").startswith("image/png")
    assert r.content.startswith(b"\x89PNG")


def test_get_image_invalid_id_400():
    r = requests.get(f"{API}/images/not-a-hex-id", timeout=30)
    assert r.status_code == 400


def test_get_image_valid_hex_but_missing_404():
    r = requests.get(f"{API}/images/{'a' * 32}", timeout=30)
    assert r.status_code == 404


# =============================== 6. cross-user image leak → 410
def test_cross_user_image_ref_returns_410():
    tok_a = _dev_login(TG_CROSS_A, "OwnerA")
    tok_b = _dev_login(TG_CROSS_B, "AttackerB")

    up = requests.post(
        f"{API}/images",
        headers=_h(tok_a),
        files={"file": ("t.png", _tiny_png_bytes(), "image/png")},
        timeout=30,
    )
    assert up.status_code == 200
    img_id = up.json()["id"]

    _sleep_cooldown()
    r = requests.post(
        f"{API}/chat/stream",
        headers={**_h(tok_b), "Content-Type": "application/json"},
        json={"content": "look at this", "image_ids": [img_id]},
        timeout=30,
    )
    assert r.status_code == 410, r.text


# =============================== 7. >3 images rejected
def test_more_than_three_images_rejected_422():
    token = _dev_login(TG_BASIC)
    _sleep_cooldown()
    r = requests.post(
        f"{API}/chat/stream",
        headers={**_h(token), "Content-Type": "application/json"},
        json={"content": "hi", "image_ids": ["a" * 32, "b" * 32, "c" * 32, "d" * 32]},
        timeout=30,
    )
    assert r.status_code == 422, r.text


# =============================== 8. fresh conversation SSE
def test_fresh_account_creates_first_conversation_and_streams():
    _cleanup_tg(TG_CONV_SHAPE)
    token = _dev_login(TG_CONV_SHAPE, "ShapeQA")
    _sleep_cooldown()
    r = requests.post(
        f"{API}/chat/stream",
        headers={**_h(token), "Content-Type": "application/json"},
        json={"content": "Reply with just: OK"},
        stream=True,
        timeout=120,
    )
    assert r.status_code == 200, r.text
    events = _sse_collect(r, max_seconds=90)
    types = [e.get("type") for e in events]
    assert "meta" in types
    assert "done" in types
    meta = next(e for e in events if e.get("type") == "meta")
    cid = meta.get("conversation_id")
    assert cid
    pytest.cid_shape = cid
    errors = [e for e in events if e.get("type") == "error"]
    deltas = [e for e in events if e.get("type") == "delta"]
    assert deltas or errors, f"no delta/error events: {events}"


# =============================== 9. list conversations shape
def test_list_conversations_shape():
    token = _dev_login(TG_CONV_SHAPE)
    r = requests.get(f"{API}/conversations", headers=_h(token), timeout=30)
    assert r.status_code == 200
    convs = r.json()["conversations"]
    assert len(convs) >= 1
    for c in convs:
        assert isinstance(c["locked"], bool)
        assert isinstance(c["size_bytes"], int)
        assert isinstance(c["id"], str)
    updated = [c["updated_at"] for c in convs]
    assert updated == sorted(updated, reverse=True)


# =============================== 10. conversation detail: image shape
def test_conversation_detail_image_shape():
    token = _dev_login(TG_CONV_SHAPE)
    up = requests.post(
        f"{API}/images",
        headers=_h(token),
        files={"file": ("t.png", _tiny_png_bytes(), "image/png")},
        timeout=30,
    )
    assert up.status_code == 200
    img_id = up.json()["id"]

    cid = getattr(pytest, "cid_shape", None)
    assert cid
    _sleep_cooldown()
    r = requests.post(
        f"{API}/chat/stream",
        headers={**_h(token), "Content-Type": "application/json"},
        json={"conversation_id": cid, "content": "What do you see?",
              "image_ids": [img_id]},
        stream=True,
        timeout=120,
    )
    assert r.status_code == 200, r.text
    _sse_collect(r, max_seconds=90)

    detail = requests.get(f"{API}/conversations/{cid}", headers=_h(token), timeout=30)
    assert detail.status_code == 200
    msgs = detail.json()["messages"]
    user_with_img = next((m for m in msgs if m["role"] == "user" and m.get("images")), None)
    assert user_with_img, "no user message with images found"
    img = user_with_img["images"][0]
    assert set(img.keys()) >= {"id", "mime", "url"}
    assert img["url"].startswith("/api/images/")
    assert "data" not in img


# =============================== 11. cooldown 3s
def test_chat_cooldown_429():
    token = _dev_login(TG_COOLDOWN, "CoolQA")
    _sleep_cooldown()
    r1 = requests.post(
        f"{API}/chat/stream",
        headers={**_h(token), "Content-Type": "application/json"},
        json={"content": "one"},
        stream=True,
        timeout=30,
    )
    assert r1.status_code == 200
    r1.close()
    r2 = requests.post(
        f"{API}/chat/stream",
        headers={**_h(token), "Content-Type": "application/json"},
        json={"content": "two"},
        timeout=30,
    )
    assert r2.status_code == 429, f"expected 429, got {r2.status_code}: {r2.text}"


# =============================== 12. 5-chat limit
def test_max_5_chats_returns_429():
    _cleanup_tg(TG_LIMIT)
    token = _dev_login(TG_LIMIT, "LimitQA")

    now = datetime.now(timezone.utc)
    docs = [{
        "tg_id": TG_LIMIT, "title": f"chat-{i}", "model": "gemini-2.5-flash-lite",
        "created_at": now, "updated_at": now, "size_bytes": 0, "message_count": 0,
        "messages": [], "last_summary": "",
    } for i in range(5)]
    _db.conversations.insert_many(docs)
    assert _db.conversations.count_documents({"tg_id": TG_LIMIT}) == 5

    _sleep_cooldown()
    r = requests.post(
        f"{API}/chat/stream",
        headers={**_h(token), "Content-Type": "application/json"},
        json={"content": "should be blocked"},
        timeout=30,
    )
    assert r.status_code == 429, r.text
    detail = r.json().get("detail", "").lower()
    assert "лимит в 5 чатов" in detail or "5 чат" in detail


# =============================== 13. heavy chat lifecycle
def test_heavy_chat_full_lifecycle():
    _cleanup_tg(TG_HEAVY)
    token = _dev_login(TG_HEAVY, "HeavyQA")

    _sleep_cooldown()
    r = requests.post(
        f"{API}/chat/stream",
        headers={**_h(token), "Content-Type": "application/json"},
        json={"content": "hi"},
        stream=True,
        timeout=60,
    )
    assert r.status_code == 200
    events = _sse_collect(r, max_seconds=60)
    meta = next(e for e in events if e.get("type") == "meta")
    heavy_cid = meta["conversation_id"]

    # attach a fake image to test cascade delete
    fake_img_id = "f" * 32
    _db.chat_images.insert_one({
        "_id": fake_img_id, "tg_id": TG_HEAVY, "mime": "image/png",
        "size": 1, "data": b"\x00", "created_at": datetime.now(timezone.utc),
        "expire_at": datetime.now(timezone.utc),
    })
    _db.conversations.update_one(
        {"_id": ObjectId(heavy_cid)},
        {"$push": {"messages": {"role": "user", "content": "",
                                "images": [{"id": fake_img_id, "mime": "image/png"}],
                                "model": "gemini-2.5-flash-lite",
                                "created_at": datetime.now(timezone.utc)}}},
    )

    heavy_title = "БОЛЬШОЙ ЧАТ"
    _db.conversations.update_one(
        {"_id": ObjectId(heavy_cid)},
        {"$set": {"size_bytes": 160 * 1024 * 1024, "title": heavy_title}},
    )

    _sleep_cooldown()
    r_new = requests.post(
        f"{API}/chat/stream",
        headers={**_h(token), "Content-Type": "application/json"},
        json={"content": "new chat please"},
        timeout=30,
    )
    assert r_new.status_code == 413, r_new.text
    detail_new = r_new.json().get("detail", "")
    assert heavy_title in detail_new
    assert "150 МБ" in detail_new

    _sleep_cooldown()
    r_write = requests.post(
        f"{API}/chat/stream",
        headers={**_h(token), "Content-Type": "application/json"},
        json={"conversation_id": heavy_cid, "content": "extend it"},
        timeout=30,
    )
    assert r_write.status_code == 423, r_write.text

    r_del = requests.delete(f"{API}/conversations/{heavy_cid}", headers=_h(token), timeout=30)
    assert r_del.status_code == 200, r_del.text
    assert _db.chat_images.count_documents({"_id": fake_img_id}) == 0
    assert _db.conversations.count_documents({"_id": ObjectId(heavy_cid)}) == 0

    _sleep_cooldown()
    r_after = requests.post(
        f"{API}/chat/stream",
        headers={**_h(token), "Content-Type": "application/json"},
        json={"content": "fresh start"},
        stream=True,
        timeout=60,
    )
    assert r_after.status_code == 200, r_after.text
    _sse_collect(r_after, max_seconds=60)


# =============================== 14. TTL indexes
def test_mongo_ttl_indexes():
    imgs = _db.chat_images.index_information()
    conv = _db.conversations.index_information()

    img_ttl = next((v for v in imgs.values()
                    if any(k == "expire_at" for k, _ in v.get("key", []))
                    and "expireAfterSeconds" in v), None)
    assert img_ttl is not None, f"chat_images TTL index missing: {imgs}"
    assert img_ttl["expireAfterSeconds"] == 0

    conv_ttl = next((v for v in conv.values()
                     if any(k == "updated_at" for k, _ in v.get("key", []))
                     and "expireAfterSeconds" in v), None)
    assert conv_ttl is not None, f"conversations TTL index missing: {conv}"
    assert conv_ttl["expireAfterSeconds"] == 7 * 24 * 60 * 60
