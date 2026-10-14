"""
test_invoices.py — Invoice Endpoint Tests & Mocking Explanations
================================================================
KEY CONCEPTS DEMONSTRATED IN THIS FILE:
---------------------------------------
1. @patch("module.function", new_callable=AsyncMock)
   - When our router calls `await get_cached_result(file_hash)`, we intercept it.
   - WHERE TO PATCH: Patch where the function is LOOKED UP, not where it is defined!
     Because `app.routers.invoices` did `from app.services.cache import get_cached_result`,
     we patch `"app.routers.invoices.get_cached_result"`.
   - `new_callable=AsyncMock`: Tells unittest.mock that the target is an `async def`
     coroutine so `await target()` executes cleanly without runtime errors.

2. ARGUMENT ORDER IN PYTEST WHEN USING MULTIPLE @patch DECORATORS:
   - Decorators are applied from bottom to top!
   - Therefore, parameters are received in bottom-to-top order:
       @patch("...function_A")  # -> received as 3rd parameter
       @patch("...function_B")  # -> received as 2nd parameter
       @patch("...function_C")  # -> received as 1st parameter
       def test_example(mock_C, mock_B, mock_A, client): ...
"""

import uuid
from unittest.mock import patch, AsyncMock
from app.config import Settings


# ---------------------------------------------------------------------------
# 1. VALIDATION TESTS (Synchronous HTTP Layer Rules)
# ---------------------------------------------------------------------------

def test_extract_unsupported_file_type(client):
    """
    HTTP 415 Unsupported Media Type:
    The API only accepts PDFs and images (PNG, JPEG, TIFF).
    Passing text/plain should be rejected before reading or hashing file content.
    """
    files = {"file": ("test.txt", b"plain text", "text/plain")}
    response = client.post("/invoices/extract", files=files)
    assert response.status_code == 415
    assert "Unsupported file type" in response.json()["detail"]


def test_extract_empty_file(client):
    """
    HTTP 400 Bad Request:
    Uploading a 0-byte file cannot be processed by OCR models and is rejected immediately.
    """
    files = {"file": ("empty.pdf", b"", "application/pdf")}
    response = client.post("/invoices/extract", files=files)
    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()


def test_extract_missing_azure_credentials(client):
    """
    HTTP 503 Service Unavailable:
    If cloud credentials are unset, the service fails fast with an explanatory 503
    rather than crashing during background task execution.
    """
    from app.config import get_settings
    from app.main import app

    # Temporarily override settings with blank credentials
    unconfigured_settings = Settings(
        azure_document_intelligence_endpoint="",
        azure_document_intelligence_key="",
        app_env="test",
    )
    app.dependency_overrides[get_settings] = lambda: unconfigured_settings

    files = {"file": ("invoice.pdf", b"%PDF-1.4 dummy", "application/pdf")}
    response = client.post("/invoices/extract", files=files)
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]


# ---------------------------------------------------------------------------
# 2. CACHE MISS FLOW (Full Processing Trigger)
# ---------------------------------------------------------------------------

@patch("app.routers.invoices._run_extraction", new_callable=AsyncMock)
@patch("app.routers.invoices.get_cached_result", new_callable=AsyncMock)
@patch("app.routers.invoices.set_task_status", new_callable=AsyncMock)
@patch("app.routers.invoices.create_invoice_record", new_callable=AsyncMock)
def test_extract_cache_miss_accepted(
    mock_create_db,          # corresponds to create_invoice_record
    mock_set_status,         # corresponds to set_task_status
    mock_get_cached,         # corresponds to get_cached_result
    mock_run_extraction,     # prevents background task from running real cloud/DB calls
    client,
):
    """
    CACHE MISS WORKFLOW:
    1. Check Redis: `get_cached_result` returns None.
    2. Write to Redis: sets status to "processing".
    3. Write to PostgreSQL: `create_invoice_record` persists initial task state.
    4. HTTP Response: Returns HTTP 202 Accepted with a unique task_id in <50ms.
    """
    # 1. Simulate Redis returning a cache miss
    mock_get_cached.return_value = None

    files = {"file": ("invoice.pdf", b"%PDF-1.4 test invoice content", "application/pdf")}
    response = client.post("/invoices/extract", files=files)

    # 2. Assert HTTP 202 status and response schema
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "processing"
    assert data["cache_hit"] is False
    assert "task_id" in data
    uuid.UUID(data["task_id"])  # confirms valid UUID format

    # 3. Assert Redis and Database functions were called
    mock_set_status.assert_awaited_once()
    mock_create_db.assert_awaited_once()


# ---------------------------------------------------------------------------
# 3. CACHE HIT FLOW (Instant In-Memory Return)
# ---------------------------------------------------------------------------

