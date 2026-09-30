import json
import logging
import os
import re
import time
import uuid
from collections import Counter, defaultdict, deque
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import event

PERFORMANCE_MONITORING = os.getenv("ORGANIZA_PERFORMANCE_MONITORING", "true").strip().lower() in {"1", "true", "yes", "on"}
PERFORMANCE_DETAIL = os.getenv("ORGANIZA_PERFORMANCE_DETAIL", "slow").strip().lower()
PERFORMANCE_SLOW_REQUEST_MS = float(os.getenv("ORGANIZA_PERFORMANCE_SLOW_REQUEST_MS", "500"))
PERFORMANCE_SLOW_SQL_MS = float(os.getenv("ORGANIZA_PERFORMANCE_SLOW_SQL_MS", "300"))
PERFORMANCE_MAX_RECORDS = max(20, int(os.getenv("ORGANIZA_PERFORMANCE_MAX_RECORDS", "400")))
PERFORMANCE_ROUTES = [p.strip() for p in os.getenv("ORGANIZA_PERFORMANCE_ROUTES", "/organiza").split(",") if p.strip()]

logger = logging.getLogger("organiza.performance")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False

_current_metrics: ContextVar[dict[str, Any] | None] = ContextVar("organiza_performance_metrics", default=None)
_recent_records: deque[dict[str, Any]] = deque(maxlen=PERFORMANCE_MAX_RECORDS)
_sql_listener_installed = False


def _sql_signature(statement: str) -> tuple[str, str, list[str]]:
    """Agrupa SQL sem armazenar parâmetros, valores pessoais ou payloads."""
    raw = " ".join((statement or "").split())
    verb = raw.split(None, 1)[0].upper() if raw else "SQL"
    safe = re.sub(r"'(?:''|[^'])*'", "?", raw)
    safe = re.sub(r"\b\d+(?:\.\d+)?\b", "?", safe)
    safe = re.sub(r"\s+", " ", safe).strip()
    tables: list[str] = []
    for pattern in (
        r"\bFROM\s+([\w.\"]+)",
        r"\bJOIN\s+([\w.\"]+)",
        r"\bUPDATE\s+([\w.\"]+)",
        r"\bINTO\s+([\w.\"]+)",
        r"\bDELETE\s+FROM\s+([\w.\"]+)",
    ):
        for match in re.findall(pattern, safe, flags=re.IGNORECASE):
            table = match.strip('"')
            if table and table not in tables:
                tables.append(table)
    return verb, safe[:600], tables[:10]


def enabled_for_path(path: str) -> bool:
    if not PERFORMANCE_MONITORING:
        return False
    if path.startswith("/static/") or path.startswith("/organiza/diagnostico-performance"):
        return False
    if not PERFORMANCE_ROUTES or "all" in PERFORMANCE_ROUTES:
        return True
    return any(path == prefix or path.startswith(prefix.rstrip("/") + "/") for prefix in PERFORMANCE_ROUTES)


def current_metrics() -> dict[str, Any] | None:
    return _current_metrics.get()


@contextmanager
def perf_stage(name: str):
    metrics = current_metrics()
    if not metrics:
        yield
        return
    started = time.perf_counter()
    try:
        yield
    finally:
        metrics.setdefault("stages", []).append({
            "name": name,
            "ms": round((time.perf_counter() - started) * 1000, 2),
        })


def install_sql_monitor(engine) -> None:
    global _sql_listener_installed
    if _sql_listener_installed or not PERFORMANCE_MONITORING:
        return
    _sql_listener_installed = True

    @event.listens_for(engine, "before_cursor_execute")
    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        context._organiza_perf_query_start = time.perf_counter()

    @event.listens_for(engine, "after_cursor_execute")
    def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        metrics = current_metrics()
        if not metrics:
            return
        start = getattr(context, "_organiza_perf_query_start", None)
        if start is None:
            return
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        metrics["sql_count"] = metrics.get("sql_count", 0) + 1
        metrics["sql_ms"] = round(metrics.get("sql_ms", 0.0) + elapsed_ms, 2)
        metrics["sql_max_ms"] = max(metrics.get("sql_max_ms", 0.0), elapsed_ms)

        verb, signature, tables = _sql_signature(statement or "")
        groups = metrics.setdefault("sql_groups_map", {})
        group = groups.setdefault(signature, {
            "verb": verb,
            "signature": signature,
            "tables": tables,
            "count": 0,
            "total_ms": 0.0,
            "max_ms": 0.0,
        })
        group["count"] += 1
        group["total_ms"] = round(group["total_ms"] + elapsed_ms, 2)
        group["max_ms"] = max(group["max_ms"], elapsed_ms)

        if PERFORMANCE_DETAIL == "full" or elapsed_ms >= PERFORMANCE_SLOW_SQL_MS:
            metrics.setdefault("slow_sql", []).append({
                "verb": verb,
                "ms": elapsed_ms,
                "tables": tables,
                "signature": signature[:220],
            })


class PerformanceMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope.get("type") != "http" or not enabled_for_path(path):
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        request_id = uuid.uuid4().hex[:12]
        metrics: dict[str, Any] = {
            "request_id": request_id,
            "method": scope.get("method", "GET"),
            "path": path,
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
            metrics["sql_groups"] = groups[:80]
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
    total = len(_recent_records)
    _recent_records.clear()
    return total


def _build_suggestions(records: list[dict[str, Any]]) -> list[dict[str, str]]:
    suggestions: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(level: str, title: str, detail: str):
        key = title + "|" + detail
        if key in seen:
            return
        seen.add(key)
        suggestions.append({"level": level, "title": title, "detail": detail})

    for record in records[:120]:
        route = f"{record.get('method', 'GET')} {record.get('path', '')}"
        total_ms = float(record.get("total_ms") or 0)
        sql_ms = float(record.get("sql_ms") or 0)
        sql_count = int(record.get("sql_count") or 0)
        if total_ms >= 3000:
            add("bad", "Rota muito lenta", f"{route} chegou a {total_ms/1000:.2f}s.")
        elif total_ms >= 1500:
            add("warn", "Rota lenta", f"{route} chegou a {total_ms/1000:.2f}s.")
        if sql_count >= 30:
            add("bad", "Muitas consultas SQL", f"{route} executou {sql_count} consultas em uma única requisição.")
        elif sql_count >= 15:
            add("warn", "Quantidade alta de SQL", f"{route} executou {sql_count} consultas.")
        if total_ms > 0 and sql_ms / total_ms >= 0.70 and sql_ms >= 500:
            add("warn", "Banco domina o tempo da tela", f"{route}: {sql_ms/total_ms*100:.0f}% do tempo foi gasto em SQL.")
        for group in record.get("sql_groups") or []:
            count = int(group.get("count") or 0)
            if count >= 10:
                tables = ", ".join(group.get("tables") or []) or "tabela não identificada"
                add("bad", "Possível N+1 / consulta repetida", f"{route}: a mesma consulta ocorreu {count}x ({tables}).")
            if float(group.get("max_ms") or 0) >= PERFORMANCE_SLOW_SQL_MS * 2:
                tables = ", ".join(group.get("tables") or []) or "tabela não identificada"
                add("warn", "SQL individual muito lento", f"{route}: consulta em {tables} chegou a {float(group.get('max_ms') or 0):.0f} ms.")
        if len(suggestions) >= 20:
            break

    if not suggestions and records:
        add("ok", "Nenhum gargalo crítico detectado", "Os registros atuais não ultrapassaram os limites principais de alerta.")
    return suggestions[:20]


def performance_summary(limit: int = 300) -> dict[str, Any]:
    records = recent_records(limit)
    grouped: dict[str, dict[str, Any]] = {}
    tables: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "table": "", "queries": 0, "total_ms": 0.0, "max_ms": 0.0, "routes": Counter(),
    })

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

        for sql in record.get("sql_groups") or []:
            for table in sql.get("tables") or ["(não identificada)"]:
                t = tables[table]
                t["table"] = table
                t["queries"] += int(sql.get("count") or 0)
                t["total_ms"] += float(sql.get("total_ms") or 0)
                t["max_ms"] = max(t["max_ms"], float(sql.get("max_ms") or 0))
                t["routes"][key] += int(sql.get("count") or 0)

    ranking = []
    for item in grouped.values():
        calls = max(1, item["calls"])
        item["avg_ms"] = round(item["total_ms"] / calls, 2)
        item["avg_sql_ms"] = round(item["sql_ms"] / calls, 2)
        item["avg_sql_count"] = round(item["sql_count"] / calls, 1)
        item["total_ms"] = round(item["total_ms"], 2)
        item["sql_ms"] = round(item["sql_ms"], 2)
        item["db_pct"] = round((item["sql_ms"] / item["total_ms"] * 100), 1) if item["total_ms"] else 0
        ranking.append(item)
    ranking.sort(key=lambda x: (x["avg_ms"], x["max_ms"]), reverse=True)

    table_ranking = []
    for item in tables.values():
        item["total_ms"] = round(item["total_ms"], 2)
        item["max_ms"] = round(item["max_ms"], 2)
        item["top_routes"] = [r for r, _ in item["routes"].most_common(3)]
        del item["routes"]
        table_ranking.append(item)
    table_ranking.sort(key=lambda x: (x["total_ms"], x["queries"]), reverse=True)

    return {
        "records": records,
        "ranking": ranking,
        "tables": table_ranking[:40],
        "suggestions": _build_suggestions(records),
    }


def monitor_status() -> dict[str, Any]:
    return {
        "enabled": PERFORMANCE_MONITORING,
        "detail": PERFORMANCE_DETAIL,
        "slow_request_ms": PERFORMANCE_SLOW_REQUEST_MS,
        "slow_sql_ms": PERFORMANCE_SLOW_SQL_MS,
        "routes": PERFORMANCE_ROUTES,
        "records_in_memory": len(_recent_records),
        "max_records": PERFORMANCE_MAX_RECORDS,
    }
