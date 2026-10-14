from pydantic import BaseModel
from typing import Optional


class LineItem(BaseModel):
    description: Optional[str] = None
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    amount: Optional[float] = None


class InvoiceFields(BaseModel):
    """Fields extracted from the invoice by Azure Document Intelligence."""
    vendor_name: Optional[str] = None
    vendor_address: Optional[str] = None
    customer_name: Optional[str] = None
    invoice_id: Optional[str] = None
    invoice_date: Optional[str] = None
    due_date: Optional[str] = None
    subtotal: Optional[float] = None
    tax: Optional[float] = None
    total: Optional[float] = None
    currency: Optional[str] = None
    line_items: list[LineItem] = []


class ExtractionResponse(BaseModel):
    """Response returned to the client."""
    filename: str
    status: str          # "success" | "partial" | "failed"
    confidence: float    # average confidence across extracted fields
    fields: InvoiceFields
    raw_page_count: int


# ---------------------------------------------------------------------------
# Async task models (used by the background processing endpoints)
# ---------------------------------------------------------------------------

class TaskAccepted(BaseModel):
    """
    Returned immediately when a file is submitted for processing.
    The client uses task_id to poll GET /invoices/status/{task_id}.

    HTTP 202 Accepted = "we got your request, processing has started,
    come back and check the result." This is the correct status for async jobs.
    """
    task_id: str
    status: str = "processing"
    message: str
    cache_hit: bool = False   # True if the result came from Redis, not Azure


class TaskStatusResponse(BaseModel):
    """
    Returned when the client polls for task status.
    result is populated only when status == "done".
    error is populated only when status == "failed".
    """
    task_id: str
    status: str                              # "processing" | "done" | "failed"
    result: Optional[ExtractionResponse] = None
    error: Optional[str] = None


class InvoiceHistoryItem(BaseModel):
    """Returned in user invoice history list."""
    task_id: str
    filename: str
    file_hash: str
    file_size_bytes: int
    content_type: str
    status: str
    vendor_name: Optional[str] = None
    invoice_total: Optional[float] = None
    currency: Optional[str] = None
    confidence: Optional[float] = None
    error_message: Optional[str] = None

    class Config:
        from_attributes = True
