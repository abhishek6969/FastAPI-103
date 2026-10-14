resource "azurerm_service_plan" "main" {
  name                = "asp-${local.name_prefix}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  os_type             = "Linux"
  sku_name            = "F1"

  tags = {
    project     = var.project_name
    environment = var.environment
    managed_by  = "terraform"
  }
}

resource "azurerm_linux_web_app" "api" {
  name                = "app-${local.name_prefix}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  service_plan_id     = azurerm_service_plan.main.id
  https_only          = true

  site_config {
    application_stack {
      docker_image_name        = var.docker_image
      docker_registry_url      = "https://index.docker.io"
      docker_registry_username = var.docker_registry_username
      docker_registry_password = var.docker_registry_password
    }
    always_on           = false
    minimum_tls_version = "1.2"
  }

  app_settings = {
    "WEBSITES_PORT"                       = "8000"
    "WEBSITES_ENABLE_APP_SERVICE_STORAGE" = "false"

    # Wired directly from the Document Intelligence resource — no manual input needed
    "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT" = azurerm_cognitive_account.document_intelligence.endpoint
    "AZURE_DOCUMENT_INTELLIGENCE_KEY"      = azurerm_cognitive_account.document_intelligence.primary_access_key

    # Wired directly from Managed Redis resource
    "REDIS_URL" = "rediss://:${azurerm_managed_redis.main.default_database[0].primary_access_key}@${azurerm_managed_redis.main.hostname}:${azurerm_managed_redis.main.default_database[0].port}"

    # Wired directly from PostgreSQL resource
    "DATABASE_URL" = "postgresql+asyncpg://${var.postgres_admin_user}:${var.postgres_admin_password}@${azurerm_postgresql_flexible_server.main.fqdn}:5432/${var.postgres_db_name}?ssl=require"

    # Microsoft Entra ID
    "AZURE_TENANT_ID" = var.azure_tenant_id
    "AZURE_CLIENT_ID" = var.azure_client_id
    # AZURE_CLIENT_SECRET is only used for local Swagger UI OAuth2 flow.
    # In production, Entra token validation only needs tenant_id + client_id.

    "APP_ENV" = var.environment
  }

  tags = {
    project     = var.project_name
    environment = var.environment
    managed_by  = "terraform"
  }
}
