from pydantic import BaseModel
from typing import Optional


class CurrentUser(BaseModel):
    """
    Represents the authenticated user, built from claims in the Entra JWT.

    WHAT ARE JWT CLAIMS?
    A JWT (JSON Web Token) is a base64-encoded JSON payload signed by Entra.
    The payload contains "claims" — key-value pairs about the user and the token.

    Key claims we extract:
      oid  (object_id)        — Immutable unique ID for this user in Entra.
                                 Use this (not UPN) as the stable DB foreign key.
                                 UPN can change if a user's email changes; OID never does.

      upn / preferred_username — User Principal Name, e.g. alice@company.com
                                 Human-readable identifier, good for display/logging.

      name                    — Display name, e.g. "Alice Smith"

      scp  (scopes)           — Space-separated list of delegated scopes granted.
                                 e.g. "invoices.extract openid profile"
                                 Used to verify the token has the right permission.

      roles                   — App roles assigned to this user in Entra.
                                 Empty list for now; we'll define roles like
                                 "Invoice.Reader" and "Invoice.Contributor" in
                                 Step 3b when we add role-based access control.

      tid  (tenant_id)        — Tenant the user belongs to. Useful for multi-tenant
                                 scenarios; we validate this matches our tenant.
    """
    object_id: str                    # oid — stable, use as DB key
    upn: str                          # preferred_username / upn
    name: Optional[str] = None        # display name
    tenant_id: Optional[str] = None   # tid
    scopes: list[str] = []            # scp split into a list
    roles: list[str] = []             # roles — empty until we define app roles in Entra
