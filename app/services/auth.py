"""
Microsoft Entra ID — Token Validation Service
==============================================

THE OAUTH 2.0 / OIDC FLOW (what happens before our code runs):
---------------------------------------------------------------
1. User opens a client app (Swagger UI, frontend, Postman)
2. Client redirects user to Entra's login page:
      https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize
3. User enters their Microsoft credentials
4. Entra authenticates the user and redirects back with an authorization code
5. Client exchanges the code for tokens by calling:
      https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token
6. Entra returns an ACCESS TOKEN (JWT) — this is what our API validates

OUR JOB (what this file does):
-------------------------------
We never see the user's password. We only receive the access token and verify:
  a) Signature    — was this token genuinely signed by Entra? (using public keys)
  b) Issuer       — does 'iss' claim match our tenant?
  c) Audience     — does 'aud' claim match our API's App ID URI?
  d) Expiry       — is 'exp' in the future?
  e) Tenant       — does 'tid' match our tenant (prevents cross-tenant tokens)?

WHY PUBLIC KEY CRYPTOGRAPHY (RS256)?
--------------------------------------
Entra signs tokens with its PRIVATE key (RSA). It publishes the matching
PUBLIC key at the JWKS endpoint. We use the public key to verify the signature
without needing Entra's private key. This means:
  - We can validate tokens OFFLINE after fetching the public keys once
  - We never need to call Entra's API on every request (just cache the keys)
  - If someone forges a token, the signature check fails

PyJWKClient handles fetching AND caching the public keys automatically.
"""

import jwt
import logging
from jwt import PyJWKClient, PyJWKClientError
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2AuthorizationCodeBearer

from app.config import get_settings, Settings
from app.models.auth import CurrentUser

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OAUTH2 SCHEME — tells FastAPI (and Swagger UI) how auth works
# ---------------------------------------------------------------------------

# WHY MODULE-LEVEL?
# FastAPI walks the dependency tree at startup to build the OpenAPI spec
# (which powers the Swagger UI padlock and Authorize button).
# It can only detect a security scheme if it's a concrete object it can
# inspect — a scheme hidden inside a function or lambda is invisible to it.
# get_settings() is an lru_cache singleton, so calling it here is safe.
_settings = get_settings()

oauth2_scheme = OAuth2AuthorizationCodeBearer(
    authorizationUrl=(
        f"https://login.microsoftonline.com/{_settings.azure_tenant_id}"
        f"/oauth2/v2.0/authorize"
    ),
    tokenUrl=(
        f"https://login.microsoftonline.com/{_settings.azure_tenant_id}"
        f"/oauth2/v2.0/token"
    ),
    scopes={
        f"api://{_settings.azure_client_id}/invoices.extract": "Extract invoices",
        "openid": "OpenID Connect",
        "profile": "User profile",
    },
    # OAuth2AuthorizationCodeBearer does two things:
    #   1. Tells Swagger UI which Entra URLs to use for the login popup
    #   2. Extracts the Bearer token from Authorization header on each request
)


# ---------------------------------------------------------------------------
# JWKS CLIENT — fetches and caches Entra's public signing keys
# ---------------------------------------------------------------------------

# Module-level so the client (and its key cache) is shared across all requests.
# PyJWKClient automatically re-fetches keys when a new key ID (kid) is seen —
# Entra rotates keys periodically, so this handles rotation transparently.
_jwks_client: PyJWKClient | None = None


def _get_jwks_client(settings: Settings) -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(
            settings.entra_jwks_uri,
            cache_keys=True,        # cache fetched keys in memory
            lifespan=3600,          # re-fetch keys after 1 hour
        )
    return _jwks_client


# ---------------------------------------------------------------------------
# get_current_user — FastAPI dependency for protected routes
# ---------------------------------------------------------------------------

