"""
GeminiKeyManager — отказоустойчивый менеджер жизненного цикла Gemini API-ключей.

Особенности:
  • Полное состояние каждого ключа (статус, счётчики, причины, тайминги).
  • Round-Robin выбор (без random) с индивидуальным per-key rate limiter (RPM).
  • Автоматическое восстановление по абсолютному времени (Unix ts) — устойчиво
    к перезапускам/усыплению процесса (Render): все кулдауны пересчитываются на лету.
  • Потокобезопасность (threading.RLock, короткие критические секции без await).
  • Failover: при отказе ключа менеджер сам выдаёт следующий доступный.
  • Подробное логирование всех изменений состояния.
"""
import re
import time
import json
import logging
import threading
from enum import Enum
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger("ai_workspace.keys")


class KeyStatus(str, Enum):
    HEALTHY = "healthy"          # полностью рабочий
    COOLDOWN = "cooldown"        # временно недоступен (rate limit / 429 retry-after)
    DAILY_LIMIT = "daily_limit"  # исчерпана дневная квота (RESOURCE_EXHAUSTED / per-day)
    INVALID = "invalid"          # 401 — ключ недействителен (навсегда)
    FORBIDDEN = "forbidden"      # 403 — нет доступа к модели (навсегда)
    BROKEN = "broken"            # временная внутренняя ошибка (5xx / сеть)


STATUS_LABEL = {
    KeyStatus.HEALTHY: "Healthy",
    KeyStatus.COOLDOWN: "Cooldown",
    KeyStatus.DAILY_LIMIT: "Daily Limit",
    KeyStatus.INVALID: "Invalid",
    KeyStatus.FORBIDDEN: "Forbidden",
    KeyStatus.BROKEN: "Broken",
}

# Никогда не восстанавливаются автоматически.
_PERMANENT = {KeyStatus.INVALID, KeyStatus.FORBIDDEN}
# Временно недоступны, но имеют время реактивации.
_TEMP_DOWN = {KeyStatus.COOLDOWN, KeyStatus.DAILY_LIMIT, KeyStatus.BROKEN}

_RETRY_DELAY_RE = re.compile(r'"?retryDelay"?\s*[:=]\s*"?([0-9]+(?:\.[0-9]+)?)\s*s', re.IGNORECASE)
_RETRY_IN_RE = re.compile(r'retry(?:\s+(?:in|after))?\s+([0-9]+(?:\.[0-9]+)?)\s*s', re.IGNORECASE)


def parse_retry_after(body: str, headers: Optional[Dict[str, str]] = None) -> float:
    """Извлекает рекомендованную задержку (сек) из заголовков или тела ошибки Gemini."""
    if headers:
        ra = headers.get("retry-after") or headers.get("Retry-After")
        if ra:
            try:
                return float(ra)
            except (TypeError, ValueError):
                pass
    for rx in (_RETRY_DELAY_RE, _RETRY_IN_RE):
        m = rx.search(body or "")
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
    return 0.0


def classify_error(status_code: int, body: str) -> str:
    """
    Определяет вид ошибки: daily_limit | cooldown | invalid | forbidden | broken | request.
    Приоритет — по HTTP-коду и СТРУКТУРИРОВАННЫМ полям ответа Gemini
    (error.status, error.details[].reason/quotaId/violations). Текст сообщений —
    только запасной вариант, чтобы смена формулировок Gemini не ломала классификацию.
    """
    # 1) Пробуем разобрать структурированный JSON-ответ Gemini.
    parsed: Dict[str, Any] = {}
    try:
        obj = json.loads(body) if body else {}
        parsed = obj.get("error", obj) if isinstance(obj, dict) else {}
    except (ValueError, TypeError):
        parsed = {}

    status_str = str(parsed.get("status", "")).upper()
    details = parsed.get("details", []) if isinstance(parsed, dict) else []

    def _has_per_day() -> bool:
        """Ищет признак именно ДНЕВНОЙ квоты в структурированных деталях."""
        for d in details or []:
            if not isinstance(d, dict):
                continue
            blob = json.dumps(d).lower()
            if "perday" in blob or "per_day" in blob or "per day" in blob or "requestsperday" in blob:
                return True
        return False

    low = (body or "").lower()

    if status_code == 401 or (status_code == 400 and "api key" in low) or status_str == "UNAUTHENTICATED":
        return "invalid"
    if status_code == 403 or status_str == "PERMISSION_DENIED":
        return "forbidden"
    if status_code == 429 or status_str == "RESOURCE_EXHAUSTED":
        # RESOURCE_EXHAUSTED покрывает и минутный rate-limit, и дневную квоту.
        # Дневную определяем по структурированным деталям, текст — как fallback.
        if _has_per_day() or "perday" in low or "per day" in low or "requests per day" in low or "requestsperday" in low:
            return "daily_limit"
        return "cooldown"
    if status_code >= 500 or status_str in ("INTERNAL", "UNAVAILABLE", "DEADLINE_EXCEEDED"):
        return "broken"
    return "request"  # прочие 4xx (safety/валидация) — не проблема ключа