@patch("app.routers.invoices.get_cached_result", new_callable=AsyncMock)
@patch("app.routers.invoices.set_task_status", new_callable=AsyncMock)
@patch("app.routers.invoices.create_invoice_record", new_callable=AsyncMock)
def test_extract_cache_hit_returns_cached_result(
    mock_create_db,
    mock_set_status,
    mock_get_cached,
    client,
):
    """
    CACHE HIT WORKFLOW:
    1. Check Redis: `get_cached_result` returns previously extracted JSON.
    2. Zero Azure Cost: No background task is scheduled to call Document Intelligence.
    3. HTTP Response: Returns HTTP 202 with status="done" and cache_hit=True immediately.
    """
    # Simulate Redis returning an existing cached extraction result
    mock_get_cached.return_value = {
        "fields": {
            "vendor_name": "Microsoft Ireland",
            "total": 120.50,
            "currency": "EUR",
            "invoice_date": "2026-09-01",
        },
        "confidence": 0.95,
        "line_items": [],
    }

    files = {"file": ("invoice.pdf", b"%PDF-1.4 duplicate invoice", "application/pdf")}
    response = client.post("/invoices/extract", files=files)

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "done"
    assert data["cache_hit"] is True
    assert "task_id" in data

    mock_set_status.assert_awaited_once()
    mock_create_db.assert_awaited_once()


# ---------------------------------------------------------------------------
# 4. TASK STATUS POLLING & SECURITY BOUNDARIES
# ---------------------------------------------------------------------------

def test_get_status_invalid_uuid(client):
    """Non-UUID parameter is caught and returns 400 before querying DB/Redis."""
    response = client.get("/invoices/status/not-a-valid-uuid")
    assert response.status_code == 400
    assert "Invalid task_id format" in response.json()["detail"]


@patch("app.routers.invoices.get_invoice_record", new_callable=AsyncMock)
@patch("app.routers.invoices.get_task_status", new_callable=AsyncMock)
def test_get_status_not_found(mock_get_redis, mock_get_db, client):
    """Returns 404 when task is absent from both Redis cache and PostgreSQL."""
    mock_get_redis.return_value = None
    mock_get_db.return_value = None

    task_id = str(uuid.uuid4())
    response = client.get(f"/invoices/status/{task_id}")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@patch("app.routers.invoices.get_invoice_record", new_callable=AsyncMock)
def test_get_status_forbidden_for_other_user(mock_get_db, client):
    """
    SECURITY BOUNDARY:
    A user must NOT be able to view another user's invoice status.
    If the invoice record's user_id does not match the token's object_id,
    the endpoint raises HTTP 403 Forbidden.
    """
    mock_record = AsyncMock()
    mock_record.user_id = "different-user-oid-999"  # belongs to someone else
    mock_get_db.return_value = mock_record

    task_id = str(uuid.uuid4())
    response = client.get(f"/invoices/status/{task_id}")
    assert response.status_code == 403
    assert "permission" in response.json()["detail"].lower()


@patch("app.routers.invoices.get_invoice_record", new_callable=AsyncMock)
@patch("app.routers.invoices.get_task_status", new_callable=AsyncMock)
def test_get_status_success_from_redis(mock_get_redis, mock_get_db, client, mock_user):
    """Successful poll retrieves status and structured invoice result."""
    task_uuid = uuid.uuid4()
    task_id = str(task_uuid)

    # Ownership check passes (record user_id matches authenticated mock_user)
    mock_record = AsyncMock()
    mock_record.user_id = mock_user.object_id
    mock_get_db.return_value = mock_record

    mock_get_redis.return_value = {
        "status": "done",
        "result": {
            "filename": "invoice.pdf",
            "status": "success",
            "confidence": 0.98,
            "raw_page_count": 1,
            "fields": {
                "vendor_name": "Contoso Corp",
                "total": 540.0,
                "currency": "USD",
                "invoice_date": "2026-08-15",
                "line_items": [],
            },
        },
    }

    response = client.get(f"/invoices/status/{task_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["task_id"] == task_id
    assert data["status"] == "done"
    assert data["result"]["fields"]["vendor_name"] == "Contoso Corp"
    assert data["result"]["fields"]["total"] == 540.0


# ---------------------------------------------------------------------------
# 5. USER HISTORY ENDPOINT
# ---------------------------------------------------------------------------

@patch("app.routers.invoices.list_user_invoices", new_callable=AsyncMock)
def test_get_history(mock_list_db, client):
    """GET /invoices/history returns paginated invoice records for the current user."""
    item1 = AsyncMock()
    item1.task_id = uuid.uuid4()
    item1.filename = "invoice_august.pdf"
    item1.file_hash = "abc123hash"
    item1.file_size_bytes = 45000
    item1.content_type = "application/pdf"
    item1.status = "done"
    item1.vendor_name = "GitHub Inc"
    item1.invoice_total = 49.00
    item1.currency = "USD"
    item1.confidence = 0.99
    item1.error_message = None

    mock_list_db.return_value = [item1]

    response = client.get("/invoices/history?limit=10&offset=0")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["filename"] == "invoice_august.pdf"
    assert data[0]["vendor_name"] == "GitHub Inc"
    assert data[0]["invoice_total"] == 49.00