async def get_current_user(
    settings: Settings = Depends(get_settings),
    token: str = Depends(oauth2_scheme),
    # oauth2_scheme extracts the raw JWT string from the
    # "Authorization: Bearer <token>" header.
    # If the header is missing entirely → FastAPI returns 401 automatically.
) -> CurrentUser:
    """
    FastAPI dependency — inject this into any route to require authentication.

    Usage:
        @router.get("/protected")
        async def my_route(user: CurrentUser = Depends(get_current_user)):
            return {"hello": user.upn}

    WHAT THIS DOES:
      1. OAuth2AuthorizationCodeBearer extracts the raw JWT string from the
         "Authorization: Bearer <token>" header. If missing → 401 automatically.
      2. PyJWKClient finds the correct public key by matching the token's 'kid'
         (key ID) header against Entra's published keys.
      3. jwt.decode() verifies signature, issuer, audience, and expiry atomically.
         Any failure raises an exception — we catch it and return 401.
      4. We build a CurrentUser from the validated claims and return it.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        # Step 1: Find the public key that signed this specific token.
        # The token header contains 'kid' (key ID). PyJWKClient finds the
        # matching key from Entra's JWKS endpoint.
        jwks_client = _get_jwks_client(settings)
        signing_key = jwks_client.get_signing_key_from_jwt(token)

        # Step 2: Decode and validate the token.
        # jwt.decode() raises exceptions for: invalid signature, expired token,
        # wrong issuer, wrong audience — all treated as 401.
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],        # Entra always uses RS256
            audience=settings.entra_audience,
            # issuer not passed here — PyJWT only accepts a single string.
            # We validate manually below to support both v1 and v2 issuers.
            options={"require": ["exp", "aud", "oid"]},
        )

        # Step 3: Validate issuer — must be one of our known Entra issuer URLs.
        # v1 tokens use sts.windows.net; v2 tokens use login.microsoftonline.com.
        # Both are valid signatures from the same Entra tenant.
        token_iss = payload.get("iss", "")
        if token_iss not in settings.entra_valid_issuers:
            logger.warning("[AUTH] Unexpected issuer: %s", token_iss)
            raise credentials_exception

        # Step 4: Validate tenant — prevent tokens from other Entra tenants
        # being accepted even if signature is valid (important in multi-tenant setups)
        token_tid = payload.get("tid")
        if token_tid != settings.azure_tenant_id:
            raise credentials_exception


        # Step 5: Extract claims into our CurrentUser model
        #
        # UPN CLAIM FALLBACK CHAIN:
        # - preferred_username → v2.0 tokens, work/school accounts
        # - upn               → v1.0 tokens, work/school accounts
        # - email             → personal Microsoft accounts (outlook.com) as guests
        # - unique_name       → personal accounts fallback (has "live.com#" prefix — strip it)
        unique_name_raw = payload.get("unique_name", "")
        unique_name_clean = unique_name_raw.replace("live.com#", "") if unique_name_raw else ""

        upn = (
            payload.get("preferred_username")
            or payload.get("upn")
            or payload.get("email")
            or unique_name_clean
            or ""
        )


        # scp claim is a space-separated string: "invoices.extract openid profile"
        scopes_str = payload.get("scp", "")
        scopes = scopes_str.split() if scopes_str else []

        # roles is already a list in the token (or absent if not configured)
        roles = payload.get("roles", [])

        return CurrentUser(
            object_id=payload["oid"],
            upn=upn,
            name=payload.get("name"),
            tenant_id=token_tid,
            scopes=scopes,
            roles=roles,
        )

    except PyJWKClientError as e:
        logger.error("[AUTH] JWKS fetch failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not fetch token signing keys from Entra. Try again shortly.",
        )
    except jwt.ExpiredSignatureError:
        logger.warning("[AUTH] Token expired")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError as e:
        # Covers: bad signature, wrong issuer, wrong audience, malformed token
        logger.warning("[AUTH] Token validation failed: %s", e)
        raise credentials_exception