def _next_daily_reset(now_ts: float) -> float:
    """Ближайшая полночь America/Los_Angeles — момент сброса дневной квоты Gemini free-tier."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/Los_Angeles")
    except Exception:  # noqa: BLE001
        tz = timezone.utc
    now = datetime.fromtimestamp(now_ts, tz)
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=30, microsecond=0)
    return nxt.timestamp()


class GeminiKeyManager:
    def __init__(self, keys: List[str], rpm_per_key: int = 15,
                 min_cooldown: float = 30.0, broken_base_cooldown: float = 15.0):
        self._keys: List[str] = list(keys)
        self._rpm = max(1, int(rpm_per_key))
        self._min_cooldown = float(min_cooldown)
        self._broken_base = float(broken_base_cooldown)
        self._lock = threading.RLock()
        self._rr = 0
        # Глобальные метрики (динамические, не зависят от числа ключей).
        self._metrics = {
            "total_requests": 0,      # успешно завершённых запросов к Gemini
            "total_switches": 0,      # переключений между ключами (failover)
            "total_429": 0,           # ответов 429 (rate limit)
            "total_daily_limit": 0,   # попаданий в дневной лимит
            "total_503": 0,           # отказов (нет рабочих ключей)
            "latency_sum": 0.0,       # сумма времени ответа Gemini (сек)
            "latency_count": 0,       # число замеров времени ответа
        }
        self._states: List[Dict[str, Any]] = []
        for i in range(len(self._keys)):
            self._states.append({
                "idx": i,
                "status": KeyStatus.HEALTHY,
                "last_used": 0.0,
                "cooldown_until": 0.0,     # абсолютный Unix ts (COOLDOWN / BROKEN)
                "reactivate_at": 0.0,      # абсолютный Unix ts (DAILY_LIMIT)
                "success": 0,
                "errors": 0,
                "served": 0,               # обслужено запросов (выдач ключа)
                "last_reason": "",
                "calls": deque(),          # ts вызовов за последние 60с (per-key RPM)
            })
        logger.info("GeminiKeyManager initialised: %s keys, %s RPM/key", len(self._keys), self._rpm)

    # ------------------------------------------------------------------ internals
    def _trim(self, s: Dict[str, Any], now: float) -> None:
        dq = s["calls"]
        while dq and dq[0] <= now - 60.0:
            dq.popleft()

    def _refresh(self, now: float) -> None:
        """Пересчёт статусов по абсолютному времени: возврат ключей после кулдауна/сброса квоты."""
        for s in self._states:
            st = s["status"]
            if st in _TEMP_DOWN:
                due = s["reactivate_at"] if st == KeyStatus.DAILY_LIMIT else s["cooldown_until"]
                if due and now >= due:
                    logger.info("Key #%s recovered from %s -> Healthy", s["idx"], STATUS_LABEL[st])
                    s["status"] = KeyStatus.HEALTHY
                    s["cooldown_until"] = 0.0
                    s["reactivate_at"] = 0.0

    def _healthy_available_locked(self, now: float) -> int:
        c = 0
        for s in self._states:
            if s["status"] == KeyStatus.HEALTHY:
                self._trim(s, now)
                if len(s["calls"]) < self._rpm:
                    c += 1
        return c

    # ------------------------------------------------------------------ selection
    def acquire(self) -> Tuple[Optional[str], int, Optional[float]]:
        """
        Round-Robin выбор здорового ключа с учётом per-key RPM.
        Возвращает (key, idx, 0.0) при успехе.
        Если сейчас нет доступных — (None, -1, wait_sec) где wait_sec = сколько ждать
        до ближайшей доступности (или None, если рабочих ключей нет вообще).
        """
        with self._lock:
            now = time.time()
            self._refresh(now)
            n = len(self._states)
            if n == 0:
                return None, -1, None
            soonest: Optional[float] = None
            for _ in range(n):
                i = self._rr % n
                self._rr = (self._rr + 1) % n
                s = self._states[i]
                if s["status"] != KeyStatus.HEALTHY:
                    if s["status"] in _TEMP_DOWN:
                        due = s["reactivate_at"] if s["status"] == KeyStatus.DAILY_LIMIT else s["cooldown_until"]
                        if due:
                            soonest = due if soonest is None else min(soonest, due)
                    continue
                self._trim(s, now)
                if len(s["calls"]) >= self._rpm:
                    due = s["calls"][0] + 60.0
                    soonest = due if soonest is None else min(soonest, due)
                    continue
                # доступен
                s["calls"].append(now)
                s["last_used"] = now
                s["served"] += 1
                logger.debug("Acquired key #%s (served=%s, healthy_now=%s)",
                             i, s["served"], self._healthy_available_locked(now))
                return self._keys[i], i, 0.0
            wait = max(0.0, soonest - now) if soonest is not None else None
            return None, -1, wait

    def peek(self) -> Tuple[bool, Optional[float], bool]:
        """(available_now, wait_sec, recoverable). Для предполётной проверки/503."""
        with self._lock:
            now = time.time()
            self._refresh(now)
            soonest: Optional[float] = None
            recoverable = False
            for s in self._states:
                if s["status"] == KeyStatus.HEALTHY:
                    self._trim(s, now)
                    if len(s["calls"]) < self._rpm:
                        return True, 0.0, True
                    due = s["calls"][0] + 60.0
                    soonest = due if soonest is None else min(soonest, due)
                    recoverable = True
                elif s["status"] in _TEMP_DOWN:
                    due = s["reactivate_at"] if s["status"] == KeyStatus.DAILY_LIMIT else s["cooldown_until"]
                    soonest = due if soonest is None else min(soonest, due)
                    recoverable = True
            wait = max(0.0, soonest - now) if soonest is not None else None
            return False, wait, recoverable

    # ------------------------------------------------------------------ reporting
    def report_success(self, idx: int) -> None:
        if not (0 <= idx < len(self._states)):
            return
        with self._lock:
            s = self._states[idx]
            s["success"] += 1
            if s["status"] in (KeyStatus.BROKEN, KeyStatus.COOLDOWN):
                logger.info("Key #%s success -> Healthy", idx)
            s["status"] = KeyStatus.HEALTHY
            s["cooldown_until"] = 0.0
            s["reactivate_at"] = 0.0

    def _set_down(self, idx: int, status: KeyStatus, due: float, reason: str) -> None:
        s = self._states[idx]
        s["errors"] += 1
        s["status"] = status
        s["last_reason"] = reason
        if status == KeyStatus.DAILY_LIMIT:
            s["reactivate_at"] = due
            s["cooldown_until"] = 0.0
        elif status in _PERMANENT:
            s["cooldown_until"] = 0.0
            s["reactivate_at"] = 0.0
        else:
            s["cooldown_until"] = due
        now = time.time()
        logger.warning("Key #%s -> %s (%s). Healthy now: %s | cooldown: %s | daily: %s",
                       idx, STATUS_LABEL[status], reason,
                       self._healthy_available_locked(now),
                       sum(1 for x in self._states if x["status"] == KeyStatus.COOLDOWN),
                       sum(1 for x in self._states if x["status"] == KeyStatus.DAILY_LIMIT))

    def report_rate_limit(self, idx: int, retry_after: float = 0.0, reason: str = "429 rate limit") -> None:
        if not (0 <= idx < len(self._states)):
            return
        with self._lock:
            self._metrics["total_429"] += 1
            wait = max(retry_after, self._min_cooldown) if retry_after > 0 else self._min_cooldown
            self._set_down(idx, KeyStatus.COOLDOWN, time.time() + wait,
                           f"{reason} (retry ~{int(wait)}s)")

    def report_daily_limit(self, idx: int, reason: str = "daily quota exhausted") -> None:
        if not (0 <= idx < len(self._states)):
            return
        with self._lock:
            self._metrics["total_daily_limit"] += 1
            self._set_down(idx, KeyStatus.DAILY_LIMIT, _next_daily_reset(time.time()), reason)

    def report_invalid(self, idx: int, reason: str = "invalid key (401)") -> None:
        if not (0 <= idx < len(self._states)):
            return
        with self._lock:
            self._set_down(idx, KeyStatus.INVALID, 0.0, reason)

    def report_forbidden(self, idx: int, reason: str = "no model access (403)") -> None:
        if not (0 <= idx < len(self._states)):
            return
        with self._lock:
            self._set_down(idx, KeyStatus.FORBIDDEN, 0.0, reason)

    def report_server_error(self, idx: int, attempt: int = 1, reason: str = "server error 5xx") -> None:
        if not (0 <= idx < len(self._states)):
            return
        with self._lock:
            wait = min(self._broken_base * max(1, attempt), 120.0)
            self._set_down(idx, KeyStatus.BROKEN, time.time() + wait, reason)

    def report_network_error(self, idx: int, reason: str = "network error") -> None:
        if not (0 <= idx < len(self._states)):
            return
        with self._lock:
            self._set_down(idx, KeyStatus.BROKEN, time.time() + self._broken_base, reason)

    # ------------------------------------------------------------------ metrics recorders
    def record_switch(self) -> None:
        """Переключение на другой ключ (failover) в рамках одного запроса."""
        with self._lock:
            self._metrics["total_switches"] += 1

    def record_success_request(self, latency_sec: Optional[float] = None) -> None:
        """Успешно завершённый запрос к Gemini (+ время ответа, если есть)."""
        with self._lock:
            self._metrics["total_requests"] += 1
            if latency_sec is not None and latency_sec >= 0:
                self._metrics["latency_sum"] += latency_sec
                self._metrics["latency_count"] += 1

    def record_503(self) -> None:
        """Отказ обслуживания: рабочих ключей нет."""
        with self._lock:
            self._metrics["total_503"] += 1

    # ------------------------------------------------------------------ stats
    @property
    def total(self) -> int:
        return len(self._states)

    def healthy_count(self) -> int:
        with self._lock:
            now = time.time()
            self._refresh(now)
            return sum(1 for s in self._states if s["status"] == KeyStatus.HEALTHY)

    def health(self) -> Dict[str, Any]:
        """Компактная сводка для /health (счётчики по статусам)."""
        with self._lock:
            now = time.time()
            self._refresh(now)
            counts = {st.value: 0 for st in KeyStatus}
            for s in self._states:
                counts[s["status"].value] += 1
            return {
                "total_keys": len(self._states),
                "healthy": counts[KeyStatus.HEALTHY.value],
                "cooldown": counts[KeyStatus.COOLDOWN.value],
                "daily_limit": counts[KeyStatus.DAILY_LIMIT.value],
                "invalid": counts[KeyStatus.INVALID.value],
                "forbidden": counts[KeyStatus.FORBIDDEN.value],
                "broken": counts[KeyStatus.BROKEN.value],
            }

    def _metrics_out(self) -> Dict[str, Any]:
        m = self._metrics
        avg = (m["latency_sum"] / m["latency_count"]) if m["latency_count"] else 0.0
        return {
            "total_requests": m["total_requests"],
            "total_switches": m["total_switches"],
            "total_429": m["total_429"],
            "total_daily_limit": m["total_daily_limit"],
            "total_503": m["total_503"],
            "avg_response_ms": round(avg * 1000, 1),
        }

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            now = time.time()
            self._refresh(now)
            counts = {st.value: 0 for st in KeyStatus}
            keys: List[Dict[str, Any]] = []
            for s in self._states:
                counts[s["status"].value] += 1
                remaining = 0.0
                next_at = None
                if s["status"] in _TEMP_DOWN:
                    due = s["reactivate_at"] if s["status"] == KeyStatus.DAILY_LIMIT else s["cooldown_until"]
                    if due:
                        remaining = max(0.0, due - now)
                        next_at = datetime.fromtimestamp(due, timezone.utc).isoformat()
                keys.append({
                    "idx": s["idx"],
                    "status": s["status"].value,
                    "status_label": STATUS_LABEL[s["status"]],
                    "success": s["success"],
                    "errors": s["errors"],
                    "served": s["served"],
                    "cooldown_remaining_sec": round(remaining, 1),
                    "next_activation_at": next_at,
                    "last_reason": s["last_reason"],
                })
            return {
                "total": len(self._states),
                "healthy": counts[KeyStatus.HEALTHY.value],
                "cooldown": counts[KeyStatus.COOLDOWN.value],
                "daily_limit": counts[KeyStatus.DAILY_LIMIT.value],
                "invalid": counts[KeyStatus.INVALID.value],
                "forbidden": counts[KeyStatus.FORBIDDEN.value],
                "broken": counts[KeyStatus.BROKEN.value],
                "rpm_per_key": self._rpm,
                "metrics": self._metrics_out(),
                "keys": keys,
            }
