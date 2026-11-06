"""On-disk, transactional sliding window. SQLite I/O stays off the event loop."""

import asyncio
import math
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from model_router.errors import GatewayError


@dataclass(frozen=True)
class Reservation:
    id: str
    tokens: int


class SQLiteTokenLimiter:
    def __init__(
        self,
        path: str | Path,
        *,
        limit: int = 50_000,
        window_seconds: float = 60,
        clock: Callable[[], float] = time.time,
    ):
        if str(path) == ":memory:":
            raise ValueError("An on-disk SQLite path is required")
        if type(limit) is not int or limit <= 0 or not math.isfinite(window_seconds):
            raise ValueError("Invalid quota configuration")
        if window_seconds <= 0:
            raise ValueError("Window must be positive")
        self.path = Path(path)
        self.limit = limit
        self.window = window_seconds
        self.clock = clock

    def _connect(self):
        return sqlite3.connect(self.path, timeout=5, isolation_level=None)

    async def initialize(self):
        try:
            await asyncio.to_thread(self._initialize)
        except (sqlite3.Error, OSError):
            raise GatewayError("storage_unavailable", "Token accounting unavailable", 503) from None

    def _initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS token_events (
                id TEXT PRIMARY KEY,
                tenant TEXT NOT NULL,
                admitted_at REAL NOT NULL,
                tokens INTEGER NOT NULL CHECK(tokens >= 0),
                settled INTEGER NOT NULL DEFAULT 0 CHECK(settled IN (0, 1))
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS events_time ON token_events(admitted_at)")
            db.execute(
                "CREATE INDEX IF NOT EXISTS events_tenant_time ON token_events(tenant, admitted_at)"
            )

    async def reserve(self, tenant: str, tokens: int) -> Reservation:
        if type(tokens) is not int or tokens <= 0:
            raise ValueError("Reservation must be a positive integer")
        if tokens > self.limit:
            raise GatewayError(
                "request_exceeds_quota", "Request exceeds tenant token capacity", 422
            )
        try:
            return await asyncio.to_thread(self._reserve, tenant, tokens)
        except (sqlite3.Error, OSError):
            raise GatewayError("storage_unavailable", "Token accounting unavailable", 503) from None

    def _reserve(self, tenant: str, tokens: int) -> Reservation:
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                now = self.clock()  # After obtaining the write lock, not before queueing for it.
                db.execute("DELETE FROM token_events WHERE admitted_at <= ?", (now - self.window,))
                rows = db.execute(
                    "SELECT admitted_at, tokens FROM token_events WHERE tenant = ? "
                    "ORDER BY admitted_at, id",
                    (tenant,),
                ).fetchall()
                used = sum(row[1] for row in rows)
                if used + tokens > self.limit:
                    needed = used + tokens - self.limit
                    freed = 0
                    retry = 1
                    for timestamp, count in rows:
                        freed += count
                        if freed >= needed:
                            retry = max(1, math.ceil(timestamp + self.window - now))
                            break
                    db.commit()  # Persist eviction even when admission is denied.
                    raise GatewayError(
                        "rate_limit_exceeded",
                        "Tenant token rate limit exceeded",
                        429,
                        retry_after=retry,
                    )
                reservation = Reservation(uuid4().hex, tokens)
                db.execute(
                    "INSERT INTO token_events(id, tenant, admitted_at, tokens) VALUES (?, ?, ?, ?)",
                    (reservation.id, tenant, now, tokens),
                )
                db.commit()
                return reservation
            except BaseException:
                db.rollback()
                raise

    async def settle(self, reservation: Reservation, actual_tokens: int):
        if type(actual_tokens) is not int or actual_tokens < 0:
            raise ValueError("Usage must be a nonnegative integer")
        try:
            await asyncio.to_thread(self._settle, reservation, actual_tokens)
        except (sqlite3.Error, OSError):
            raise GatewayError("storage_unavailable", "Token accounting unavailable", 503) from None

    def _settle(self, reservation: Reservation, actual_tokens: int):
        with closing(self._connect()) as db:
            # Single conditional writes are atomic and make settlement idempotent.
            if actual_tokens == 0:
                db.execute(
                    "DELETE FROM token_events WHERE id = ? AND settled = 0", (reservation.id,)
                )
                return
            db.execute(
                "UPDATE token_events SET tokens = ?, settled = 1 WHERE id = ? AND settled = 0",
                (actual_tokens, reservation.id),
            )

    async def used(self, tenant: str) -> int:
        try:
            return await asyncio.to_thread(self._used, tenant)
        except (sqlite3.Error, OSError):
            raise GatewayError("storage_unavailable", "Token accounting unavailable", 503) from None

    def _used(self, tenant: str) -> int:
        with closing(self._connect()) as db:
            return db.execute(
                "SELECT COALESCE(SUM(tokens), 0) FROM token_events "
                "WHERE tenant = ? AND admitted_at > ?",
                (tenant, self.clock() - self.window),
            ).fetchone()[0]
