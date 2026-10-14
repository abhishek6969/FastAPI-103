"""
Invoice Router — Async Extract with Redis Caching
==================================================
Flow:
  POST /invoices/extract
    1. Validate file type
    2. Read bytes + SHA-256 hash
    3. Check Redis cache → if hit, return task immediately (no Azure call)
    4. If miss → generate task_id, store "processing" in Redis
    5. Register background task (returns HTTP 202 immediately)
    6. Background task calls Azure, stores result in Redis when done

  GET /invoices/status/{task_id}
    → Read task state from Redis, return result when done
"""

import hashlib
import uuid
import asyncio
from fastapi import APIRouter, UploadFile, File, HTTPException, BackgroundTasks, Depends
from fastapi.responses import JSONResponse

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import ExtractionResponse, TaskAccepted, TaskStatusResponse, InvoiceHistoryItem
from app.models.auth import CurrentUser
from app.database import get_db
from app.services.extractor import extract_invoice_fields
from app.services.cache import get_cached_result, store_cached_result, set_task_status, get_task_status
from app.services.auth import get_current_user
from app.services.db_invoice import (
    create_invoice_record,
    update_invoice_record_completed,
    update_invoice_record_failed,
    get_invoice_record,
    list_user_invoices,
)
from app.config import get_settings, Settings

router = APIRouter(prefix="/invoices", tags=["invoices"])

ALLOWED_TYPES = {
    "image/jpeg",
    "image/png",
    "image/tiff",
    "application/pdf",
}


# ---------------------------------------------------------------------------
# BACKGROUND TASK FUNCTION
# ---------------------------------------------------------------------------

async def _run_extraction(
    task_id: str,
    file_hash: str,
    file_bytes: bytes,
    content_type: str,
    filename: str,
    settings: Settings,
) -> None:
    """
    This function runs AFTER the HTTP response has already been sent.

    WHY asyncio.to_thread()?
    The Azure SDK's extract_invoice_fields() is a regular blocking function.
    asyncio.to_thread() runs the blocking function in a separate thread pool thread,
    keeping the async event loop completely free.
    """
    task_uuid = uuid.UUID(task_id)
    try:
        # Run the blocking Azure SDK call in a thread — keeps the event loop free
        result: ExtractionResponse = await asyncio.to_thread(
            extract_invoice_fields,
            file_bytes=file_bytes,
            content_type=content_type,
            filename=filename,
            endpoint=settings.azure_document_intelligence_endpoint,
            key=settings.azure_document_intelligence_key,
        )

        result_dict = result.model_dump()

        # Store result under BOTH Redis keys:
        # 1. task:{id}         → so the polling client can fetch it
        # 2. invoice:{hash}    → so the same file never hits Azure again (24h cache)
        await set_task_status(task_id, status="done", result=result_dict)
        await store_cached_result(file_hash, result_dict)

        # Update PostgreSQL persistent database record
        vendor_name = result.fields.vendor_name if result.fields else None
        invoice_total = result.fields.total if result.fields else None
        currency = result.fields.currency if result.fields else None
        confidence = result.confidence

        await update_invoice_record_completed(
            task_id=task_uuid,
            result=result_dict,
            vendor_name=vendor_name,
            invoice_total=invoice_total,
            currency=currency,
            confidence=confidence,
        )

    except Exception as e:
        # If Azure fails, mark task as failed with the error message in both Redis and PostgreSQL
        await set_task_status(task_id, status="failed", error=str(e))
        await update_invoice_record_failed(task_id=task_uuid, error_message=str(e))


# ---------------------------------------------------------------------------
# POST /invoices/extract — submit invoice for processing
# ---------------------------------------------------------------------------

