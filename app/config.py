from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    azure_document_intelligence_endpoint: str = ""
    azure_document_intelligence_key: str = ""
    redis_url: str = ""
    database_url: str = "postgresql+asyncpg://postgres:postgres@db:5432/invoice_db"
    app_env: str = "development"

    # Microsoft Entra ID (Azure AD) — used for token validation
    azure_tenant_id: str = ""
    azure_client_id: str = ""
    azure_client_secret: str = ""  # only needed for Swagger UI OAuth2 flow
    running_local:bool=False
    @property
    def entra_issuer(self) -> str:
        """v2.0 issuer — the preferred/correct format after setting accessTokenAcceptedVersion: 2"""
        return f"https://login.microsoftonline.com/{self.azure_tenant_id}/v2.0"

    @property
    def entra_valid_issuers(self) -> list[str]:
        """
        Both v1 and v2 issuers are accepted.

        WHY TWO ISSUERS?
        By default, App Registrations issue v1.0 tokens (sts.windows.net issuer).
        Setting accessTokenAcceptedVersion=2 in the manifest switches to v2.0
        (login.microsoftonline.com/.../v2.0 issuer). Accepting both during
        transition avoids a hard cutover and is a common real-world pattern.
        """
        return [
            f"https://login.microsoftonline.com/{self.azure_tenant_id}/v2.0",  # v2 token
            f"https://sts.windows.net/{self.azure_tenant_id}/",               # v1 token
        ]

    @property
    def entra_jwks_uri(self) -> str:
        """Entra's public key endpoint — used to verify JWT signatures."""
        return f"https://login.microsoftonline.com/{self.azure_tenant_id}/discovery/v2.0/keys"

    @property
    def entra_audience(self) -> list[str]:
        """
        Valid audiences for our API's access tokens.
        Entra v2.0 sets 'aud' to the Application ID URI we defined: api://{client_id}
        We also accept the bare client_id GUID as a fallback (v1.0 tokens / some flows).
        """
        return [f"api://{self.azure_client_id}", self.azure_client_id]

    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    # lru_cache means Settings is only created once — acts as a singleton
    return Settings()
