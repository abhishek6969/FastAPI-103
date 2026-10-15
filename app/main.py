from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import invoices, preferences
from app.config import get_settings

settings = get_settings()

app = FastAPI(
    title="Invoice Extractor API",
    description="Upload invoice images or PDFs to extract structured fields using Azure Document Intelligence.",
    version="2.0.0",

    # SWAGGER UI OAUTH2 CONFIG
    # This pre-fills the Swagger UI "Authorize" dialog so you can log in with
    # your Microsoft account directly from the docs page.
    #
    # swagger_ui_init_oauth tells Swagger UI which client ID to use when
    # initiating the OAuth2 Authorization Code flow. The user clicks
    # "Authorize" → redirected to Microsoft login → comes back with a token →
    # Swagger UI sends that token as "Authorization: Bearer <token>" on every
    # API call you make from the docs.
    swagger_ui_init_oauth={
        "clientId": settings.azure_client_id,
        "scopes": f"api://{settings.azure_client_id}/invoices.extract openid profile",
        "usePkceWithAuthorizationCodeGrant": True,
        # PKCE (Proof Key for Code Exchange) — a security enhancement for
        # public clients (like browser-based Swagger UI) that prevents
        # authorization code interception attacks. Use it instead of a
        # client_secret for browser flows.
    },
)

# CORS — open for local dev; tighten origins in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(invoices.router)
app.include_router(preferences.router)


@app.get("/health", tags=["health"])
def health_check():
    return {"status": "ok"}
