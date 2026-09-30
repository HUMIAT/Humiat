import json
import logging
import os
import re
import time
import uuid
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import event

PERFORMANCE_MONITORING = os.getenv("PERFORMANCE_MONITORING", "true").strip().lower() in {"1", "true", "yes", "on"}
PERFORMANCE_DETAIL = os.getenv("PERFORMANCE_DETAIL", "slow").strip().lower()
PERFORMANCE_SLOW_REQUEST_MS = float(os.getenv("PERFORMANCE_SLOW_REQUEST_MS", "500"))
PERFORMANCE_SLOW_SQL_MS = float(os.getenv("PERFORMANCE_SLOW_SQL_MS", "300"))
PERFORMANCE_MAX_RECORDS = max(20, int(os.getenv("PERFORMANCE_MAX_RECORDS", "300")))
PERFORMANCE_ROUTES = [p.strip() for p in os.getenv("PERFORMANCE_ROUTES", "all").split(",") if p.strip()]

logger = logging.getLogger("organiza.performance")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False

_current_metrics: ContextVar[dict[str, Any] | None] = ContextVar("performance_metrics", default=None)
_recent_records: deque[dict[str, Any]] = deque(maxlen=PERFORMANCE_MAX_RECORDS)
_sql_listener_installed = False


def _sql_signature(statement: str) -> tuple[str, str, list[str]]:
    """Cria assinatura segura para agrupar SQL sem guardar parâmetros."""
    raw = " ".join((statement or "").split())
    verb = raw.split(None, 1)[0].upper() if raw else "SQL"
    # Os valores chegam separados em ``parameters``; ainda assim, mascaramos
    # literais existentes na instrução para evitar exposição acidental.
    safe = re.sub(r"'(?:''|[^'])*'", "?", raw)
    safe = re.sub(r'\b\d+(?:\.\d+)?\b', '?', safe)
    safe = re.sub(r'\s+', ' ', safe).strip()
    tables: list[str] = []
    for pattern in (r'\bFROM\s+([\w."]+)', r'\bJOIN\s+([\w."]+)', r'\bUPDATE\s+([\w."]+)', r'\bINTO\s+([\w."]+)'):
        for match in re.findall(pattern, safe, flags=re.IGNORECASE):
            table = match.strip('"')
            if table and table not in tables:
                tables.append(table)
    return verb, safe[:500], tables[:8]



def enabled_for_path(path: str) -> bool:
    if not PERFORMANCE_MONITORING:
        return False
    if not PERFORMANCE_ROUTES or "all" in PERFORMANCE_ROUTES:
        return True
    return any(path == prefix or path.startswith(prefix.rstrip("/") + "/") for prefix in PERFORMANCE_ROUTES)


def current_metrics() -> dict[str, Any] | None:
    return _current_metrics.get()


@contextmanager
def perf_stage(name: str):
    """Cronometra uma etapa sem registrar parâmetros ou dados pessoais."""
    metrics = current_metrics()
    if not metrics:
        yield
        return
    started = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        metrics.setdefault("stages", []).append({"name": name, "ms": elapsed_ms})


def install_sql_monitor(engine) -> None:
    global _sql_listener_installed
    if _sql_listener_installed or not PERFORMANCE_MONITORING:
        return
    _sql_listener_installed = True

    @event.listens_for(engine, "before_cursor_execute")
    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        context._perf_query_start = time.perf_counter()

    @event.listens_for(engine, "after_cursor_execute")
    def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        metrics = current_metrics()
        if not metrics:
            return
        start = getattr(context, "_perf_query_start", None)
        if start is None:
            return
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        metrics["sql_count"] = metrics.get("sql_count", 0) + 1
        metrics["sql_ms"] = round(metrics.get("sql_ms", 0.0) + elapsed_ms, 2)
        metrics["sql_max_ms"] = max(metrics.get("sql_max_ms", 0.0), elapsed_ms)

        verb, signature, tables = _sql_signature(statement or "")
        groups = metrics.setdefault("sql_groups_map", {})
        group = groups.setdefault(signature, {
            "verb": verb, "signature": signature, "tables": tables,
            "count": 0, "total_ms": 0.0, "max_ms": 0.0,
        })
        group["count"] += 1
        group["total_ms"] = round(group["total_ms"] + elapsed_ms, 2)
        group["max_ms"] = max(group["max_ms"], elapsed_ms)

        if PERFORMANCE_DETAIL == "full" or elapsed_ms >= PERFORMANCE_SLOW_SQL_MS:
            metrics.setdefault("slow_sql", []).append({
                "verb": verb,
                "ms": elapsed_ms,
                "tables": tables,
                "signature": signature[:180],
            })


class PerformanceMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not enabled_for_path(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        request_id = uuid.uuid4().hex[:12]
        metrics: dict[str, Any] = {
            "request_id": request_id,
            "method": scope.get("method", "GET"),
            "path": scope.get("path", ""),
            "sql_count": 0,
            "sql_ms": 0.0,
            "sql_max_ms": 0.0,
            "stages": [],
            "slow_sql": [],
            "sql_groups_map": {},
            "status": 500,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        token = _current_metrics.set(metrics)

        async def send_wrapper(message):
            if message.get("type") == "http.response.start":
                metrics["status"] = message.get("status", 0)
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            metrics["error"] = type(exc).__name__
            raise
        finally:
            metrics["total_ms"] = round((time.perf_counter() - started) * 1000, 2)
            groups = []
            for group in metrics.pop("sql_groups_map", {}).values():
                count = max(1, int(group.get("count") or 0))
                group["avg_ms"] = round(float(group.get("total_ms") or 0) / count, 2)
                groups.append(group)
            groups.sort(key=lambda item: (item.get("total_ms", 0), item.get("count", 0)), reverse=True)
            metrics["sql_groups"] = groups[:60]
            _current_metrics.reset(token)
            should_store = (
                PERFORMANCE_DETAIL == "full"
                or metrics["total_ms"] >= PERFORMANCE_SLOW_REQUEST_MS
                or metrics.get("status", 200) >= 400
            )
            if should_store:
                record = dict(metrics)
                _recent_records.appendleft(record)
                logger.warning("PERF %s", json.dumps(record, ensure_ascii=False, separators=(",", ":")))


def recent_records(limit: int = 100) -> list[dict[str, Any]]:
    return list(_recent_records)[:max(1, min(limit, PERFORMANCE_MAX_RECORDS))]



def clear_records() -> int:
    """Limpa os registros em memória e retorna quantos foram removidos."""
    total = len(_recent_records)
    _recent_records.clear()
    return total


def performance_summary(limit: int = 300) -> dict[str, Any]:
    records = recent_records(limit)
    grouped: dict[str, dict[str, Any]] = {}
    for record in records:
        key = f"{record.get('method', 'GET')} {record.get('path', '')}"
        item = grouped.setdefault(key, {
            "route": key, "calls": 0, "total_ms": 0.0, "sql_ms": 0.0,
            "sql_count": 0, "max_ms": 0.0, "errors": 0,
        })
        item["calls"] += 1
        item["total_ms"] += float(record.get("total_ms") or 0)
        item["sql_ms"] += float(record.get("sql_ms") or 0)
        item["sql_count"] += int(record.get("sql_count") or 0)
        item["max_ms"] = max(item["max_ms"], float(record.get("total_ms") or 0))
        if int(record.get("status") or 200) >= 400 or record.get("error"):
            item["errors"] += 1
    ranking = []
    for item in grouped.values():
        calls = max(1, item["calls"])
        item["avg_ms"] = round(item["total_ms"] / calls, 2)
        item["avg_sql_ms"] = round(item["sql_ms"] / calls, 2)
        item["avg_sql_count"] = round(item["sql_count"] / calls, 1)
        item["total_ms"] = round(item["total_ms"], 2)
        item["sql_ms"] = round(item["sql_ms"], 2)
        ranking.append(item)
    ranking.sort(key=lambda x: (x["avg_ms"], x["max_ms"]), reverse=True)
    return {"records": records, "ranking": ranking}

def monitor_status() -> dict[str, Any]:
    return {
        "enabled": PERFORMANCE_MONITORING,
        "detail": PERFORMANCE_DETAIL,
        "slow_request_ms": PERFORMANCE_SLOW_REQUEST_MS,
        "slow_sql_ms": PERFORMANCE_SLOW_SQL_MS,
        "routes": PERFORMANCE_ROUTES,
        "records_in_memory": len(_recent_records),
    }
