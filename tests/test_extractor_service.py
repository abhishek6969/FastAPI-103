"""
test_extractor_service.py — Unit Tests for Document Intelligence Data Helpers
=============================================================================
MOTIVE OF THIS TEST FILE:
-------------------------
In production, Azure Document Intelligence reads invoices and returns raw field objects.
The helper functions and Pydantic models in `app/services/extractor.py` and
`app/models/invoice.py` are responsible for parsing those raw Azure objects into clean,
predictable Python datatypes.

WHY THESE HELPERS EXIST:
------------------------
1. Azure Document Intelligence returns monetary values as nested SDK objects:
       CurrencyValue(amount=199.99, currencySymbol="$", currencyCode="USD")
   It does NOT return plain Python floats!
   If our code naively did `float(field.value)`, it would crash with a TypeError.

2. The helper `_safe_float(value)` must defensively handle:
   - Nested objects with an `.amount` property (CurrencyValue)
   - Plain integers and floats (100, 45.67)
   - String numbers ("123.45")
   - Empty/missing fields (None)
   - Unexpected garbage input (empty lists, non-numeric strings)

These tests ensure our extraction parser never crashes in production when encountering
unusual invoice formatting or unexpected Azure payload variations.
"""

from types import SimpleNamespace
from app.services.extractor import _safe_float
from app.models.invoice import InvoiceFields, LineItem, ExtractionResponse


# ---------------------------------------------------------------------------
# 1. DEFENSIVE FLOAT CONVERSION TESTS
# ---------------------------------------------------------------------------

def test_safe_float_none():
    """MOTIVE: When an invoice field is missing (None), safe_float must return None without error."""
    assert _safe_float(None) is None


def test_safe_float_plain_numbers():
    """MOTIVE: Standard numbers (int, float, string numeric) must convert to float."""
    assert _safe_float(100) == 100.0
    assert _safe_float(45.67) == 45.67
    assert _safe_float("123.45") == 123.45


def test_safe_float_currency_object():
    """
    MOTIVE: Azure Document Intelligence returns monetary amounts wrapped in a CurrencyValue object.
    We simulate this using a SimpleNamespace with an `.amount` attribute.
    _safe_float must extract `.amount` and convert it to float.
    """
    currency_val = SimpleNamespace(amount=199.99, currencySymbol="$")
    assert _safe_float(currency_val) == 199.99


def test_safe_float_invalid_types():
    """MOTIVE: Malformed, non-numeric, or unparsable types must return None instead of raising exceptions."""
    assert _safe_float("not-a-number") is None
    assert _safe_float([]) is None
    assert _safe_float({}) is None


# ---------------------------------------------------------------------------
# 2. PYDANTIC RESPONSE SCHEMA VALIDATION
# ---------------------------------------------------------------------------

def test_extraction_response_model():
    """
    MOTIVE: Validate that the full ExtractionResponse Pydantic model correctly
            serializes line items, nested fields, and confidence scores into JSON/dict.
    """
    fields = InvoiceFields(
        vendor_name="Acme Corp",
        customer_name="John Doe",
        invoice_id="INV-001",
        invoice_date="2026-01-01",
        total=150.00,
        currency="USD",
        line_items=[
            LineItem(description="Widget A", quantity=2, unit_price=50.0, amount=100.0),
            LineItem(description="Widget B", quantity=1, unit_price=50.0, amount=50.0),
        ],
    )
    response = ExtractionResponse(
        filename="invoice.pdf",
        status="success",
        confidence=0.98,
        fields=fields,
        raw_page_count=1,
    )
    data = response.model_dump()
    assert data["fields"]["vendor_name"] == "Acme Corp"
    assert data["fields"]["total"] == 150.00
    assert len(data["fields"]["line_items"]) == 2
    assert data["confidence"] == 0.98
