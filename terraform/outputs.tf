output "resource_group_name" {
  value = azurerm_resource_group.main.name
}

output "app_service_url" {
  value = "https://${azurerm_linux_web_app.api.default_hostname}"
}

output "app_service_swagger_url" {
  value = "https://${azurerm_linux_web_app.api.default_hostname}/docs"
}

output "document_intelligence_endpoint" {
  value = azurerm_cognitive_account.document_intelligence.endpoint
}

output "document_intelligence_key" {
  value     = azurerm_cognitive_account.document_intelligence.primary_access_key
  sensitive = true
}

output "redis_hostname" {
  value = azurerm_managed_redis.main.hostname
}

output "redis_connection_string" {
  value     = "rediss://:${azurerm_managed_redis.main.default_database[0].primary_access_key}@${azurerm_managed_redis.main.hostname}:${azurerm_managed_redis.main.default_database[0].port}"
  sensitive = true
}

output "postgres_server_fqdn" {
  value = azurerm_postgresql_flexible_server.main.fqdn
}

output "postgres_database_url" {
  value     = "postgresql+asyncpg://${var.postgres_admin_user}:${var.postgres_admin_password}@${azurerm_postgresql_flexible_server.main.fqdn}:5432/${var.postgres_db_name}?ssl=require"
  sensitive = true
}
