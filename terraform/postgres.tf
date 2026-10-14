resource "azurerm_postgresql_flexible_server" "main" {
  name                   = "azpsql-${local.name_prefix}"
  location               = azurerm_resource_group.main.location
  resource_group_name    = azurerm_resource_group.main.name

  administrator_login    = var.postgres_admin_user
  administrator_password = var.postgres_admin_password

  sku_name               = "B_Standard_B1ms"
  storage_mb             = 32768
  version                = "16"

  backup_retention_days        = 7
  geo_redundant_backup_enabled = false
  public_network_access_enabled = true

  tags = {
    project     = var.project_name
    environment = var.environment
    managed_by  = "terraform"
  }
  lifecycle {
    ignore_changes = [zone]
  }
}

resource "azurerm_postgresql_flexible_server_database" "app_db" {
  name      = var.postgres_db_name
  server_id = azurerm_postgresql_flexible_server.main.id
  collation = "en_US.utf8"
  charset   = "utf8"
}

# Allow inbound connections from Azure services (includes App Service)
resource "azurerm_postgresql_flexible_server_firewall_rule" "allow_all_services" {
  name             = "allow-all-services"
  server_id        = azurerm_postgresql_flexible_server.main.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "255.255.255.255"
}

# Uncomment to allow your local machine to connect (e.g. for running alembic locally)
# resource "azurerm_postgresql_flexible_server_firewall_rule" "allow_local_dev" {
#   name             = "allow-local-dev"
#   server_id        = azurerm_postgresql_flexible_server.main.id
#   start_ip_address = "YOUR.PUBLIC.IP"
#   end_ip_address   = "YOUR.PUBLIC.IP"
# }

resource "azurerm_postgresql_flexible_server_configuration" "require_ssl" {
  name      = "require_secure_transport"
  server_id = azurerm_postgresql_flexible_server.main.id
  value     = "on"
}
