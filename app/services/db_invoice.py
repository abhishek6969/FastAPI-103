import uuid
import logging
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.db_invoice import InvoiceRecord
from app.models.auth import CurrentUser

logger = logging.getLogger("uvicorn")


async def create_invoice_record(
    db: AsyncSession,
    task_id: uuid.UUID,
    user: CurrentUser,
    filename: str,
    file_hash: str,
    file_size_bytes: int,
    content_type: str,
    status: str = "processing",
    result: dict | None = None,
    vendor_name: str | None = None,
    invoice_total: float | None = None,
    currency: str | None = None,
    confidence: float | None = None,
) -> InvoiceRecord:
    """Create a new invoice record in PostgreSQL upon file upload."""
    record = InvoiceRecord(
        task_id=task_id,
        user_id=user.object_id,
        user_upn=user.upn or user.name,
        filename=filename,
        file_hash=file_hash,
        file_size_bytes=file_size_bytes,
        content_type=content_type,
        status=status,
        result=result,
        vendor_name=vendor_name,
        invoice_total=invoice_total,
        currency=currency,
        confidence=confidence,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    logger.info("[DB] Created invoice record task_id=%s for user=%s", task_id, user.upn)
    return record


async def update_invoice_record_completed(
    task_id: uuid.UUID,
    result: dict,
    vendor_name: str | None,
    invoice_total: float | None,
    currency: str | None,
    confidence: float | None,
) -> None:
    """
    Called by background task to update record status to 'done'.
    Uses a fresh AsyncSessionLocal so it doesn't depend on the request lifecycle.
    """
    async with AsyncSessionLocal() as session:
        query = select(InvoiceRecord).where(InvoiceRecord.task_id == task_id)
        res = await session.execute(query)
        record = res.scalar_one_or_none()
        if record:
            record.status = "done"
            record.result = result
            record.vendor_name = vendor_name
            record.invoice_total = invoice_total
            record.currency = currency
            record.confidence = confidence
            await session.commit()
            logger.info("[DB] Updated invoice record task_id=%s to 'done'", task_id)


async def update_invoice_record_failed(task_id: uuid.UUID, error_message: str) -> None:
    """
    Called by background task to update record status to 'failed'.
    Uses a fresh AsyncSessionLocal.
    """
    async with AsyncSessionLocal() as session:
        query = select(InvoiceRecord).where(InvoiceRecord.task_id == task_id)
        res = await session.execute(query)
        record = res.scalar_one_or_none()
        if record:
            record.status = "failed"
            record.error_message = error_message
            await session.commit()
            logger.warning("[DB] Updated invoice record task_id=%s to 'failed': %s", task_id, error_message)


async def get_invoice_record(db: AsyncSession, task_id: uuid.UUID) -> InvoiceRecord | None:
    """Fetch an invoice record by task_id."""
    query = select(InvoiceRecord).where(InvoiceRecord.task_id == task_id)
    res = await db.execute(query)
    return res.scalar_one_or_none()


async def list_user_invoices(
    db: AsyncSession,
    user_id: str,
    limit: int = 20,
    offset: int = 0,
) -> list[InvoiceRecord]:
    """Retrieve all invoices submitted by a specific user, ordered newest first."""
    query = (
        select(InvoiceRecord)
        .where(InvoiceRecord.user_id == user_id)
        .order_by(desc(InvoiceRecord.created_at))
        .limit(limit)
        .offset(offset)
    )
    res = await db.execute(query)
    return list(res.scalars().all())
