"""
test_database_service.py — Unit Tests for PostgreSQL Database Operations
========================================================================
MOTIVE OF THIS TEST FILE:
-------------------------
In production, every invoice extraction job is persisted to PostgreSQL via SQLAlchemy ORM.
The functions in `app/services/db_invoice.py` are responsible for:
  1. `create_invoice_record`: Inserting a new `InvoiceRecord` linked to `user_id`.
  2. `get_invoice_record`: Finding a record by UUID (used for user authorization checks).
  3. `list_user_invoices`: Returning paginated history for the `/history` endpoint.

WHY WE USE AsyncMock HERE (AND A KEY DISTINCTION WITH SQLAlchemy!):
-------------------------------------------------------------------
Notice line 42 below:
    mock_session.add = MagicMock()       # ← Notice: MagicMock, NOT AsyncMock!
    mock_session.commit = AsyncMock()    # ← Notice: AsyncMock!
    mock_session.refresh = AsyncMock()   # ← Notice: AsyncMock!

WHY THE DIFFERENCE?
  - `session.add(record)` in SQLAlchemy is a synchronous in-memory operation (it stages the
    object in the session unit-of-work, no SQL is sent yet). It is NOT called with `await`.
    Therefore, `session.add` is a standard `MagicMock()`.

  - `await session.commit()` and `await session.refresh(record)` actually communicate with
    PostgreSQL over TCP (sending SQL statements and receiving DB generated keys).
    These MUST be awaited.
    Therefore, `commit` and `refresh` MUST be `AsyncMock()`!
    If they were `MagicMock`, Python would crash with `TypeError: object can't be used in 'await'`.

By faking the `AsyncSession`, we test all our SQL queries and data mappings in milliseconds
without running a PostgreSQL container or spinning up cloud infrastructure!
"""

import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.models.auth import CurrentUser
from app.services.db_invoice import (
    create_invoice_record,
    get_invoice_record,
    list_user_invoices,
)


# ---------------------------------------------------------------------------
# 1. RECORD CREATION TEST
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_invoice_record():
    """
    MOTIVE: Verify that `create_invoice_record`:
      1. Instantiates an `InvoiceRecord` with all supplied arguments.
      2. Associates it with the user's permanent `object_id`.
      3. Stages it via `session.add()`.
      4. Persists it via `await session.commit()`.
      5. Refreshes the instance via `await session.refresh()`.
    """
    mock_session = AsyncMock()
    mock_session.add = MagicMock()       # Synchronous staging in SQLAlchemy
    mock_session.commit = AsyncMock()    # Asynchronous database I/O
    mock_session.refresh = AsyncMock()   # Asynchronous database I/O

    user = CurrentUser(
        object_id="user-123",
        upn="user@company.com",
        name="User Name",
    )
    task_id = uuid.uuid4()

    record = await create_invoice_record(
        db=mock_session,
        task_id=task_id,
        user=user,
        filename="test.pdf",
        file_hash="hash123",
        file_size_bytes=1024,
        content_type="application/pdf",
        status="processing",
    )

    # 1. Assert fields were set correctly on the ORM model
    assert record.task_id == task_id
    assert record.user_id == "user-123"
    assert record.filename == "test.pdf"

    # 2. Assert the database lifecycle was executed in exact order
    mock_session.add.assert_called_once()
    mock_session.commit.assert_awaited_once()
    mock_session.refresh.assert_awaited_once()


# ---------------------------------------------------------------------------
# 2. SINGLE RECORD RETRIEVAL TEST
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_invoice_record():
    """
    MOTIVE: Verify that `get_invoice_record` properly queries the database and
            returns the matching InvoiceRecord.
    -----------------------------------------------------------------------------
    THE PRODUCTION CODE WE ARE TESTING (from app/services/db_invoice.py):
        async def get_invoice_record(db: AsyncSession, task_id: uuid.UUID):
            query = select(InvoiceRecord).where(InvoiceRecord.task_id == task_id)
            res = await db.execute(query)          # Step 1: Execute query against DB
            return res.scalar_one_or_none()        # Step 2: Extract 1 record (or None)

    WHY USE mock_session?
      `get_invoice_record` requires an `AsyncSession` as its first parameter (`db`).
      In production, FastAPI injects a real connection to PostgreSQL.
      In unit tests, we don't want a live database. So we create `mock_session = AsyncMock()`.

    THE 3 MOCK OBJECTS INVOLVED:
      1. `mock_session`: Fakes the DB connection (`db`).
      2. `mock_result`:  Fakes the SQLAlchemy `Result` object returned by `db.execute()`.
      3. `mock_record`:  Fakes the `InvoiceRecord` row found in the table.
    """
    # 1. SETUP: Wire the mock return values to simulate SQLAlchemy's flow
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_record = MagicMock()

    # Step 1 simulation: When `await db.execute(...)` is called, return `mock_result`
    mock_session.execute.return_value = mock_result

    # Step 2 simulation: When `res.scalar_one_or_none()` is called, return `mock_record`
    mock_result.scalar_one_or_none.return_value = mock_record

    # 2. EXECUTION: Call our real function with the fake session
    task_id = uuid.uuid4()
    record = await get_invoice_record(db=mock_session, task_id=task_id)

    # 3. WHAT ARE WE COMPARING?
    # Comparison 1: Did `get_invoice_record` faithfully return the record that the DB returned?
    #               If there was a bug (e.g. code returned None or wrong variable), this fails.
    assert record == mock_record

    # Comparison 2: Did our function actually send the query to PostgreSQL and await it?
    #               If someone commented out `db.execute()` or forgot `await`, this fails.
    mock_session.execute.assert_awaited_once()


# ---------------------------------------------------------------------------
# 3. PAGINATED HISTORY RETRIEVAL TEST
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_user_invoices():
    """
    MOTIVE: Verify that `list_user_invoices` queries the database for a user's
            invoices and correctly unpacks the rows into a Python list.
    -----------------------------------------------------------------------------
    THE PRODUCTION CODE WE ARE TESTING (from app/services/db_invoice.py):
        async def list_user_invoices(db: AsyncSession, user_id: str, limit: int, offset: int):
            query = select(InvoiceRecord).where(...).order_by(...).limit(limit).offset(offset)
            res = await db.execute(query)          # Step 1: Execute query
            return list(res.scalars().all())       # Step 2: Unpack rows and return list

    WHY mock_result.scalars.return_value.all.return_value?
      Look at the method chaining in Step 2: `res.scalars().all()`
        - `res.scalars()` is called on `mock_result`
          -> returns an intermediate mock (`mock_result.scalars.return_value`)
        - `.all()` is called on that intermediate mock
          -> returns the final list (`...all.return_value = mock_records`)
    """
    # 1. SETUP: Create fake session, fake result, and 2 fake invoice records
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_records = [MagicMock(), MagicMock()]  # Simulate 2 invoice records in the DB

    # Wire up the SQLAlchemy 2.0 query execution chain:
    # `await db.execute()` -> returns `mock_result`
    mock_session.execute.return_value = mock_result
    # `mock_result.scalars().all()` -> returns our list of 2 records
    mock_result.scalars.return_value.all.return_value = mock_records

    # 2. EXECUTION: Call our real function
    records = await list_user_invoices(db=mock_session, user_id="user-123", limit=10, offset=0)

    # 3. WHAT ARE WE COMPARING?
    # Comparison 1: Did our function return the list of records from the database?
    assert records == mock_records
    assert len(records) == 2

    # Comparison 2: Did our function actually execute the query against the database?
    mock_session.execute.assert_awaited_once()