@router.post(
    "/extract",
    response_model=TaskAccepted,
    status_code=202,
    summary="Submit an invoice for async extraction",
)
async def extract_invoice(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="Invoice image (JPEG/PNG/TIFF) or PDF"),
    settings: Settings = Depends(get_settings),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """

        HOW FASTAPI BACKGROUNDTASKS WORKS:
    FastAPI's BackgroundTasks are not a separate process or worker — they run
    inside the same uvicorn process, in the same event loop, after the response
    is fully sent. The sequence is:
      1. Route handler runs, calls background_tasks.add_task(...)
      2. FastAPI sends the HTTP 202 response to the client
      3. THIS function runs — client already has their task_id
      4. Function finishes — result is in Redis
    LIMITATION: If the uvicorn process crashes mid-task, the task is lost.
    For critical production workloads you'd use a proper task queue (Celery +
    Redis/RabbitMQ as broker) where tasks are persisted and survive restarts.
    For this use case (3–8 second Azure call, non-critical if lost) FastAPI
    BackgroundTasks is the right level of complexity.
    Submit an invoice image or PDF for field extraction.

    Returns immediately with a task_id (HTTP 202).
    Poll GET /invoices/status/{task_id} for the result.
    If the same file was processed before, result comes from Redis cache instantly.
    All jobs are persisted to PostgreSQL associated with current_user.object_id.
    """
    # --- File type validation ---
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '{file.content_type}'. Allowed: {sorted(ALLOWED_TYPES)}",
        )

    contents = await file.read()
    if len(contents) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    # --- Azure credentials check ---
    if not settings.azure_document_intelligence_endpoint or not settings.azure_document_intelligence_key:
        raise HTTPException(
            status_code=503,
            detail="Azure Document Intelligence is not configured. Set AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT and KEY in .env",
        )

    # --- SHA-256 hash of file contents ---
    # SHA-256 produces a unique 64-char hex string for the file's content.
    # Same bytes = same hash, every time. This is our cache key.
    # We hash the raw bytes (not filename) because the same invoice could be
    # uploaded with different filenames — the content is what matters.
    file_hash = hashlib.sha256(contents).hexdigest()
    filename = file.filename or "unknown"
    task_uuid = uuid.uuid4()
    task_id = str(task_uuid)

    # --- Check Redis cache first ---
    cached = await get_cached_result(file_hash)
    if cached:
        # CACHE HIT: this exact file was processed before — return immediately
        await set_task_status(task_id, status="done", result=cached)
        
        # Persist cache-hit record in PostgreSQL
        fields = cached.get("fields", {})
        await create_invoice_record(
            db=db,
            task_id=task_uuid,
            user=current_user,
            filename=filename,
            file_hash=file_hash,
            file_size_bytes=len(contents),
            content_type=file.content_type,
            status="done",
            result=cached,
            vendor_name=fields.get("vendor_name"),
            invoice_total=fields.get("total"),
            currency=fields.get("currency"),
            confidence=cached.get("confidence"),
        )

        return TaskAccepted(
            task_id=task_id,
            status="done",
            message=f"Result retrieved from cache for invoice submitted by {current_user.upn or current_user.name}.",
            cache_hit=True,
        )

    # --- CACHE MISS: start background processing ---
    # Store initial state in Redis
    await set_task_status(task_id, status="processing")

    # Persist initial record in PostgreSQL
    await create_invoice_record(
        db=db,
        task_id=task_uuid,
        user=current_user,
        filename=filename,
        file_hash=file_hash,
        file_size_bytes=len(contents),
        content_type=file.content_type,
        status="processing",
    )

    # Register the background task.
    # FastAPI sends the HTTP 202 response FIRST, then runs this function.
    background_tasks.add_task(
        _run_extraction,
        task_id=task_id,
        file_hash=file_hash,
        file_bytes=contents,
        content_type=file.content_type,
        filename=filename,
        settings=settings,
    )

    return TaskAccepted(
        task_id=task_id,
        status="processing",
        message=f"Invoice submitted by {current_user.upn or current_user.name}. Poll /invoices/status/{{task_id}} for the result.",
        cache_hit=False,
    )


# ---------------------------------------------------------------------------
# GET /invoices/status/{task_id} — poll for result
# ---------------------------------------------------------------------------

@router.get(
    "/status/{task_id}",
    response_model=TaskStatusResponse,
    summary="Poll for extraction result",
)
async def get_status(
    task_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Poll this endpoint after submitting an invoice.

    Security: Enforces user ownership — only the user who submitted the task
    (or an admin) can view its status.
    """
    try:
        task_uuid = uuid.UUID(task_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid task_id format. Must be a valid UUID.")

    # 1. Enforce user ownership via PostgreSQL
    record = await get_invoice_record(db, task_uuid)
    if record and record.user_id != current_user.object_id:
        raise HTTPException(
            status_code=403,
            detail="Forbidden: You do not have permission to view this invoice task.",
        )

    # 2. Check Redis (fastest for active/recent tasks)
    task = await get_task_status(task_id)

    # 3. Fallback to PostgreSQL if Redis key expired or Redis was restarted
    if task is None:
        if record is None:
            raise HTTPException(
                status_code=404,
                detail=f"Task '{task_id}' not found.",
            )
        result = None
        if record.result:
            result = ExtractionResponse(**record.result)
        return TaskStatusResponse(
            task_id=str(record.task_id),
            status=record.status,
            result=result,
            error=record.error_message,
        )

    result = None
    if task.get("result"):
        result = ExtractionResponse(**task["result"])

    return TaskStatusResponse(
        task_id=task_id,
        status=task["status"],
        result=result,
        error=task.get("error"),
    )


# ---------------------------------------------------------------------------
# GET /invoices/history — list user's invoice extraction history
# ---------------------------------------------------------------------------

@router.get(
    "/history",
    response_model=list[InvoiceHistoryItem],
    summary="List invoice history for authenticated user",
)
async def get_user_history(
    limit: int = 20,
    offset: int = 0,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns paginated invoice extraction history for the currently logged-in user.
    Ordered newest first.
    """
    records = await list_user_invoices(
        db=db,
        user_id=current_user.object_id,
        limit=limit,
        offset=offset,
    )
    return [
        InvoiceHistoryItem(
            task_id=str(r.task_id),
            filename=r.filename,
            file_hash=r.file_hash,
            file_size_bytes=r.file_size_bytes,
            content_type=r.content_type,
            status=r.status,
            vendor_name=r.vendor_name,
            invoice_total=float(r.invoice_total) if r.invoice_total is not None else None,
            currency=r.currency,
            confidence=r.confidence,
            error_message=r.error_message,
        )
        for r in records
    ]
