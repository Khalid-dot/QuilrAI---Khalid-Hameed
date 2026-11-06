import asyncio
import os
import sqlite3
import sys
from pathlib import Path

import pytest

from model_router.errors import GatewayError
from model_router.limiter import SQLiteTokenLimiter

SRC = str(Path(__file__).resolve().parents[1] / "src")


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


async def test_exact_capacity_tenant_isolation_and_eviction(tmp_path):
    clock = Clock()
    limiter = SQLiteTokenLimiter(tmp_path / "quota.sqlite3", limit=100, clock=clock)
    await limiter.initialize()
    await limiter.reserve("tenant-a", 100)
    assert await limiter.used("tenant-a") == 100
    assert await limiter.used("tenant-b") == 0
    await limiter.reserve("tenant-b", 100)
    clock.now += 59.999
    with pytest.raises(GatewayError) as error:
        await limiter.reserve("tenant-a", 1)
    assert error.value.status == 429 and error.value.retry_after == 1
    clock.now += 0.001
    await limiter.reserve("tenant-a", 1)
    assert await limiter.used("tenant-a") == 1
    with sqlite3.connect(limiter.path) as db:
        assert db.execute("SELECT COUNT(*) FROM token_events").fetchone()[0] == 1


async def test_retry_after_waits_until_enough_tokens_expire(tmp_path):
    clock = Clock()
    limiter = SQLiteTokenLimiter(tmp_path / "quota.sqlite3", limit=100, clock=clock)
    await limiter.initialize()
    await limiter.reserve("tenant", 20)
    clock.now += 10
    await limiter.reserve("tenant", 80)
    clock.now += 10
    with pytest.raises(GatewayError) as error:
        await limiter.reserve("tenant", 50)
    assert error.value.retry_after == 50  # First 20-token expiry is insufficient.


async def test_reconciliation_is_idempotent_and_zero_releases_reservation(tmp_path):
    limiter = SQLiteTokenLimiter(tmp_path / "quota.sqlite3", limit=100)
    await limiter.initialize()
    first = await limiter.reserve("tenant", 80)
    await limiter.settle(first, 30)
    await limiter.settle(first, 0)  # Cannot accidentally refund an already-settled request.
    assert await limiter.used("tenant") == 30
    second = await limiter.reserve("tenant", 70)
    await limiter.settle(second, 0)
    assert await limiter.used("tenant") == 30


async def test_actual_overage_recorded_and_future_requests_blocked(tmp_path):
    limiter = SQLiteTokenLimiter(tmp_path / "quota.sqlite3", limit=100)
    await limiter.initialize()
    reservation = await limiter.reserve("tenant", 90)
    await limiter.settle(reservation, 110)
    assert await limiter.used("tenant") == 110
    with pytest.raises(GatewayError):
        await limiter.reserve("tenant", 1)


async def test_persistence_across_instances_and_restart(tmp_path):
    path = tmp_path / "persistent.sqlite3"
    first = SQLiteTokenLimiter(path, limit=100)
    await first.initialize()
    await first.reserve("tenant", 80)
    second = SQLiteTokenLimiter(path, limit=100)
    await second.initialize()
    assert await second.used("tenant") == 80
    with pytest.raises(GatewayError):
        await second.reserve("tenant", 21)
    assert path.is_file()


async def test_concurrent_independent_connections_cannot_overspend(tmp_path):
    path = tmp_path / "concurrent.sqlite3"
    limiters = [SQLiteTokenLimiter(path, limit=100) for _ in range(20)]
    await limiters[0].initialize()

    async def admit(limiter):
        try:
            await limiter.reserve("tenant", 7)
            return True
        except GatewayError as exc:
            assert exc.status == 429
            return False

    results = await asyncio.gather(*(admit(limiter) for limiter in limiters))
    assert sum(results) == 14
    assert await limiters[0].used("tenant") == 98


async def test_separate_processes_share_one_atomic_budget(tmp_path):
    path = tmp_path / "processes.sqlite3"
    limiter = SQLiteTokenLimiter(path, limit=100)
    await limiter.initialize()
    code = """
import asyncio, sys
from model_router.limiter import SQLiteTokenLimiter
from model_router.errors import GatewayError
async def main():
    try:
        await SQLiteTokenLimiter(sys.argv[1], limit=100).reserve('tenant', 30)
        print('admitted')
    except GatewayError as exc:
        print(exc.code)
asyncio.run(main())
"""
    env = {**os.environ, "PYTHONPATH": SRC}
    processes = await asyncio.gather(
        *[
            asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                code,
                str(path),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            for _ in range(4)
        ]
    )
    results = await asyncio.gather(*(process.communicate() for process in processes))
    assert all(process.returncode == 0 for process in processes)
    assert sum(stdout.strip() == b"admitted" for stdout, _ in results) == 3
    assert sum(stdout.strip() == b"rate_limit_exceeded" for stdout, _ in results) == 1
    assert await limiter.used("tenant") == 90


async def test_settling_evicted_reservation_does_not_resurrect_usage(tmp_path):
    clock = Clock()
    limiter = SQLiteTokenLimiter(tmp_path / "quota.sqlite3", limit=100, clock=clock)
    await limiter.initialize()
    old = await limiter.reserve("tenant", 50)
    clock.now += 60
    await limiter.reserve("tenant", 10)
    await limiter.settle(old, 40)
    assert await limiter.used("tenant") == 10


@pytest.mark.parametrize("amount", [0, -1, True, 1.5, "10"])
async def test_bad_reservations_rejected(tmp_path, amount):
    limiter = SQLiteTokenLimiter(tmp_path / "quota.sqlite3")
    with pytest.raises(ValueError):
        await limiter.reserve("tenant", amount)


async def test_request_exceeding_total_capacity_rejected_before_database_access(tmp_path):
    limiter = SQLiteTokenLimiter(tmp_path / "quota.sqlite3", limit=100)
    with pytest.raises(GatewayError) as error:
        await limiter.reserve("tenant", 101)
    assert error.value.status == 422
    assert not limiter.path.exists()


async def test_database_errors_fail_closed_and_are_sanitized(tmp_path):
    limiter = SQLiteTokenLimiter(tmp_path / "nonexistent" / "quota.sqlite3")
    with pytest.raises(GatewayError) as error:
        await limiter.reserve("tenant", 1)
    assert error.value.code == "storage_unavailable"
    assert str(tmp_path) not in error.value.message


def test_in_memory_database_disallowed():
    with pytest.raises(ValueError):
        SQLiteTokenLimiter(":memory:")
