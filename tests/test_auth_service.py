"""
test_auth_service.py — Unit Tests for Microsoft Entra ID Authentication
=======================================================================
MOTIVE OF THIS TEST FILE:
-------------------------
In production, every request sends a Microsoft Entra ID Bearer token (JWT).
The function `get_current_user` in `app/services/auth.py` is responsible for:
  1. Fetching Entra's public signing keys (JWKS) over HTTPS.
  2. Cryptographically validating the token's RS256 signature.
  3. Verifying the token hasn't expired (`exp` claim).
  4. Verifying the token belongs to OUR Azure tenant (`tid` claim) — blocking
     tokens issued by other companies' Azure directories!
  5. Parsing claims (`oid`, `preferred_username`, `roles`, `scp`) into `CurrentUser`.

WHY WE MOCK IN THIS FILE:
-------------------------
If we didn't mock:
  - Tests would fail without an active internet connection.
  - Tests would require real Microsoft accounts and live client secrets.
  - Test tokens would expire after 1 hour, breaking CI/CD forever.

WHAT WE MOCK HERE:
------------------
  1. `_get_jwks_client`: Replaced so no HTTPS calls go to login.microsoftonline.com.
  2. `jwt.decode`: Replaced so we can feed in exact fake JWT payload dictionaries
     (valid, expired, or malicious foreign tenant) to test how our app responds.
"""

import pytest
import jwt
from unittest.mock import patch, MagicMock
from fastapi import HTTPException
from app.models.auth import CurrentUser
from app.services.auth import get_current_user
from app.config import Settings


# ---------------------------------------------------------------------------
# 1. MODEL INTEGRITY TEST
# ---------------------------------------------------------------------------

def test_current_user_model():
    """
    MOTIVE: Verify the CurrentUser Pydantic model correctly stores user identity.
    -----------------------------------------------------------------------------
    In our system:
      - `object_id` (Entra OID) is the permanent foreign key in PostgreSQL.
      - `upn` is the user's email/username used for logging and notifications.
      - `roles` and `scopes` are lists for authorization.
    """
    user = CurrentUser(
        object_id="00000000-0000-0000-0000-000000000001",
        upn="admin@company.com",
        name="Admin User",
        roles=["Invoice.Admin", "Invoice.Contributor"],
        scopes=["invoices.extract", "openid"],
    )

    assert user.object_id == "00000000-0000-0000-0000-000000000001"
    assert user.upn == "admin@company.com"
    assert user.name == "Admin User"
    assert "Invoice.Admin" in user.roles
    assert "invoices.extract" in user.scopes


# ---------------------------------------------------------------------------
# 2. VALID TOKEN FLOW
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch("app.services.auth._get_jwks_client")
@patch("jwt.decode")
async def test_get_current_user_valid_token(mock_jwt_decode, mock_get_jwks):
    """
    MOTIVE: Verify the happy path — when a user presents a valid Entra token,
            `get_current_user` extracts their claims and returns a CurrentUser.
    -----------------------------------------------------------------------------
    WHAT IS GOING ON:
      1. We mock `_get_jwks_client` with a dummy object so it doesn't call Azure.
      2. We tell `mock_jwt_decode.return_value = {...}`: "when `jwt.decode` is called,
         pretend the cryptographic check passed and return these decoded claims".
      3. We call `await get_current_user(...)` and assert the claims mapped correctly.
    """
    # Fake the JWKS key client (no network call)
    mock_jwks = MagicMock()
    mock_get_jwks.return_value = mock_jwks

    tenant_id = "ae16116c-272b-4b41-bcde-faae7cbd75fd"
    settings = Settings(
        azure_tenant_id=tenant_id,
        azure_client_id="8d9daf82-b864-43de-b499-db1d8c3991d6",
        app_env="test",
    )

    # Fake what Entra's decoded token payload looks like
    mock_jwt_decode.return_value = {
        "oid": "user-guid-1234",
        "preferred_username": "john@company.com",
        "name": "John Doe",
        "iss": f"https://login.microsoftonline.com/{tenant_id}/v2.0",
        "tid": tenant_id,
        "scp": "invoices.extract openid",
        "roles": ["Invoice.Reader"],
    }

    user = await get_current_user(token="fake.dummy.jwt.token", settings=settings)

    assert user.object_id == "user-guid-1234"
    assert user.upn == "john@company.com"
    assert user.name == "John Doe"
    assert user.roles == ["Invoice.Reader"]
    assert "invoices.extract" in user.scopes


