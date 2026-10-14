"""
Azure Document Intelligence — Invoice Extractor Service
========================================================
Azure Document Intelligence (formerly called "Form Recognizer") is an Azure AI
service that reads documents and extracts structured data from them.

Instead of us writing OCR + field-parsing logic, Azure has already trained a
model on millions of real invoices. We just send it an image/PDF and it returns
a JSON with fields like vendor name, total amount, line items, etc.

HOW IT WORKS (high level):
  1. We create a client that connects to our Azure resource
  2. We send the file bytes to Azure ("begin_analyze_document")
  3. Azure processes it asynchronously on their side — the SDK polls for us
  4. We get back a result object containing all detected fields
  5. We map those fields to our own Pydantic models
"""

import io
from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AzureKeyCredential
from app.models.invoice import InvoiceFields, LineItem, ExtractionResponse


# ---------------------------------------------------------------------------
# CLIENT SETUP
# ---------------------------------------------------------------------------

def _get_client(endpoint: str, key: str) -> DocumentIntelligenceClient:
    """
    Creates the Azure SDK client.

    - endpoint: URL of your Azure Document Intelligence resource
      e.g. https://my-resource.cognitiveservices.azure.com/

    - AzureKeyCredential: just wraps your API key string into an object the
      SDK expects. Alternative auth is Managed Identity (no keys at all),
      which we'll use when deployed to Azure App Service.

    - DocumentIntelligenceClient: the main SDK object. It knows the Azure
      REST API endpoints, handles retries, and serializes/deserializes JSON
      for us so we don't write raw HTTP calls.
    """
    return DocumentIntelligenceClient(
        endpoint=endpoint,
        credential=AzureKeyCredential(key),
    )


# ---------------------------------------------------------------------------
# HELPER: Safe float extraction
# ---------------------------------------------------------------------------

def _safe_float(value) -> float | None:
    """
    Azure returns monetary values as special objects, not plain floats.

    For example, invoice total might come back as:
        CurrencyValue(amount=1250.00, currencySymbol='$')

    So we can't just do float(field.value) — we need to grab .amount first.
    This helper handles both cases (object with .amount, or plain number).
    """
    if value is None:
        return None
    if hasattr(value, "amount"):      # e.g. CurrencyValue object
        return float(value.amount)
    try:
        return float(value)           # plain number field
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# MAIN EXTRACTION FUNCTION
# ---------------------------------------------------------------------------

