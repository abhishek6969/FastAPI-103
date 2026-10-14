"""
Redis Cache Service
===================
WHY REDIS HERE?
---------------
After Azure Document Intelligence processes an invoice, we store the result
in Redis so the same file never hits Azure again.

Why Redis and not a SQL database?
  - Results are temporary — we don't need to keep them forever
  - Redis is in-memory → microsecond reads (vs. milliseconds for Postgres/SQL)
  - Redis has native TTL (time-to-live) — cache entries auto-expire, no cleanup job needed
  - Redis is the standard choice for caching + async task result storage in Python web stacks

TWO KEY PATTERNS USED HERE:
  1. invoice:{sha256_hash}  → cached extraction result (TTL: 24 hours)
     Keyed by file content hash, so the same invoice uploaded twice = one Azure call

  2. task:{uuid}            → background task status + result (TTL: 1 hour)
     Keyed by task ID returned to the client, so they can poll for completion

WHY ASYNC REDIS (redis.asyncio)?
---------------------------------
Our FastAPI route handlers are `async def`. If we used the sync Redis client
inside an async function and the Redis call took even 1ms, it would block the
entire event loop — meaning no other requests can be served during that time.

redis.asyncio wraps Redis operations as coroutines, so the event loop can
handle other requests while waiting for Redis to respond.
"""

import json
import redis.asyncio as aioredis
from app.config import get_settings

# TTL constants — clear intent, easy to change
RESULT_TTL_SECONDS = 60 * 60 * 24   # 24 hours for cached invoice results
TASK_TTL_SECONDS   = 60 * 60        # 1 hour for task status entries

# Module-level client — created once, reused across all requests.
# redis.asyncio uses a connection pool internally, so this is safe and efficient.
_redis_client: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    """
    Returns a shared Redis client (singleton pattern).

    WHY NOT CREATE A NEW CLIENT PER REQUEST?
    Each new client opens a TCP connection to Redis. Opening connections is
    expensive (handshake, memory allocation). A shared client with a connection
    pool reuses existing connections — much more efficient at scale.

    decode_responses=True makes Redis return Python strings instead of bytes.
    Without it, you'd get b"some string" everywhere and have to decode manually.
    """
    global _redis_client
    if _redis_client is None:
        settings = get_settings()
        _redis_client = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,  # return str, not bytes
        )
    return _redis_client


# ---------------------------------------------------------------------------
# INVOICE RESULT CACHE (keyed by file content hash)
# ---------------------------------------------------------------------------

async def get_cached_result(file_hash: str) -> dict | None:
    """
    Check if this exact file has been processed before.

    file_hash is a SHA-256 digest of the uploaded file bytes.
    Same file content = same hash = same cached result.

    Returns the cached result dict, or None if not found (cache miss).
    """
    redis = await get_redis()
    cached = await redis.get(f"invoice:{file_hash}")
    if cached:
        return json.loads(cached)   # Redis stores strings; we stored JSON
    return None


async def store_cached_result(file_hash: str, result: dict) -> None:
    """
    Store extraction result in Redis with a 24-hour TTL.

    SETEX = SET + EXPIRE in one atomic command.
    After 24h, Redis automatically deletes the key — no cleanup job needed.

    We store the full result dict as a JSON string. Redis is a key-value store;
    complex objects must be serialised to a string first.
    """
    redis = await get_redis()
    await redis.setex(
        name=f"invoice:{file_hash}",
        time=RESULT_TTL_SECONDS,
        value=json.dumps(result),
    )


# ---------------------------------------------------------------------------
# TASK STATUS (keyed by task UUID)
# ---------------------------------------------------------------------------

async def set_task_status(task_id: str, status: str, result: dict | None = None, error: str | None = None) -> None:
    """
    Stores the current status of a background task.

    status values:
      "processing" — Azure call is in progress
      "done"       — result is ready
      "failed"     — Azure call failed, error contains the reason

    TTL is 1 hour — the client should poll and retrieve the result well within that.
    After 1h the task entry is gone; polling a stale task_id returns 404.
    """
    redis = await get_redis()
    payload = {"status": status}
    if result is not None:
        payload["result"] = result
    if error is not None:
        payload["error"] = error

    await redis.setex(
        name=f"task:{task_id}",
        time=TASK_TTL_SECONDS,
        value=json.dumps(payload),
    )


async def get_task_status(task_id: str) -> dict | None:
    """
    Retrieves task status by ID.
    Returns None if the task doesn't exist or has expired.
    """
    redis = await get_redis()
    raw = await redis.get(f"task:{task_id}")
    if raw:
        return json.loads(raw)
    return None