# ---------------------------------------------------------------------------
# 3. EXPIRED TOKEN FLOW (Security Rule)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch("app.services.auth._get_jwks_client")
@patch("jwt.decode")
async def test_get_current_user_expired_token(mock_jwt_decode, mock_get_jwks):
    """
    MOTIVE: Verify expired tokens are rejected with HTTP 401 Unauthorized.
    -----------------------------------------------------------------------------
    WHAT IS GOING ON:
      In real life, PyJWT raises `jwt.ExpiredSignatureError` if current_time > exp.
      We simulate this using `mock_jwt_decode.side_effect = ExpiredSignatureError(...)`.
      We verify that `get_current_user` catches it and cleanly converts it into
      FastAPI's `HTTPException(401, detail='Token has expired.')`.
    """
    mock_get_jwks.return_value = MagicMock()
    # side_effect tells the mock: "instead of returning a value, RAISE this exception"
    mock_jwt_decode.side_effect = jwt.ExpiredSignatureError("Signature has expired")

    settings = Settings(azure_tenant_id="test-tenant", app_env="test")

    # -------------------------------------------------------------------------
    # WHY WRAP WITH `pytest.raises` INSTEAD OF CALLING DIRECTLY?
    # -------------------------------------------------------------------------
    # If you called `await get_current_user(...)` directly without wrapping:
    #   -> Python would raise HTTPException, crash the function, and FAIL the test!
    #
    # `pytest.raises(HTTPException)` acts like an intelligent try/catch assertion:
    #   1. It tells pytest: "We EXPECT this code to throw an HTTPException".
    #   2. If it DOES raise HTTPException: Pytest catches it and the test keeps going.
    #   3. If it DOES NOT raise (e.g. a bug in our auth allowed an expired token):
    #      Pytest marks the test as FAILED ("Expected HTTPException but nothing was raised!").
    #
    # `as exc_info`:
    #   Captures the thrown exception object into `exc_info.value` so we can inspect it:
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(token="expired.token", settings=settings)

    # Verify our code returned 401 Unauthorized (not 500 server crash or 404):
    assert exc_info.value.status_code == 401
    # Verify the error message informs the user that their token has expired:
    assert "expired" in exc_info.value.detail.lower()


# ---------------------------------------------------------------------------
# 4. CROSS-TENANT ATTACK PREVENTION (Security Rule)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch("app.services.auth._get_jwks_client")
@patch("jwt.decode")
async def test_get_current_user_mismatched_tenant(mock_jwt_decode, mock_get_jwks):
    """
    MOTIVE: Prevent cross-tenant attacks (Tenant Spoofing).
    -----------------------------------------------------------------------------
    WHAT IS GOING ON:
      Microsoft Entra ID is multi-tenant. Anyone with a Microsoft work account in
      ANOTHER company could get a validly signed Microsoft token.
      If our API only checked the signature, users from other companies could log in!
      Our code checks: `token_tid == settings.azure_tenant_id`.
      Here we simulate a token where `tid` belongs to an unauthorized company.
      We verify the code rejects it with HTTP 401.
    """
    mock_get_jwks.return_value = MagicMock()
    settings = Settings(azure_tenant_id="our-authorized-tenant-id", app_env="test")

    # A token signed by Microsoft, but issued to a DIFFERENT tenant directory
    mock_jwt_decode.return_value = {
        "oid": "user-guid-1234",
        "iss": f"https://login.microsoftonline.com/our-authorized-tenant-id/v2.0",
        "tid": "malicious-foreign-tenant-id",  # ❌ Mismatched tenant ID
    }

    # Expect the function to reject the foreign tenant with an HTTPException:
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(token="foreign.token", settings=settings)

    # Assert that the status code is indeed 401 Unauthorized:
    assert exc_info.value.status_code == 401