def extract_invoice_fields(
    file_bytes: bytes,
    content_type: str,   # e.g. "image/jpeg" or "application/pdf"
    filename: str,
    endpoint: str,
    key: str,
) -> ExtractionResponse:
    """
    Sends the invoice file to Azure and returns structured fields.

    WHAT IS "prebuilt-invoice"?
    ---------------------------
    Azure Document Intelligence has several models:
      - prebuilt-invoice   → understands invoices specifically (what we use)
      - prebuilt-receipt   → understands receipts
      - prebuilt-idDocument → understands passports/IDs
      - Custom models      → you train on your own document types

    "prebuilt" means Azure already trained it — we get the benefit of that
    training without doing any ML work ourselves.

    WHAT DOES begin_analyze_document() DO?
    ---------------------------------------
    It sends the file bytes to Azure's API and returns a "poller".
    A poller is an object that keeps checking "is Azure done yet?"
    Calling poller.result() blocks until Azure finishes and returns the data.

    Under the hood Azure is:
      1. Running OCR (reads every character on the page)
      2. Understanding layout (which text is a table, which is a header)
      3. Matching layout patterns to known invoice field names
      4. Assigning a confidence score (0.0–1.0) to each extracted field
    """
    client = _get_client(endpoint, key)

    # Send file to Azure. This is a synchronous call that waits for the result.
    # (In Step 2 we'll move this into a background task so the API responds
    # immediately and the client polls for the result later.)
    # SDK v1.0.0 (GA) change: the old `analyze_request=` keyword was renamed to `body=`.
    # `body` accepts either an AnalyzeDocumentRequest object (for URL/base64 input)
    # or an IO[bytes] stream (for raw file bytes, which is our case).
    # io.BytesIO wraps the bytes in a file-like object that the SDK can stream.
    # content_type tells Azure the format of the bytes (JPEG / PDF / etc.)
    poller = client.begin_analyze_document(
        model_id="prebuilt-invoice",
        body=io.BytesIO(file_bytes),
        content_type=content_type,
    )
    result = poller.result()  # blocks here until Azure is done

    # result.documents = list of documents Azure found in the file
    # Usually 1, but a multi-page PDF could have multiple invoices
    if not result.documents:
        # Azure processed the file but couldn't identify any invoice structure
        return ExtractionResponse(
            filename=filename,
            status="failed",
            confidence=0.0,
            fields=InvoiceFields(),
            raw_page_count=len(result.pages) if result.pages else 0,
        )

    # Take the first detected document (first invoice found)
    doc = result.documents[0]

    # doc.fields is a dict where:
    #   key   = Azure's field name, e.g. "VendorName", "InvoiceTotal"
    #   value = DocumentField object with .content (raw text) and .confidence
    f = doc.fields or {}

    # ---------------------------------------------------------------------------
    # FIELD EXTRACTION HELPERS
    # ---------------------------------------------------------------------------

    def get_str(field_name: str) -> str | None:
        """
        Gets the raw text content of a field.
        .content is the literal text Azure read from the document,
        e.g. field "InvoiceDate" → content = "2024-03-15"
        """
        field = f.get(field_name)
        return field.content if field else None

    def get_float(field_name: str) -> float | None:
        """
        Gets numeric/currency fields.
        Azure stores them in .value_currency (for money) or .value_number (plain).
        We try currency first since invoice amounts are almost always monetary.
        """
        field = f.get(field_name)
        if not field:
            return None
        return _safe_float(field.value_currency or field.value_number)

    # ---------------------------------------------------------------------------
    # LINE ITEMS (the table inside the invoice)
    # ---------------------------------------------------------------------------

    # Invoices have a table of items like:
    #   | Description        | Qty | Unit Price | Amount  |
    #   | Cloud hosting      |  1  |  $500.00   | $500.00 |
    #   | Support contract   |  2  |  $375.00   | $750.00 |
    #
    # Azure extracts this table into "Items" → an array of row objects.
    # Each row object has fields: Description, Quantity, UnitPrice, Amount.

    line_items: list[LineItem] = []
    items_field = f.get("Items")

    if items_field and items_field.value_array:
        for item in items_field.value_array:
            # Each item is a DocumentField with type "object" (a row in the table)
            # .value_object gives us a dict of that row's column fields
            item_fields = item.value_object or {}

            line_items.append(LineItem(
                description=item_fields.get("Description").content
                            if item_fields.get("Description") else None,

                # .value_number = plain numeric value (quantity is a number, not money)
                quantity=_safe_float(
                    getattr(item_fields.get("Quantity"), "value_number", None)
                ),

                # .value_currency = monetary value object with .amount
                unit_price=_safe_float(
                    getattr(item_fields.get("UnitPrice"), "value_currency", None)
                ),
                amount=_safe_float(
                    getattr(item_fields.get("Amount"), "value_currency", None)
                ),
            ))

    # ---------------------------------------------------------------------------
    # ASSEMBLE FINAL RESULT
    # ---------------------------------------------------------------------------

    fields = InvoiceFields(
        vendor_name=get_str("VendorName"),        # company that issued the invoice
        vendor_address=get_str("VendorAddress"),
        customer_name=get_str("CustomerName"),    # who it was billed to
        invoice_id=get_str("InvoiceId"),          # invoice number / reference
        invoice_date=get_str("InvoiceDate"),
        due_date=get_str("DueDate"),
        subtotal=get_float("SubTotal"),           # before tax
        tax=get_float("TotalTax"),
        total=get_float("InvoiceTotal"),          # grand total
        currency=get_str("CurrencyCode"),         # e.g. "USD", "GBP"
        line_items=line_items,
    )

    # Confidence score: average of all per-field confidence scores (0.0 – 1.0)
    # Azure gives each field its own confidence — e.g. vendor name might be 0.98
    # but a smudged date might only be 0.62. We average them for an overall score.
    confidences = [
        field.confidence for field in f.values()
        if field and field.confidence is not None
    ]
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

    return ExtractionResponse(
        filename=filename,
        status="success" if avg_confidence > 0.5 else "partial",
        confidence=round(avg_confidence, 3),
        fields=fields,
        raw_page_count=len(result.pages) if result.pages else 0,
    )
