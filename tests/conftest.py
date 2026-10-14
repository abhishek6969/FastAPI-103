"""
conftest.py — Pytest Global Fixtures & Dependency Overrides
============================================================
HOW FASTAPI DEPENDENCY INJECTION MAKES TESTING EASY:
---------------------------------------------------
In production, endpoint functions declare dependencies via `Depends(...)`:
    @router.post("/extract")
    async def extract_invoice(
        current_user: CurrentUser = Depends(get_current_user), # validates Entra JWT
        db: AsyncSession = Depends(get_db),                    # connects to live PostgreSQL
        settings: Settings = Depends(get_settings),            # loads production .env
    ):

In unit tests, we DO NOT want to:
  1. Call Microsoft Entra ID over the internet to validate dummy tokens.
  2. Require a running PostgreSQL server or Docker container just to run unit tests.
  3. Risk mutating production or staging database state.

FastAPI solves this cleanly via `app.dependency_overrides`:
It is a dictionary mapping the original dependency function (e.g. `get_current_user`)
to a replacement callable (e.g. `lambda: mock_user`).

When `TestClient` executes a request against an endpoint, FastAPI checks
`app.dependency_overrides` FIRST. If found, it executes the override instead of
the real dependency function!
"""

import pytest
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient

from app.main import app
from app.models.auth import CurrentUser
from app.services.auth import get_current_user
from app.database import get_db
from app.config import get_settings, Settings


# ---------------------------------------------------------------------------
# 1. MOCK USER FIXTURES (Bypassing Microsoft Entra ID)
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_user() -> CurrentUser:
    """
    Simulates a successfully authenticated user who presented a valid Entra token.
    
    Instead of calling Entra's JWKS endpoint and decoding a cryptographic RS256 token,
    we directly construct the Pydantic `CurrentUser` object that `get_current_user()`
    would have returned after validation.
    """
    return CurrentUser(
        object_id="00000000-0000-0000-0000-000000000001",
        upn="developer@company.com",
        name="Test Developer",
        roles=["Invoice.Contributor"],
        scopes=["invoices.extract"],
    )


@pytest.fixture
def other_user() -> CurrentUser:
    """
    Simulates a second, different authenticated user.
    Used for testing multi-tenant security boundaries — e.g. verifying that User B
    receives an HTTP 403 Forbidden when trying to inspect an invoice submitted by User A.
    """
    return CurrentUser(
        object_id="00000000-0000-0000-0000-000000000002",
        upn="stranger@company.com",
        name="Other User",
        roles=["Invoice.Reader"],
        scopes=["invoices.extract"],
    )


# ---------------------------------------------------------------------------
# 2. MOCK DATABASE FIXTURE (Bypassing Live PostgreSQL)
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_db_session() -> AsyncMock:
    """
    Simulates a SQLAlchemy `AsyncSession` without connecting to PostgreSQL.

    WHAT DOES AsyncMock DO BASICALLY? (The Plain English Mental Model)
    -------------------------------------------------------------------
    Think of `MagicMock` as a fake regular function:
        def fake_sync_function():
            return "some_value"

    And think of `AsyncMock` as a fake `async def` coroutine:
        async def fake_coroutine():
            return "some_value"

    WHY DOES THIS MATTER IN FASTAPI?
    --------------------------------
    Our production code uses `await` everywhere:
        await db.commit()
        await redis.get("key")
        await create_invoice_record(...)

    Python's syntax rules say: you can ONLY `await` something that is an async coroutine.
    - If you pass a regular `MagicMock`:
        await mock_session.commit()
        # 💥 CRASH: TypeError: object MagicMock can't be used in 'await' expression
        # Python refuses to await it because MagicMock is synchronous!

    - If you pass an `AsyncMock`:
        await mock_session.commit()
        # ✅ SUCCESS: It executes instantly, returns without network delays,
        # and records that it was awaited so you can test:
        mock_session.commit.assert_awaited_once()

    WHAT AsyncMock LETS YOU CONTROL:
    1. Return value when awaited:
       mock_redis.get.return_value = '{"status": "done"}'
    2. Errors raised when awaited:
       mock_db.commit.side_effect = ConnectionError("DB is down")
    3. Assertions:
       mock_db.commit.assert_awaited_once()
       mock_db.commit.assert_awaited_with()
    """
    session = AsyncMock()
    session.add = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.refresh = AsyncMock()
    session.close = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# 3. TEST CONFIGURATION FIXTURE
# ---------------------------------------------------------------------------

@pytest.fixture
def test_settings() -> Settings:
    """
    Provides isolated test settings with mock credentials.
    Guarantees that unit tests do not accidentally read or write to live cloud services.
    """
    return Settings(
        azure_document_intelligence_endpoint="https://mock-docintel.cognitiveservices.azure.com/",
        azure_document_intelligence_key="dummy_mock_key_12345",
        redis_url="redis://localhost:6379",
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/invoice_db",
        azure_tenant_id="ae16116c-272b-4b41-bcde-faae7cbd75fd",
        azure_client_id="8d9daf82-b864-43de-b499-db1d8c3991d6",
        app_env="test",
        running_local=True,
    )


# ---------------------------------------------------------------------------
# 4. TEST CLIENT WITH OVERRIDES (The Core Dependency Wiring)
# ---------------------------------------------------------------------------

@pytest.fixture
def client(mock_user: CurrentUser, mock_db_session: AsyncMock, test_settings: Settings):
    """
    Instantiates a FastAPI `TestClient` with all external dependencies replaced:

    1. `get_current_user` -> returns `mock_user` (no Entra JWT needed).
    2. `get_db`           -> yields `mock_db_session` (no PostgreSQL server needed).
    3. `get_settings`     -> returns `test_settings` (no local .env dependencies).

    TEARDOWN:
    `app.dependency_overrides.clear()` is called after each test finishes,
    ensuring state does not leak between different test files.
    """
    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_db] = lambda: mock_db_session
    app.dependency_overrides[get_settings] = lambda: test_settings

    with TestClient(app) as test_client:
        yield test_client

    # Clean up overrides so subsequent tests start with a clean slate
    app.dependency_overrides.clear()
