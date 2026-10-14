"""
test_cache_service.py — Unit Tests for Redis Caching Layer
==========================================================
MOTIVE OF THIS TEST FILE:
-------------------------
In production, Redis provides two critical performance guarantees:
  1. `invoice:{sha256}`: Result caching with 24-hour TTL (zero Azure costs on duplicate PDFs).
  2. `task:{uuid}`: Status polling tracking with 1-hour TTL.

WHY WE USE AsyncMock HERE:
--------------------------
Our Redis client is `redis.asyncio.Redis` (async I/O). Every call in `app/services/cache.py` is awaited:
    raw = await redis.get(f"task:{task_id}")
    await redis.setex(name=..., time=..., value=...)

If we tried to use a standard synchronous `MagicMock()`:
    await mock_redis.get(...)
    # 💥 CRASH: TypeError: object MagicMock can't be used in 'await' expression!

Because `redis.get` is awaited by our application, we MUST mock `redis` with `AsyncMock()`.
An `AsyncMock()` acts like a fake `async def` function:
  - When Python executes `await redis.get(...)`, `AsyncMock` completes instantly without opening a TCP socket.
  - We can instruct it what to return: `mock_redis.get.return_value = '{"some": "json"}'`.
  - We can verify it was awaited with the exact expected arguments:
    `mock_redis.setex.assert_awaited_once_with(name=..., time=..., value=...)`.
"""

import json
import pytest
from unittest.mock import patch, AsyncMock
from app.services.cache import (
    get_cached_result,
    store_cached_result,
    set_task_status,
    get_task_status,
    RESULT_TTL_SECONDS,
    TASK_TTL_SECONDS,
)


# ---------------------------------------------------------------------------
# 1. INVOICE RESULT CACHE HIT
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch("app.services.cache.get_redis")
async def test_get_cached_result_hit(mock_get_redis):
    """
    MOTIVE: Verify that when Redis has a cached invoice for a file hash,
            `get_cached_result` deserializes the JSON string and returns a Python dict.
    ---------------------------------------------------------------------------------
    WHAT IS GOING ON:
      1. We patch `get_redis` to return our fake `mock_redis` (an AsyncMock).
      2. We set `mock_redis.get.return_value = json.dumps(...)` to pretend Redis found the key.
      3. We call `await get_cached_result("mock_hash_123")`.
      4. We verify the returned object is a parsed Python dict.
      5. We verify `mock_redis.get` was awaited with the key `"invoice:mock_hash_123"`.
    """
    mock_redis = AsyncMock()
    # Redis stores strings. When redis.get() is awaited, it returns a JSON string:
    mock_redis.get.return_value = json.dumps({"vendor_name": "Azure Corp", "total": 100})
    mock_get_redis.return_value = mock_redis

    result = await get_cached_result("mock_hash_123")

    assert result == {"vendor_name": "Azure Corp", "total": 100}
    # Verify the code queried the exact expected Redis key name:
    mock_redis.get.assert_awaited_once_with("invoice:mock_hash_123")


# ---------------------------------------------------------------------------
# 2. INVOICE RESULT CACHE MISS
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch("app.services.cache.get_redis")
async def test_get_cached_result_miss(mock_get_redis):
    """
    MOTIVE: Verify that when Redis does NOT have the key, `get_cached_result` returns None.
    ---------------------------------------------------------------------------------------
    WHAT IS GOING ON:
      In Redis, a missing key returns `None`.
      We configure `mock_redis.get.return_value = None`.
      The application must interpret this as a cache miss and return `None` (which tells
      the router to trigger a new Azure Document Intelligence job).
    """
    mock_redis = AsyncMock()
    mock_redis.get.return_value = None  # Key does not exist in Redis
    mock_get_redis.return_value = mock_redis

    result = await get_cached_result("unknown_hash")
    assert result is None


# ---------------------------------------------------------------------------
# 3. STORING RESULTS WITH TTL
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch("app.services.cache.get_redis")
async def test_store_cached_result(mock_get_redis):
    """
    MOTIVE: Verify that when storing an invoice result in Redis, the service uses `SETEX`
            with the exact 24-hour TTL constant and valid JSON serialization.
    ---------------------------------------------------------------------------------------
    WHAT IS GOING ON:
      We pass a Python dict to `store_cached_result`.
      We assert that `mock_redis.setex` was awaited with:
        - name: `"invoice:sample_hash"`
        - time: `RESULT_TTL_SECONDS` (86400 seconds / 24 hours)
        - value: serialised JSON string
    """
    mock_redis = AsyncMock()
    mock_get_redis.return_value = mock_redis

    sample_result = {"vendor_name": "Contoso", "total": 250.0}
    await store_cached_result("sample_hash", sample_result)

    # Ensure SETEX was called atomically with the 24-hour expiration:
    mock_redis.setex.assert_awaited_once_with(
        name="invoice:sample_hash",
        time=RESULT_TTL_SECONDS,
        value=json.dumps(sample_result),
    )


# ---------------------------------------------------------------------------
# 4. TASK STATUS TRACKING (POLLING FLOW)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch("app.services.cache.get_redis")
async def test_set_and_get_task_status(mock_get_redis):
    """
    MOTIVE: Verify the async polling lifecycle — storing a task's state in Redis
            and reading it back when the client polls `GET /invoices/status/{task_id}`.
    ---------------------------------------------------------------------------------
    WHAT IS GOING ON:
      1. We test `set_task_status`: verifying it serializes `{"status": "done", "result": ...}`
         and saves it with a 1-hour TTL (`TASK_TTL_SECONDS`).
      2. We test `get_task_status`: verifying it parses the payload back into a Python dict.
    """
    mock_redis = AsyncMock()
    mock_get_redis.return_value = mock_redis

    # Step 1: Test writing task status
    await set_task_status("task-123", status="done", result={"vendor": "ABC"})
    mock_redis.setex.assert_awaited_once_with(
        name="task:task-123",
        time=TASK_TTL_SECONDS,
        value=json.dumps({"status": "done", "result": {"vendor": "ABC"}}),
    )

    # Step 2: Test reading task status back
    mock_redis.get.return_value = json.dumps({"status": "done", "result": {"vendor": "ABC"}})
    status_data = await get_task_status("task-123")
    assert status_data["status"] == "done"
    assert status_data["result"] == {"vendor": "ABC"}
