resource "azurerm_managed_redis" "main" {
  name                = "redis-${local.name_prefix}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku_name            = "Balanced_B0"

  default_database {
    access_keys_authentication_enabled = true
  }

  tags = {
    project     = var.project_name
    environment = var.environment
    managed_by  = "terraform"
  }
}